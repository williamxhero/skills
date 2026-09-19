"""Read-only benchmark for the compact phase-context transport envelope."""
from __future__ import annotations

import json
import math

from context_projection import read_history
from phase_context import compact_context_from_snapshot


BENCHMARK_NAME = "implement-needs-context-budget-v1"
BENCHMARK_CASES = (
    "empty-run",
    "active-dependency",
    "closed-delivery",
    "recovery-exception",
    "release-context",
)


def _encoded(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return raw, max(1, math.ceil(len(raw) / 4))


def benchmark_context(db, run_id, phase="planning"):
    """Compare envelopes without creating snapshots or advancing state."""
    before_version = db.business_version(run_id)
    stored = db.read_snapshot(run_id)
    if stored is None:
        return {
            "decision": "inconclusive",
            "reason": "snapshot_missing",
            "benchmark": {"name": BENCHMARK_NAME, "cases": list(BENCHMARK_CASES), "fixed": True},
            "coverage": {"tokens": "estimated", "provider_tokens": "unknown", "fees": "unknown"},
        }
    compact = compact_context_from_snapshot(db, run_id, phase, stored)
    legacy = {
        "run_id": run_id,
        "phase": phase,
        "state_version": stored["state_version"],
        "event_cursor": stored["event_cursor"],
        "acceptance": stored["payload"].get("acceptance", []),
        "direct_dependencies": stored["payload"].get("direct_dependencies", []),
        "decisions": stored["payload"].get("decisions", []),
        "worktree": stored["payload"].get("worktree", {}),
        "version": stored["payload"].get("version", {}),
        "evidence": stored["payload"].get("evidence", []),
        "snapshot": stored["payload"],
        "events": db.events_since(run_id, stored["event_cursor"]),
        "unresolved_exceptions": db.unresolved_exceptions(run_id),
    }
    legacy_raw, legacy_tokens = _encoded(legacy)
    compact_raw, compact_tokens = _encoded(compact)
    pointer = compact["snapshot_pointer"]
    history = read_history(db, pointer)
    required_equal = all(compact[key] == legacy[key] for key in ("run_id", "phase", "state_version", "event_cursor", "acceptance", "direct_dependencies", "decisions", "events", "unresolved_exceptions"))
    safety = {
        "required_fields_preserved": required_equal,
        "business_version_unchanged": db.business_version(run_id) == before_version,
        "history_pointer_reachable": history["record"] == stored["payload"],
        "provider_token_data": "unknown",
        "fee_data": "unknown",
    }
    savings = {
        "payload_bytes": legacy_raw.__len__() - compact_raw.__len__(),
        "token_estimate": legacy_tokens - compact_tokens,
        "payload_reduction_ratio": (1 - compact_raw.__len__() / legacy_raw.__len__()) if legacy_raw else 0,
    }
    allowed = all(safety[key] for key in ("required_fields_preserved", "business_version_unchanged", "history_pointer_reachable")) and savings["payload_bytes"] > 0
    return {
        "decision": "allow" if allowed else "inconclusive",
        "benchmark": {"name": BENCHMARK_NAME, "cases": list(BENCHMARK_CASES), "fixed": True},
        "phase": phase,
        "legacy": {"payload_bytes": len(legacy_raw), "token_estimate": legacy_tokens},
        "compact": {"payload_bytes": len(compact_raw), "token_estimate": compact_tokens},
        "savings": savings,
        "safety": safety,
        "coverage": {"tokens": "estimated", "provider_tokens": "unknown", "fees": "unknown"},
        "snapshot_pointer": pointer,
    }
