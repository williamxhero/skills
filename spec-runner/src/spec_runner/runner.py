"""Small orchestration seam for the durable Spec Runner lifecycle.

The implementation remains in ``workflow`` during the staged refactor.  This
module owns the interface callers use, which lets recovery and production
implementations move behind it without changing the CLI contract.
"""

from __future__ import annotations

from pathlib import Path

from .models import RunnerRequest, StageResult


class Runner:
    """Coordinate one durable lifecycle while preserving legacy projections."""

    @staticmethod
    def _stage_result(result: dict[str, object]) -> StageResult:
        """Keep legacy dictionaries at the outer edge of the typed seam."""
        return StageResult.from_public(result)

    def start_stage(self, request: RunnerRequest) -> StageResult:
        from . import workflow

        return self._stage_result(workflow._start_legacy(
            brief_file=request.brief_file,
            config_file=request.config_file,
            control_root=request.control_root,
            launch_key=request.launch_key,
            run_id=request.run_id,
            takeover_key=request.takeover_key,
            launch_token=request.launch_token,
            migration=request.migration,
        ))

    def start(self, request: RunnerRequest) -> dict[str, object]:
        return self.start_stage(request).public()

    def drive(self, request: RunnerRequest) -> dict[str, object]:
        from . import workflow

        result = workflow._drive_legacy(
            brief_file=request.brief_file,
            config_file=request.config_file,
            control_root=request.control_root,
            launch_key=request.launch_key,
            run_id=request.run_id,
            launch_token=request.launch_token,
        )
        return StageResult.from_public(result).public()

    def resume(self, request: RunnerRequest) -> dict[str, object]:
        from . import workflow

        result = workflow._resume_legacy(
            brief_file=request.brief_file,
            config_file=request.config_file,
            control_root=request.control_root,
            launch_key=request.launch_key,
        )
        return StageResult.from_public(result).public()

    def control(self, *, control_root: Path, run_id: str, requested_state: str) -> dict[str, object]:
        from . import workflow

        return workflow._control_legacy(
            control_root=control_root,
            run_id=run_id,
            requested_state=requested_state,
        )

    def launch(self, *, request: RunnerRequest, handshake_timeout_seconds: float = 10.0) -> dict[str, object]:
        from . import workflow

        return workflow._launch_legacy(
            brief_file=request.brief_file,
            config_file=request.config_file,
            control_root=request.control_root,
            launch_key=request.launch_key,
            handshake_timeout_seconds=handshake_timeout_seconds,
        )

    def status(self, *, control_root: Path, run_id: str | None) -> dict[str, object]:
        from . import workflow

        return workflow._status_legacy(control_root=control_root, run_id=run_id)

    def doctor(self, *, config_file: Path | None, control_root: Path) -> dict[str, object]:
        from . import workflow

        return workflow._doctor_legacy(config_file=config_file, control_root=control_root)
