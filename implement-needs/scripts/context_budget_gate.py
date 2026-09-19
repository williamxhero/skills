"""Read-only runtime admission and fail-closed fallback for context budgets."""
from __future__ import annotations

import json
import math

from context_delta import ContextDeltaError, build_delta_context
from context_budget import BENCHMARK_CASES
from context_projection import ContextProjectionError, PHASE_PROJECTIONS, read_history
from phase_context import compact_context_from_snapshot


BUDGET_VERSION = "context-budget-v1"
PHASE_BUDGETS = {
    "startup": {"max_bytes": 2048, "max_tokens": 512},
    "planning": {"max_bytes": 4096, "max_tokens": 1024},
    "implementation": {"max_bytes": 8192, "max_tokens": 2048},
    "verification": {"max_bytes": 8192, "max_tokens": 2048},
    "recovery": {"max_bytes": 12288, "max_tokens": 3072},
    "release": {"max_bytes": 12288, "max_tokens": 3072},
}
REQUIRED_COMPACT_FIELDS = frozenset({
    "run_id", "phase", "business_version", "event_cursor", "acceptance",
    "direct_dependencies", "evidence", "unresolved_exceptions", "snapshot_pointer",
})


def _encoded(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return raw, max(1, math.ceil(len(raw) / 4))


def _budget(phase, override=None):
    if phase not in PHASE_PROJECTIONS:
        raise ContextProjectionError("context_phase_unknown", {"phase": phase})
    selected = dict(PHASE_BUDGETS[phase] if override is None else override)
    if (
        not isinstance(selected, dict)
        or not isinstance(selected.get("max_bytes"), int)
        or isinstance(selected.get("max_bytes"), bool)
        or selected["max_bytes"] < 1
        or not isinstance(selected.get("max_tokens"), int)
        or isinstance(selected.get("max_tokens"), bool)
        or selected["max_tokens"] < 1
    ):
        raise ContextProjectionError("context_budget_invalid")
    return {"version": BUDGET_VERSION, "phase": phase, **selected}


def _required_fields(context):
    if context.get("mode") == "delta":
        base = context.get("base")
        return (
            all(field in context for field in ("context_version", "run_id", "phase", "business_version", "event_cursor", "collections"))
            and isinstance(base, dict)
            and isinstance(base.get("pointer"), str)
            and isinstance(base.get("digest"), str)
        )
    return REQUIRED_COMPACT_FIELDS.issubset(context)


def _pointer_reachable(db, context, stored):
    try:
        pointer = context["base"]["pointer"] if context.get("mode") == "delta" else context["snapshot_pointer"]
        history = read_history(db, pointer)
    except (ContextProjectionError, KeyError, TypeError):
        return False
    if context.get("mode") == "delta":
        return history.get("record") == stored["payload"]
    return history.get("record") == stored["payload"]


def _report(db, run_id, phase, context, before_version, budget, *, fallback):
    raw, token_estimate = _encoded(context)
    observed_tokens = None
    required = _required_fields(context)
    stored = db.read_snapshot(run_id)
    pointer_reachable = stored is not None and _pointer_reachable(db, context, stored)
    unchanged = db.business_version(run_id) == before_version
    measurement = {
        "payload_bytes": len(raw),
        "token_estimate": token_estimate,
        "observed_tokens": observed_tokens,
        "fee": None,
        "coverage": {"tokens": "estimated", "provider_tokens": "unknown", "fees": "unknown"},
    }
    safety = {
        "required_fields_preserved": required,
        "pointer_reachability": pointer_reachable,
        "business_version_unchanged": unchanged,
        "side_effect_boundary": unchanged,
    }
    within_budget = len(raw) <= budget["max_bytes"] and token_estimate <= budget["max_tokens"]
    allowed = within_budget and all(safety.values())
    return {
        "decision": "allow" if allowed else "inconclusive",
        "phase": phase,
        "run_id": run_id,
        "budget": budget,
        "measurement": measurement,
        "safety": safety,
        "fallback": fallback,
        "context": context if allowed else None,
        "reason": None if allowed else ("context_budget_exceeded" if not within_budget else "context_safety_inconclusive"),
    }


def admit_context(db, run_id, phase, mode="compact", base_event_cursor=None, base_state_version=None, budget=None):
    """Admit a context or return a structured non-success result without writes."""
    selected_budget = _budget(phase, budget)
    if mode not in {"compact", "delta"}:
        return {"decision": "blocked", "reason": "context_mode_invalid", "budget": selected_budget}
    stored = db.read_snapshot(run_id)
    if stored is None:
        return {"decision": "blocked", "reason": "context_snapshot_missing", "budget": selected_budget}
    before_version = db.business_version(run_id)
    try:
        if mode == "delta":
            if base_event_cursor is None:
                return {"decision": "blocked", "reason": "delta_base_cursor_missing", "budget": selected_budget}
            context = build_delta_context(db, run_id, phase, base_event_cursor, base_state_version)
        else:
            context = compact_context_from_snapshot(db, run_id, phase, stored)
        result = _report(db, run_id, phase, context, before_version, selected_budget, fallback="none")
        if result["decision"] == "allow":
            return result
        if mode == "compact":
            fallback = build_delta_context(db, run_id, phase, stored["event_cursor"], stored["state_version"])
            fallback_result = _report(db, run_id, phase, fallback, before_version, selected_budget, fallback="delta")
            if fallback_result["decision"] == "allow":
                return fallback_result
        result["context"] = None
        result["fallback"] = "blocked"
        return result
    except (ContextProjectionError, ContextDeltaError) as exc:
        return {
            "decision": "blocked",
            "reason": exc.code,
            "details": exc.details,
            "budget": selected_budget,
            "fallback": "blocked",
        }


def benchmark_budget_suite(db, fixture_runs, phase="planning", budget=None):
    """Evaluate the fixed P3 fixture set without mutating business state."""
    if not isinstance(fixture_runs, dict):
        return {"decision": "inconclusive", "reason": "fixture_manifest_invalid"}
    missing = [case for case in BENCHMARK_CASES if case not in fixture_runs]
    if missing:
        return {"decision": "inconclusive", "reason": "fixture_cases_missing", "missing_cases": missing}
    cases = {
        case: admit_context(db, fixture_runs[case], phase, budget=budget)
        for case in BENCHMARK_CASES
    }
    allowed = all(item["decision"] == "allow" for item in cases.values())
    return {
        "decision": "allow" if allowed else "inconclusive",
        "benchmark": {"name": "implement-needs-context-budget-v1", "cases": list(BENCHMARK_CASES), "fixed": True},
        "phase": phase,
        "cases": cases,
    }
