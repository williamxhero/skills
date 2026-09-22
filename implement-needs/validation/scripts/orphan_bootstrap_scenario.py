"""Deterministic attempt-advance matrix for orphan bootstrap recovery."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from task_binding import may_advance_attempt


CASES = {
    "archived": ("archived", "unknown", True),
    "outcome_only": ("working", "abandoned_after_bootstrap", False),
    "verified_without_archive": ("verified", "completed", False),
    "narrow_tombstone": (
        "tombstoned", "backend_absent_after_create", True,
    ),
    "broad_tombstone": ("tombstoned", "completed", False),
}


def run_orphan_bootstrap_matrix():
    cases = {}
    for name, (lifecycle, outcome, expected) in sorted(CASES.items()):
        actual = may_advance_attempt(lifecycle, outcome)
        cases[name] = {
            "lifecycle": lifecycle,
            "outcome": outcome,
            "expected": expected,
            "actual": actual,
        }
    return {
        "evidence_kind": "local_deterministic_replay",
        "resources_created": 0,
        "cases": cases,
    }
