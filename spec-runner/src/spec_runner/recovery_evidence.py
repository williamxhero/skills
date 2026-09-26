"""Read-only evidence helpers used by process-exit recovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .errors import RunnerError


@dataclass(frozen=True)
class TurnEvidence:
    """A bounded projection of one persisted SDK thread readback."""

    inspection: dict[str, object]
    thread_id: str
    turn_id: str
    status: str | None
    terminal: bool

    def has_status(self, *statuses: str) -> bool:
        return self.terminal and self.status in statuses


def latest_worker(*, workers: Iterable[dict[str, object]], backend_kind: str,
                  states: set[str], exact_id: str | None = None,
                  prefix: str | None = None) -> dict[str, object] | None:
    """Select the newest worker matching one durable stage identity."""
    matches = []
    for worker in workers:
        if worker.get("backend_kind") != backend_kind or worker.get("state") not in states:
            continue
        worker_id = str(worker.get("worker_id") or "")
        if exact_id is not None and worker_id != exact_id:
            continue
        if prefix is not None and not worker_id.startswith(prefix):
            continue
        matches.append(worker)
    return matches[-1] if matches else None


def read_turn_evidence(*, adapter: Any, thread_id: str, turn_id: str,
                       repository_path: Path, error_message: str) -> TurnEvidence:
    """Read and validate one thread without starting, steering, or replaying it."""
    try:
        inspection = adapter.read_thread(thread_id=thread_id, repository_path=repository_path)
    except RunnerError as exc:
        raise RunnerError(
            "recovery_blocked",
            error_message,
            details={"inspection_error": exc.code},
        ) from exc
    if not isinstance(inspection, dict):
        raise RunnerError("recovery_blocked", "the SDK operation has no readable external result")
    turns = inspection.get("turns")
    turn_count = inspection.get("turn_count")
    last = turns[-1] if isinstance(turns, list) and turns else None
    status = last.get("status") if isinstance(last, dict) else None
    terminal = (
        inspection.get("started_turn") is False
        and inspection.get("thread_id") == thread_id
        and inspection.get("thread_status") == "idle"
        and inspection.get("active_flags") == []
        and isinstance(turns, list)
        and isinstance(turn_count, int)
        and not isinstance(turn_count, bool)
        and turn_count == len(turns)
        and bool(turns)
        and isinstance(last, dict)
        and last.get("turn_id") == turn_id
    )
    return TurnEvidence(
        inspection=inspection,
        thread_id=thread_id,
        turn_id=turn_id,
        status=str(status) if isinstance(status, str) else None,
        terminal=terminal,
    )

