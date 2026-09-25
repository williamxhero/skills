from __future__ import annotations

import pytest

from harness.windows_detached_parent_probe import validate_report


def _report() -> dict[str, object]:
    return {
        "evidence_kind": "observed_windows_native_detached_parent",
        "platform": "Windows",
        "wrapper_pid": 10,
        "child_pid": 11,
        "wrapper_exit_code": 0,
        "parent_exited_before_continue": True,
        "ready_after_wrapper_exit": True,
        "final_state": "completed",
    }


def test_windows_detached_parent_report_requires_child_survival() -> None:
    validate_report(_report())
    invalid = _report()
    invalid["final_state"] = "running"
    with pytest.raises(AssertionError, match="survive wrapper exit"):
        validate_report(invalid)