"""Auditable whole-SPEC route summaries and route inheritance gates.

Planning values are deliberately separate from applied and turn execution
facts.  A summary is a prediction/authorization artifact; it never proves
what a host actually applied.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping


FACTORS = ("difficulty", "risk", "coupling", "ambiguity", "verification_burden")

_ROUTE_POLICY = Path(__file__).resolve().parents[2] / "route-codex-task" / "scripts"
if str(_ROUTE_POLICY) not in sys.path:
    sys.path.insert(0, str(_ROUTE_POLICY))
try:
    from route_policy import validate_pair
except ImportError:  # fail closed in validation, while keeping import diagnostics clear
    validate_pair = None


class RouteSummaryError(ValueError):
    """A route summary is absent, stale, or unsafe for dispatch."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RouteSummaryError(f"{field} must be a non-empty string")
    return value.strip()


def validate_route_summary(summary: Mapping[str, Any], *, spec_id: str | None = None,
                           planning_version: str | None = None) -> dict[str, Any]:
    if not isinstance(summary, Mapping):
        raise RouteSummaryError("route summary is required")
    value = dict(summary)
    for field in ("spec_id", "planning_version", "planned_model", "planned_effort",
                  "selection", "strategy_version"):
        _text(value.get(field), field)
    if validate_pair is None:
        raise RouteSummaryError("route-codex-task policy unavailable")
    route_issues: list[dict[str, Any]] = []
    if validate_pair({"model": value["planned_model"], "thinking": value["planned_effort"]}, "route_summary.planned", route_issues) is None:
        raise RouteSummaryError("planned route is not in the approved route policy")
    if spec_id is not None and value["spec_id"] != spec_id:
        raise RouteSummaryError("route summary spec identity mismatch")
    if planning_version is not None and value["planning_version"] != planning_version:
        raise RouteSummaryError("route summary is stale")
    factors = value.get("factors")
    if not isinstance(factors, Mapping) or any(not _text(factors.get(k), f"factors.{k}") for k in FACTORS):
        raise RouteSummaryError("route summary factors are incomplete")
    fallbacks = value.get("approved_fallbacks", [])
    if not isinstance(fallbacks, list) or any(not isinstance(item, Mapping) for item in fallbacks):
        raise RouteSummaryError("approved_fallbacks must be an array of objects")
    for item in fallbacks:
        if validate_pair({"model": item.get("model"), "thinking": item.get("effort")}, "route_summary.fallback", route_issues) is None:
            raise RouteSummaryError("fallback route is not in the approved route policy")
    evidence = value.get("evidence")
    if not isinstance(evidence, list) or not evidence or any(not isinstance(item, str) or not item.strip() for item in evidence):
        raise RouteSummaryError("route summary evidence is required")
    if value.get("ticket_override") is not None:
        raise RouteSummaryError("ticket-level route override is forbidden")
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    value["summary_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    value["source_kind"] = "whole_spec_planning"
    return value


def make_route_summary(*, spec_id: str, planning_version: str, planned_model: str,
                       planned_effort: str, selection: str, factors: Mapping[str, str],
                       approved_fallbacks: list[Mapping[str, Any]], evidence: list[str],
                       strategy_version: str = "route-codex-task-v1") -> dict[str, Any]:
    return validate_route_summary({
        "spec_id": spec_id, "planning_version": planning_version,
        "planned_model": planned_model, "planned_effort": planned_effort,
        "selection": selection, "factors": dict(factors),
        "approved_fallbacks": list(approved_fallbacks), "evidence": list(evidence),
        "strategy_version": strategy_version,
    }, spec_id=spec_id, planning_version=planning_version)


def inherit_ticket_route(summary: Mapping[str, Any], ticket: Mapping[str, Any]) -> dict[str, Any]:
    checked = validate_route_summary(summary)
    if ticket.get("route_override") is not None or ticket.get("model") is not None or ticket.get("effort") is not None:
        raise RouteSummaryError("ticket cannot override its SPEC route")
    return {"source": "spec_route_summary", "spec_id": checked["spec_id"],
            "planned_model": checked["planned_model"], "planned_effort": checked["planned_effort"],
            "summary_digest": checked["summary_digest"]}


def route_status(*, summary: Mapping[str, Any], applied: Mapping[str, Any] | None = None,
                 execution: Mapping[str, Any] | None = None) -> dict[str, Any]:
    checked = validate_route_summary(summary)
    result = {"planned": {"model": checked["planned_model"], "effort": checked["planned_effort"],
                           "summary_digest": checked["summary_digest"]},
              "applied": None, "execution": None}
    if applied is not None:
        if not applied.get("model") or not applied.get("effort"):
            raise RouteSummaryError("applied route readback is incomplete")
        result["applied"] = dict(applied)
    if execution is not None:
        result["execution"] = dict(execution)
    return result


def dispatch_route_gate(*, summary: Mapping[str, Any] | None, applied: Mapping[str, Any] | None,
                       identity: Mapping[str, Any] | None, expected: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if summary is None:
        return {"decision": "blocked", "reason": "route_summary_missing"}
    try:
        checked = validate_route_summary(summary)
    except RouteSummaryError as exc:
        return {"decision": "blocked", "reason": str(exc)}
    if not isinstance(identity, Mapping) or any(not identity.get(k) for k in ("run_id", "task_id", "attempt_id", "formal_thread_id", "host_id")):
        return {"decision": "blocked", "reason": "route_identity_readback_missing"}
    if not isinstance(applied, Mapping) or not applied.get("model") or not applied.get("effort"):
        return {"decision": "blocked", "reason": "applied_route_readback_missing"}
    if expected:
        selected = {"model": applied.get("model"), "effort": applied.get("effort")}
        allowed = [{"model": checked["planned_model"], "effort": checked["planned_effort"]}]
        allowed.extend({"model": item.get("model"), "effort": item.get("effort")} for item in checked["approved_fallbacks"])
        if selected not in allowed:
            return {"decision": "blocked", "reason": "applied_route_mismatch"}
    return {"decision": "allow", "reason": "verified_applied_route", "route": route_status(summary=checked, applied=applied)}
