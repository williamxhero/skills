from __future__ import annotations

import pytest

from harness.live_process_restart import validate_report


def _report() -> dict[str, object]:
    return {
        "evidence_kind": "observed_live_sdk_process_restart",
        "process_phases": {
            "initial": {"pid": 10, "thread_id": "thread-1", "turn_id": "turn-1", "status": "completed"},
            "resume": {"pid": 11, "thread_id": "thread-1", "turn_id": "turn-2", "status": "completed"},
        },
        "archive_readback": {"thread_id": "thread-1", "archived": True},
    }


def test_live_process_restart_report_requires_distinct_processes_and_turns() -> None:
    validate_report(_report())

    invalid = _report()
    invalid["process_phases"]["resume"]["pid"] = 10
    with pytest.raises(AssertionError, match="different worker processes"):
        validate_report(invalid)