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
from pathlib import Path
from typing import Any, Mapping, Protocol

from .config import RunnerConfig
from .errors import RunnerError
from .models import RunnerRequest, StageResult
from .run_coordinator import RunCoordinator
from .store import Store
from .input_gate import pending_question
from .request_context import context_path, read_context


class LifecyclePort(Protocol):
    """Operations needed by the lifecycle coordinator."""

    def start(self, request: RunnerRequest) -> Mapping[str, object]: ...

    def drive(self, request: RunnerRequest) -> Mapping[str, object]: ...

    def resume(self, request: RunnerRequest) -> Mapping[str, object]: ...

    def answer(
        self,
        *,
        control_root: Path,
        run_id: str,
        question_id: str,
        value: Any,
        brief_file: Path | None = None,
        config_file: Path | None = None,
    ) -> Mapping[str, object]: ...

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

        def start_operation(
            current: RunnerRequest,
            *,
            run_id: str | None,
            launch_key: str,
            control_root: Path,
        ) -> Mapping[str, object]:
            return workflow.start(
                brief_file=current.brief_file,
                config_file=current.config_file,
                control_root=control_root,
                launch_key=launch_key,
                run_id=run_id,
                launch_token=current.launch_token,
            )

        return RunCoordinator(
            start_operation=start_operation,
            validate_launch_key=workflow._validate_launch_key,
            sleep=time.sleep,
        ).drive(request).public()

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

    def answer(
        self,
        *,
        control_root: Path,
        run_id: str,
        question_id: str,
        value: Any,
        brief_file: Path | None = None,
        config_file: Path | None = None,
    ) -> Mapping[str, object]:
        """Accept one business answer and resume the same launch identity."""
        root = control_root.expanduser().resolve()
        store = Store.open(root, create=False)
        waiting_for_input = False
        request_context = None
        launch_key: str | None = None
        try:
            current = store.find_by_run_id(run_id)
            if current and current.state == "needs_input":
                waiting_for_input = True
                question = pending_question(control_root=root, run=current, question_id=question_id)
                if brief_file is None and config_file is None:
                    try:
                        request_context = read_context(
                            context_path(root / current.artifact_root / run_id),
                            run_id=run_id,
                        )
                    except RunnerError as exc:
                        if exc.code != "answer_continuation_missing":
                            raise
                answer = store.submit_answer_and_wake(
                    run_id=run_id,
                    question_id=question_id,
                    value=value,
                    question=question,
                    expected_input_digest=current.input_digest,
                )
            else:
                answer = store.submit_answer(run_id=run_id, question_id=question_id, value=value)
            if current is not None:
                launch_key = current.launch_key
            result: dict[str, object] = {"accepted": True, "answer": answer, **store.public_status(run_id)}
        finally:
            store.close()
        if waiting_for_input and request_context is not None:
            result["continuation"] = self.resume(RunnerRequest(
                brief_file=request_context.brief_file,
                config_file=request_context.config_file,
                control_root=root,
                launch_key=request_context.launch_key,
            ))
        elif brief_file is not None or config_file is not None:
            if brief_file is None or config_file is None:
                raise RunnerError("answer_inputs_incomplete", "answer continuation requires both --brief and --config")
            if launch_key is None:
                launch_key = str(result["run"]["launch_key"])
            result["continuation"] = self.resume(RunnerRequest(
                brief_file=brief_file,
                config_file=config_file,
                control_root=root,
                launch_key=launch_key,
            ))
        return result

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

    def answer(
        self,
        *,
        control_root: Path,
        run_id: str,
        question_id: str,
        value: Any,
        brief_file: Path | None = None,
        config_file: Path | None = None,
    ) -> Mapping[str, object]:
        return self._port.answer(
            control_root=control_root,
            run_id=run_id,
            question_id=question_id,
            value=value,
            brief_file=brief_file,
            config_file=config_file,
        )

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
