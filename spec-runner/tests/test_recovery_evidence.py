from __future__ import annotations

from pathlib import Path

import pytest

from spec_runner.errors import RunnerError
from spec_runner.recovery_evidence import latest_worker, read_turn_evidence


class Adapter:
    def __init__(self, inspection):
        self.inspection = inspection

    def read_thread(self, *, thread_id: str, repository_path: Path):
        return self.inspection


def _inspection(status: str = "completed") -> dict[str, object]:
    return {
        "thread_id": "thread-1",
        "thread_status": "idle",
        "active_flags": [],
        "started_turn": False,
        "turn_count": 1,
        "turns": [{"turn_id": "turn-1", "status": status}],
    }


def test_read_turn_evidence_requires_exact_idle_thread_and_turn() -> None:
    evidence = read_turn_evidence(
        adapter=Adapter(_inspection()), thread_id="thread-1", turn_id="turn-1",
        repository_path=Path("."), error_message="cannot inspect",
    )

    assert evidence.terminal is True
    assert evidence.has_status("completed")
    assert evidence.status == "completed"


def test_read_turn_evidence_marks_mismatched_identity_nonterminal() -> None:
    evidence = read_turn_evidence(
        adapter=Adapter(_inspection()), thread_id="thread-1", turn_id="other",
        repository_path=Path("."), error_message="cannot inspect",
    )

    assert evidence.terminal is False
    assert not evidence.has_status("completed")


def test_read_turn_evidence_maps_adapter_failure_to_recovery_block() -> None:
    class FailingAdapter:
        def read_thread(self, **kwargs):
            raise RunnerError("sdk_read_failed", "unavailable")

    with pytest.raises(RunnerError) as raised:
        read_turn_evidence(
            adapter=FailingAdapter(), thread_id="thread-1", turn_id="turn-1",
            repository_path=Path("."), error_message="cannot inspect",
        )

    assert raised.value.code == "recovery_blocked"
    assert raised.value.details["inspection_error"] == "sdk_read_failed"


def test_latest_worker_supports_exact_and_prefix_stage_identity() -> None:
    workers = [
        {"worker_id": "codex_sdk:run:codex_ticket_planning:S1", "backend_kind": "codex_sdk", "state": "failed"},
        {"worker_id": "codex_sdk:run:codex_implementation:S1", "backend_kind": "codex_sdk", "state": "running"},
    ]

    assert latest_worker(
        workers=workers, backend_kind="codex_sdk", states={"failed"},
        prefix="codex_sdk:run:codex_ticket_planning:",
    )["worker_id"].endswith("S1")
    assert latest_worker(
        workers=workers, backend_kind="codex_sdk", states={"running"},
        exact_id="codex_sdk:run:codex_implementation:S1",
    )["state"] == "running"
