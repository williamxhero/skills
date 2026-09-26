"""Coordinate durable run driving behind a small lifecycle interface.

The public workflow accepts a request and returns the historical JSON mapping.
This module owns the stateful part that is easy to get wrong: reopening the
same run, waiting on a persisted recovery deadline, applying a control request
while waiting, and waking exactly once for a deadline.  Stage execution stays
with the injected start operation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Protocol

from .errors import RunnerError
from .models import RunnerRequest, StageResult
from .recovery import RecoveryAction
from .recovery_runtime import RecoveryRuntime
from .store import Store


class StartOperation(Protocol):
    def __call__(self, request: RunnerRequest, *, run_id: str | None) -> Mapping[str, object]: ...


class LaunchKeyValidator(Protocol):
    def __call__(self, value: str) -> str: ...


@dataclass(frozen=True)
class RunCoordinator:
    """Drive one durable run until it reaches a non-waiting state."""

    start_operation: StartOperation
    validate_launch_key: LaunchKeyValidator
    sleep: Callable[[float], None] = time.sleep
    poll_seconds: float = 0.25

    def drive(self, request: RunnerRequest) -> StageResult:
        launch_key = self.validate_launch_key(request.launch_key)
        control_root = request.control_root.expanduser().resolve()
        active_run_id = request.run_id
        woken_deadlines: set[tuple[str, str]] = set()

        while True:
            result = self.start_operation(
                request,
                run_id=active_run_id,
                launch_key=launch_key,
                control_root=control_root,
            )
            active_run_id = self._run_id(result)

            while True:
                wait_seconds, should_return, should_wake = self._inspect_wait(
                    control_root=control_root,
                    run_id=active_run_id,
                    result=result,
                    woken_deadlines=woken_deadlines,
                )
                if should_return:
                    return StageResult.from_public(should_return)
                if should_wake:
                    break
                self.sleep(wait_seconds)

    @staticmethod
    def _run_id(result: Mapping[str, object]) -> str:
        run_payload = result.get("run")
        if not isinstance(run_payload, Mapping) or not isinstance(run_payload.get("run_id"), str):
            raise RunnerError("run_status_missing", "Runner start returned no durable run identity")
        return str(run_payload["run_id"])

    def _inspect_wait(
        self,
        *,
        control_root: Path,
        run_id: str,
        result: Mapping[str, object],
        woken_deadlines: set[tuple[str, str]],
    ) -> tuple[float, Mapping[str, object] | None, bool]:
        store = Store.open(control_root, create=False)
        try:
            current = store.find_by_run_id(run_id)
            if current is None:
                raise RunnerError("unknown_run", "recovery wait run disappeared from the control database")
            wait_record = RecoveryRuntime.wait_record(store=store, run_id=run_id)
            if wait_record is None:
                if current.state in {RecoveryAction.WAIT_RETRY.value, RecoveryAction.SERVICE_WAIT.value}:
                    raise RunnerError("recovery_record_missing", "waiting run has no persisted recovery decision")
                return 0.0, {**result, **store.public_status(run_id)}, False

            action, deadline = wait_record
            control = store.control_for_run(run_id)
            if control and control.get("requested_state") in {"pause_requested", "cancel_requested"}:
                requested = str(control["requested_state"])
                stopped_state = "paused" if requested == "pause_requested" else "cancelled"
                store.set_run_state(run_id, stopped_state)
                store.append_event(
                    run_id=run_id,
                    event_key=f"control:{run_id}:{control['generation']}:applied",
                    event_type="control_applied",
                    payload={
                        "requested_state": requested,
                        "generation": control["generation"],
                        "during": "recovery_wait",
                    },
                )
                return 0.0, {**result, **store.public_status(run_id)}, False

            wake_key = (action, deadline)
            remaining = (
                datetime.fromisoformat(deadline) - datetime.now(timezone.utc)
            ).total_seconds()
            if remaining <= 0:
                if wake_key in woken_deadlines:
                    return 0.0, {**result, **store.public_status(run_id)}, False
                woken_deadlines.add(wake_key)
                store.append_event(
                    run_id=run_id,
                    event_key=f"recovery:{run_id}:timer-woke:{action}:{deadline}",
                    event_type="recovery_timer_woke",
                    payload={"action": action, "deadline": deadline},
                )
                return 0.0, None, True
            return min(remaining, self.poll_seconds), None, False
        finally:
            store.close()
