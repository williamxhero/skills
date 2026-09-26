from __future__ import annotations

import pytest

from harness.windows_cleanup_replay_probe import validate_report


def _report() -> dict[str, object]:
    return {
        "evidence_kind": "observed_windows_production_cleanup_replay",
        "platform": "Windows",
        "lock_holder_ready": True,
        "workspace_retained_while_locked": True,
        "manifest_retained_while_locked": True,
        "first_public_start_state": "cleanup_pending",
        "same_run_replayed": True,
        "final_state": "completed",
        "workspace_removed": True,
        "manifest_removed": True,
        "main_head_unchanged": True,
        "merge_receipt_unchanged": True,
        "worker_count_unchanged": True,
        "operation_count_unchanged": True,
        "step_count_unchanged": True,
        "delivery_cleanup_outcome": "cleaned",
    }


def test_windows_cleanup_replay_report_requires_no_new_worker_or_merge() -> None:
    validate_report(_report())
    invalid = _report()
    invalid["worker_count_unchanged"] = False
    with pytest.raises(AssertionError, match="another worker, operation or step"):
        validate_report(invalid)


def test_windows_cleanup_replay_report_requires_same_merged_commit() -> None:
    invalid = _report()
    invalid["main_head_unchanged"] = False
    with pytest.raises(AssertionError, match="already merged commit"):
        validate_report(invalid)
