from __future__ import annotations

import pytest

from harness.windows_launcher_log_handles_probe import validate_report


def _report() -> dict[str, object]:
    return {
        "evidence_kind": "observed_windows_launcher_log_handles",
        "platform": "Windows",
        "wrapper_exit_code": 0,
        "child_pid": 2,
        "wrapper_pid": 1,
        "holder_ready": True,
        "rotation_blocked_while_held": True,
        "runner_terminated_while_held": True,
        "recovered_same_run_while_log_handles_held": True,
        "public_rotation_error": "launcher_log_rotation_failed",
        "rotation_after_release": True,
        "rotated_logs_readable": True,
        "public_rotation_replayed": True,
        "recovered_same_run": True,
        "final_state": "completed",
    }


def test_launcher_log_report_requires_independent_handle_block() -> None:
    validate_report(_report())
    invalid = _report()
    invalid["rotation_blocked_while_held"] = False
    with pytest.raises(AssertionError, match="block their rotation"):
        validate_report(invalid)


def test_launcher_log_report_requires_same_run_recovery() -> None:
    invalid = _report()
    invalid["recovered_same_run"] = False
    with pytest.raises(AssertionError, match="same run"):
        validate_report(invalid)
