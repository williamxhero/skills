"""Small orchestration seam for the durable Spec Runner lifecycle.

The implementation remains in ``workflow`` during the staged refactor.  This
module owns the interface callers use, which lets recovery and production
implementations move behind it without changing the CLI contract.
"""

from __future__ import annotations

from pathlib import Path

from .lifecycle import LifecycleCoordinator, LifecyclePort
from .models import RunnerRequest, StageResult


class Runner:
    """Coordinate one durable lifecycle while preserving legacy projections."""

    def __init__(self, port: LifecyclePort | None = None) -> None:
        self._lifecycle = LifecycleCoordinator(port)

    def start_stage(self, request: RunnerRequest) -> StageResult:
        return self._lifecycle.start(request)

    def start(self, request: RunnerRequest) -> dict[str, object]:
        return self.start_stage(request).public()

    def drive(self, request: RunnerRequest) -> dict[str, object]:
        return self._lifecycle.drive(request).public()

    def resume(self, request: RunnerRequest) -> dict[str, object]:
        return self._lifecycle.resume(request).public()

    def control(self, *, control_root: Path, run_id: str, requested_state: str) -> dict[str, object]:
        return dict(self._lifecycle.control(
            control_root=control_root,
            run_id=run_id,
            requested_state=requested_state,
        ))

    def launch(self, *, request: RunnerRequest, handshake_timeout_seconds: float = 10.0) -> dict[str, object]:
        return dict(self._lifecycle.launch(
            request=request,
            handshake_timeout_seconds=handshake_timeout_seconds,
        ))

    def status(self, *, control_root: Path, run_id: str | None) -> dict[str, object]:
        return dict(self._lifecycle.status(control_root=control_root, run_id=run_id))

    def doctor(self, *, config_file: Path | None, control_root: Path) -> dict[str, object]:
        return dict(self._lifecycle.doctor(config_file=config_file, control_root=control_root))
