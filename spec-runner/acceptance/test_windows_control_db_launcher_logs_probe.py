from __future__ import annotations

import pytest

from harness.windows_control_db_launcher_logs_probe import validate_report


def _report() -> dict[str, object]:
    return {
        "evidence_kind": "observed_windows_control_db_launcher_logs",
        "platform": "Windows",
        "control_lock_ready": True,
        "log_handles_ready": True,
        "lock_attempt_error": "control_database_busy",
        "control_db_did_not_advance": True,
        "runner_terminated_while_both_locks_held": True,
        "recovered_same_run_with_logs_held": True,
        "rotation_error_with_logs_held": "launcher_log_rotation_failed",
        "rotation_after_log_release": True,
        "rotation_replayed": True,
        "control_db_exists_after": True,
        "control_db_integrity_after": "ok",
        "final_state": "completed",
    }


def test_combined_windows_lock_report_requires_both_lock_boundaries() -> None:
    validate_report(_report())
    invalid = _report()
    invalid["control_db_did_not_advance"] = False
    with pytest.raises(AssertionError, match="control database must not advance"):
        validate_report(invalid)


def test_combined_windows_lock_report_requires_log_release_replay() -> None:
    invalid = _report()
    invalid["rotation_replayed"] = False
    with pytest.raises(AssertionError, match="rotation intent"):
        validate_report(invalid)
