from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness.hybrid_fault_probe import run_probe


def test_hybrid_probe_uses_formal_adapter_and_preserves_live_gaps() -> None:
    report = run_probe()
    assert report["passed"] is True
    assert report["evidence_kind"] == "deterministic_fixture"
    assert report["live_provider_incident"] is False
    assert {case["family"] for case in report["cases"]} == {
        "encrypted_item_mismatch",
        "capacity",
        "stream_disconnected",
        "route_not_found",
        "authorization",
    }
    assert all(case["thread_id"] == "thread-hybrid" for case in report["cases"])
    assert all(case["turn_id"] == "turn-hybrid" for case in report["cases"])
    assert "hybrid real SDK fault injection" in report["unverified"]
