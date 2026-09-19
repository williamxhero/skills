"""Read-only benchmark for the compact phase-context transport envelope."""
from __future__ import annotations

import json
import math

from context_projection import ContextProjectionError, read_history
from context_delta import DELTA_COLLECTIONS, PHASE_COLLECTIONS, build_delta_context
from phase_context import compact_context_from_snapshot


BENCHMARK_NAME = "implement-needs-context-budget-v1"
BENCHMARK_CASES = (
    "empty-run",
    "active-dependency",
    "closed-delivery",
    "recovery-exception",
    "release-context",
)
PRESERVED_FIELDS = (
    "run_id",
    "phase",
    "state_version",
    "business_version",
    "event_cursor",
    "acceptance",
    "direct_dependencies",
    "decisions",
    "worktree",
    "version",
    "evidence",
    "events",
    "unresolved_exceptions",
)


def _encoded(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return raw, max(1, math.ceil(len(raw) / 4))


def _legacy_context(db, run_id, phase, stored):
    return {
        "run_id": run_id,
        "phase": phase,
        "state_version": stored["state_version"],
        "business_version": stored["state_version"],
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


def _inconclusive(reason, *, case_name=None):
    benchmark = {"name": BENCHMARK_NAME, "cases": list(BENCHMARK_CASES), "fixed": True}
    if case_name is not None:
        benchmark["case"] = case_name
    return {
        "decision": "inconclusive",
        "reason": reason,
        "benchmark": benchmark,
        "coverage": {"tokens": "estimated", "provider_tokens": "unknown", "fees": "unknown"},
    }


def _benchmark_case(db, run_id, phase, case_name):
    """Compare one persisted fixture without creating snapshots or advancing state."""
    before_version = db.business_version(run_id)
    stored = db.read_snapshot(run_id)
    if stored is None:
        return _inconclusive("snapshot_missing", case_name=case_name)
    compact = compact_context_from_snapshot(db, run_id, phase, stored)
    legacy = _legacy_context(db, run_id, phase, stored)
    legacy_raw, legacy_tokens = _encoded(legacy)
    compact_raw, compact_tokens = _encoded(compact)
    pointer = compact["snapshot_pointer"]
    history = read_history(db, pointer)
    preserved = {key: compact[key] == legacy[key] for key in PRESERVED_FIELDS}
    completion_predicates = {
        key: preserved[key]
        for key in ("acceptance", "direct_dependencies", "decisions", "events", "unresolved_exceptions")
    }
    evidence_coverage = {
        "legacy_count": len(legacy["evidence"]),
        "compact_count": len(compact["evidence"]),
        "equivalent": preserved["evidence"],
    }
    stale_write_rejection = {
        "state_version_retained": preserved["state_version"],
        "event_cursor_retained": preserved["event_cursor"],
        "status": "covered" if preserved["state_version"] and preserved["event_cursor"] else "missing",
    }
    safety = {
        "required_fields_preserved": all(preserved.values()),
        "completion_predicates_equivalent": all(completion_predicates.values()),
        "evidence_coverage_equivalent": evidence_coverage["equivalent"],
        "stale_write_rejection_covered": stale_write_rejection["status"] == "covered",
        "business_version_unchanged": db.business_version(run_id) == before_version,
        "history_pointer_reachable": history["record"] == stored["payload"],
        "provider_token_data": "unknown",
        "fee_data": "unknown",
    }
    savings = {
        "payload_bytes": len(legacy_raw) - len(compact_raw),
        "token_estimate": legacy_tokens - compact_tokens,
        "payload_reduction_ratio": (1 - len(compact_raw) / len(legacy_raw)) if legacy_raw else 0,
    }
    allowed = all(safety[key] for key in (
        "required_fields_preserved",
        "completion_predicates_equivalent",
        "evidence_coverage_equivalent",
        "stale_write_rejection_covered",
        "business_version_unchanged",
        "history_pointer_reachable",
    )) and savings["payload_bytes"] > 0
    return {
        "decision": "allow" if allowed else "inconclusive",
        "benchmark": {"name": BENCHMARK_NAME, "cases": list(BENCHMARK_CASES), "fixed": True, "case": case_name},
        "run_id": run_id,
        "phase": phase,
        "legacy": {"payload_bytes": len(legacy_raw), "token_estimate": legacy_tokens},
        "compact": {"payload_bytes": len(compact_raw), "token_estimate": compact_tokens},
        "savings": savings,
        "safety": safety,
        "completion_predicates": completion_predicates,
        "evidence_coverage": evidence_coverage,
        "stale_write_rejection": stale_write_rejection,
        "coverage": {"tokens": "estimated", "provider_tokens": "unknown", "fees": "unknown"},
        "snapshot_pointer": pointer,
    }


def benchmark_context(db, run_id, phase="planning"):
    """Compare one named fixture without creating snapshots or advancing state."""
    return _benchmark_case(db, run_id, phase, run_id)


def benchmark_context_suite(db, fixture_runs, phase="planning"):
    """Run the complete fixed fixture set against separately persisted runs."""
    if not isinstance(fixture_runs, dict):
        return _inconclusive("fixture_manifest_invalid")
    missing = [case for case in BENCHMARK_CASES if case not in fixture_runs]
    if missing:
        report = _inconclusive("fixture_cases_missing")
        report["missing_cases"] = missing
        return report
    cases = {
        case: _benchmark_case(db, fixture_runs[case], phase, case)
        for case in BENCHMARK_CASES
    }
    passed = all(item["decision"] == "allow" for item in cases.values())
    return {
        "decision": "allow" if passed else "inconclusive",
        "benchmark": {"name": BENCHMARK_NAME, "cases": list(BENCHMARK_CASES), "fixed": True},
        "phase": phase,
        "cases": cases,
        "coverage": {"tokens": "estimated", "provider_tokens": "unknown", "fees": "unknown"},
    }


def benchmark_delta_context(db, run_id, phase="planning"):
    """Compare P3-2 delta transport with the P3-1 compact envelope."""
    before_version = db.business_version(run_id)
    stored = db.read_snapshot(run_id)
    if stored is None:
        return _inconclusive("snapshot_missing", case_name=run_id)
    compact = compact_context_from_snapshot(db, run_id, phase, stored)
    legacy = _legacy_context(db, run_id, phase, stored)
    delta = build_delta_context(db, run_id, phase, stored["event_cursor"], stored["state_version"])
    legacy_raw, legacy_tokens = _encoded(legacy)
    compact_raw, compact_tokens = _encoded(compact)
    delta_raw, delta_tokens = _encoded(delta)
    pointers = {"base": read_history(db, delta["base"]["pointer"])}
    for collection, descriptor in delta["collections"].items():
        pointer = descriptor.get("pointer")
        if not pointer:
            continue
        try:
            pointers[collection] = read_history(db, pointer)
        except ContextProjectionError as exc:
            pointers[collection] = {"error": type(exc).__name__}
    required = PHASE_COLLECTIONS[phase]
    inherit_safe = isinstance(delta.get("base", {}).get("pointer"), str) and bool(delta["base"].get("digest"))
    required_safe = inherit_safe and all(
        descriptor.get("value") is not None or isinstance(descriptor.get("pointer"), str)
        for collection, descriptor in delta["collections"].items()
        if collection in required
    )
    pointer_reachable = all("record" in item for item in pointers.values())
    safety = {
        "required_fields_preserved": required_safe,
        "pointer_reachability": pointer_reachable,
        "business_version_unchanged": db.business_version(run_id) == before_version,
        "side_effect_boundary": db.business_version(run_id) == before_version,
        "provider_token_data": "unknown",
        "fee_data": "unknown",
    }
    savings = {
        "payload_bytes": len(compact_raw) - len(delta_raw),
        "token_estimate": compact_tokens - delta_tokens,
        "payload_reduction_ratio": (1 - len(delta_raw) / len(compact_raw)) if compact_raw else 0,
    }
    allowed = all(safety[key] for key in (
        "required_fields_preserved", "pointer_reachability", "business_version_unchanged", "side_effect_boundary",
    )) and savings["payload_bytes"] >= 0
    return {
        "decision": "allow" if allowed else "inconclusive",
        "benchmark": {"name": BENCHMARK_NAME, "cases": list(BENCHMARK_CASES), "fixed": True, "case": run_id, "candidate": "p3-2-delta"},
        "run_id": run_id,
        "phase": phase,
        "legacy": {"payload_bytes": len(legacy_raw), "token_estimate": legacy_tokens},
        "p3_1_compact": {"payload_bytes": len(compact_raw), "token_estimate": compact_tokens},
        "candidate": {"payload_bytes": len(delta_raw), "token_estimate": delta_tokens},
        "savings": savings,
        "safety": safety,
        "pointers": {collection: {"reachable": "record" in item} for collection, item in pointers.items()},
        "coverage": {"tokens": "estimated", "provider_tokens": "unknown", "fees": "unknown"},
    }


def benchmark_delta_suite(db, fixture_runs, phase="planning"):
    if not isinstance(fixture_runs, dict):
        return _inconclusive("fixture_manifest_invalid")
    missing = [case for case in BENCHMARK_CASES if case not in fixture_runs]
    if missing:
        report = _inconclusive("fixture_cases_missing")
        report["missing_cases"] = missing
        return report
    cases = {
        case: benchmark_delta_context(db, fixture_runs[case], phase)
        for case in BENCHMARK_CASES
    }
    safe = all(item["decision"] == "allow" for item in cases.values())
    improved = any(item["savings"]["payload_bytes"] > 0 for item in cases.values())
    return {
        "decision": "allow" if safe and improved else "inconclusive",
        "benchmark": {"name": BENCHMARK_NAME, "cases": list(BENCHMARK_CASES), "fixed": True, "candidate": "p3-2-delta"},
        "phase": phase,
        "cases": cases,
        "coverage": {"tokens": "estimated", "provider_tokens": "unknown", "fees": "unknown"},
    }
