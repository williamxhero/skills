from __future__ import annotations

import sqlite3

import pytest

from harness.windows_control_db_restart_probe import _integrity, validate_report


def _report() -> dict[str, object]:
    return {
        "evidence_kind": "observed_windows_control_db_restart",
        "platform": "Windows",
        "lock_acquired_before_runner_termination": True,
        "lock_write_attempt_error": "control_database_busy",
        "lock_write_attempt_elapsed_seconds": 5.0,
        "runner_terminated_while_lock_held": True,
        "control_db_exists_after": True,
        "control_db_integrity_after": "ok",
        "recovered_same_run": True,
        "final_state": "completed",
        "launcher_logs_readable": True,
    }


def test_windows_control_db_report_requires_integrity_and_same_run_recovery() -> None:
    validate_report(_report())
    invalid = _report()
    invalid["control_db_integrity_after"] = "database disk image is malformed"
    with pytest.raises(AssertionError, match="intact control DB"):
        validate_report(invalid)

def test_integrity_check_releases_control_database_handle(tmp_path) -> None:
    database = tmp_path / "spec-runner.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE marker (value TEXT)")
        connection.execute("INSERT INTO marker VALUES ('ok')")
        connection.commit()
    finally:
        connection.close()
    assert _integrity(database) == "ok"
    database.unlink()
