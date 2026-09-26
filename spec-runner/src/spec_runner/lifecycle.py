"""Lifecycle seam for the durable Runner.

The public CLI still speaks the historical dictionary contract.  This module
keeps that compatibility conversion at one seam while the Runner works with
typed requests and stage results.  The current implementation adapter points
at ``workflow`` during the migration; callers and tests no longer need to
know that private module's function graph.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Protocol

from .config import RunnerConfig
from .errors import RunnerError
from .models import RunnerRequest, StageResult
from .recovery import RecoveryAction
from .recovery_runtime import RecoveryRuntime
from .store import Store


class LifecyclePort(Protocol):
    """Operations needed by the lifecycle coordinator."""

    def start(self, request: RunnerRequest) -> Mapping[str, object]: ...

    def drive(self, request: RunnerRequest) -> Mapping[str, object]: ...

    def resume(self, request: RunnerRequest) -> Mapping[str, object]: ...

    def control(self, *, control_root: Path, run_id: str, requested_state: str) -> Mapping[str, object]: ...

    def launch(self, *, request: RunnerRequest, handshake_timeout_seconds: float) -> Mapping[str, object]: ...

    def status(self, *, control_root: Path, run_id: str | None) -> Mapping[str, object]: ...

    def doctor(self, *, config_file: Path | None, control_root: Path) -> Mapping[str, object]: ...


class LegacyWorkflowAdapter:
    """Adapt the existing workflow implementation to ``LifecyclePort``.

    Imports stay inside methods so the compatibility adapter does not create a
    module cycle and so tests can replace the legacy implementation at the
    existing seam while it is being migrated.
    """

    def start(self, request: RunnerRequest) -> Mapping[str, object]:
        from . import workflow

        return workflow._start_legacy(
            brief_file=request.brief_file,
            config_file=request.config_file,
            control_root=request.control_root,
            launch_key=request.launch_key,
            run_id=request.run_id,
            takeover_key=request.takeover_key,
            launch_token=request.launch_token,
            migration=request.migration,
        )

    def drive(self, request: RunnerRequest) -> Mapping[str, object]:
        from . import workflow

        launch_key = workflow._validate_launch_key(request.launch_key)
        control_root = request.control_root.expanduser().resolve()
        active_run_id = request.run_id
        woken_deadlines: set[tuple[str, str]] = set()
        while True:
            result = workflow.start(
                brief_file=request.brief_file,
                config_file=request.config_file,
                control_root=control_root,
                launch_key=launch_key,
                run_id=active_run_id,
                launch_token=request.launch_token,
            )
            run_payload = result.get("run")
            if not isinstance(run_payload, dict) or not isinstance(run_payload.get("run_id"), str):
                raise RunnerError("run_status_missing", "Runner start returned no durable run identity")
            active_run_id = str(run_payload["run_id"])

            while True:
                store = Store.open(control_root, create=False)
                try:
                    current = store.find_by_run_id(active_run_id)
                    if current is None:
                        raise RunnerError("unknown_run", "recovery wait run disappeared from the control database")
                    wait_record = RecoveryRuntime.wait_record(store=store, run_id=active_run_id)
                    if wait_record is None:
                        if current.state in {RecoveryAction.WAIT_RETRY.value, RecoveryAction.SERVICE_WAIT.value}:
                            raise RunnerError("recovery_record_missing", "waiting run has no persisted recovery decision")
                        return {**result, **store.public_status(active_run_id)}
                    action, deadline = wait_record
                    control = store.control_for_run(active_run_id)
                    if control and control.get("requested_state") in {"pause_requested", "cancel_requested"}:
                        requested = str(control["requested_state"])
                        stopped_state = "paused" if requested == "pause_requested" else "cancelled"
                        store.set_run_state(active_run_id, stopped_state)
                        store.append_event(
                            run_id=active_run_id,
                            event_key=f"control:{active_run_id}:{control['generation']}:applied",
                            event_type="control_applied",
                            payload={"requested_state": requested, "generation": control["generation"], "during": "recovery_wait"},
                        )
                        return {**result, **store.public_status(active_run_id)}
                    wake_key = (action, deadline)
                    remaining = (datetime.fromisoformat(deadline) - datetime.now(timezone.utc)).total_seconds()
                    if remaining <= 0:
                        if wake_key in woken_deadlines:
                            return {**result, **store.public_status(active_run_id)}
                        woken_deadlines.add(wake_key)
                        store.append_event(
                            run_id=active_run_id,
                            event_key=f"recovery:{active_run_id}:timer-woke:{action}:{deadline}",
                            event_type="recovery_timer_woke",
                            payload={"action": action, "deadline": deadline},
                        )
                        break
                    wait_seconds = min(remaining, 0.25)
                finally:
                    store.close()
                time.sleep(wait_seconds)

    def resume(self, request: RunnerRequest) -> Mapping[str, object]:
        from . import workflow

        control_root = request.control_root.expanduser().resolve()
        store = Store.open(control_root, create=False)
        try:
            existing = store.find_by_launch_key(request.launch_key)
            if existing is None:
                raise RunnerError("unknown_run", "resume requires an existing launch_key")
            if existing.state == "cancelled":
                raise RunnerError("cancelled_run", "cancelled runs require explicit creation of a new launch identity")
            store.clear_control(existing.run_id)
        finally:
            store.close()
        return workflow.drive(
            brief_file=request.brief_file,
            config_file=request.config_file,
            control_root=control_root,
            launch_key=request.launch_key,
        )

    def control(self, *, control_root: Path, run_id: str, requested_state: str) -> Mapping[str, object]:
        from . import workflow

        return workflow._control_legacy(
            control_root=control_root,
            run_id=run_id,
            requested_state=requested_state,
        )

    def launch(self, *, request: RunnerRequest, handshake_timeout_seconds: float) -> Mapping[str, object]:
        from . import workflow

        return workflow._launch_legacy(
            brief_file=request.brief_file,
            config_file=request.config_file,
            control_root=request.control_root,
            launch_key=request.launch_key,
            handshake_timeout_seconds=handshake_timeout_seconds,
        )

    def status(self, *, control_root: Path, run_id: str | None) -> Mapping[str, object]:
        from . import workflow

        store = Store.open(control_root.expanduser().resolve(), create=False)
        try:
            if run_id:
                return store.public_status(run_id)
            return {"runs": store.list_status()}
        finally:
            store.close()

    def doctor(self, *, config_file: Path | None, control_root: Path) -> Mapping[str, object]:
        root = control_root.expanduser().resolve()
        report: dict[str, object] = {
            "read_only": True,
            "control_database_exists": (root / "spec-runner.sqlite3").is_file(),
            "supported_backends": ["deterministic_test", "codex_sdk"],
        }
        if config_file:
            config = RunnerConfig.from_file(config_file, root)
            report["config"] = {
                "valid": True,
                "repository_path": os.fspath(config.repository_path),
                "execution_backend": config.execution_backend,
                "requested_model": config.model_name,
                "requested_effort": config.effort,
            }
        return report


class LifecycleCoordinator:
    """Coordinate lifecycle calls and convert legacy projections once."""

    def __init__(self, port: LifecyclePort | None = None) -> None:
        self._port = port or LegacyWorkflowAdapter()

    def start(self, request: RunnerRequest) -> StageResult:
        return StageResult.from_public(self._port.start(request))

    def drive(self, request: RunnerRequest) -> StageResult:
        return StageResult.from_public(self._port.drive(request))

    def resume(self, request: RunnerRequest) -> StageResult:
        return StageResult.from_public(self._port.resume(request))

    def control(self, *, control_root: Path, run_id: str, requested_state: str) -> Mapping[str, object]:
        return self._port.control(
            control_root=control_root,
            run_id=run_id,
            requested_state=requested_state,
        )

    def launch(self, *, request: RunnerRequest, handshake_timeout_seconds: float) -> Mapping[str, object]:
        return self._port.launch(
            request=request,
            handshake_timeout_seconds=handshake_timeout_seconds,
        )

    def status(self, *, control_root: Path, run_id: str | None) -> Mapping[str, object]:
        return self._port.status(control_root=control_root, run_id=run_id)

    def doctor(self, *, config_file: Path | None, control_root: Path) -> Mapping[str, object]:
        return self._port.doctor(config_file=config_file, control_root=control_root)
