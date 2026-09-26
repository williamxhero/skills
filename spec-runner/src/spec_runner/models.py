"""Internal orchestration values shared by the Runner modules.

These values are deliberately private to the package.  CLI and durable
receipt contracts continue to use the existing JSON dictionaries at their
outer edge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .config import RunnerConfig
from .store import RunRecord, Store


@dataclass(frozen=True)
class RunContext:
    """The immutable inputs and durable handles for one Runner attempt."""

    control_root: Path
    config: RunnerConfig
    brief: str
    brief_digest: str
    run: RunRecord
    store: Store

    def public_status(self) -> dict[str, object]:
        """Return the legacy status projection at the orchestration seam."""
        return self.store.public_status(self.run.run_id)


@dataclass(frozen=True)
class StageResult:
    """Typed view of a stage result before it crosses a legacy JSON seam."""

    run_id: str | None
    state: str | None
    payload: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def from_public(cls, payload: Mapping[str, object]) -> "StageResult":
        run = payload.get("run")
        if isinstance(run, Mapping):
            run_id = run.get("run_id")
            state = run.get("state")
            if not isinstance(run_id, str) or not isinstance(state, str):
                raise ValueError("stage result has an invalid run projection")
        else:
            # Mechanical cleanup and other legacy adapters may return a
            # top-level state without the full public status projection.
            run_id = None
            state = payload.get("state") if isinstance(payload.get("state"), str) else None
        return cls(run_id=run_id, state=state, payload=dict(payload))

    def public(self) -> dict[str, object]:
        """Return the byte-for-byte compatible dictionary shape."""
        return dict(self.payload)


@dataclass(frozen=True)
class RunnerRequest:
    """Normalized lifecycle request used by the internal Runner interface."""

    brief_file: Path
    config_file: Path
    control_root: Path
    launch_key: str
    run_id: str | None = None
    takeover_key: str | None = None
    launch_token: str | None = None
    migration: dict[str, Any] | None = None
