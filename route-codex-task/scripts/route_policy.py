#!/usr/bin/env python3
"""Single deterministic policy and receipt implementation for Codex task routing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

POLICY_PATH = Path(__file__).resolve().parents[1] / "references" / "model-policy.json"
SHA256_LENGTH = 64

# Evidence levels recorded on every allow receipt. Each level names what was
# actually observed, so a requested route or a title token can never be reported
# as an applied or executed one.
EVIDENCE_UNAVAILABLE = "unavailable"
EVIDENCE_MODES = ("configured_readback", "execution_proof")


def load_policy(path: Path = POLICY_PATH) -> tuple[dict[str, Any] | None, str | None]:
    """Return the canonical policy or a deterministic fail-closed reason."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, exc.__class__.__name__
    fields = {
        "schema_version",
        "owner",
        "models",
        "efforts",
        "model_rank",
        "effort_rank",
        "difficulty_floor",
    }
    if not isinstance(value, dict) or set(value) != fields:
        return None, "invalid_shape"
    models = value.get("models")
    efforts = value.get("efforts")
    model_rank = value.get("model_rank")
    effort_rank = value.get("effort_rank")
    floors = value.get("difficulty_floor")
    expected_models = ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"]
    expected_efforts = ["light", "medium", "high", "xhigh"]
    if (
        value.get("schema_version") != 1
        or value.get("owner") != "route-codex-task"
        or models != expected_models
        or efforts != expected_efforts
        or model_rank != {name: rank for rank, name in enumerate(expected_models)}
        or effort_rank != {name: rank for rank, name in enumerate(expected_efforts)}
        or not isinstance(floors, dict)
        or set(floors) != {"easy", "standard", "hard", "extreme"}
        or any(
            not isinstance(pair, dict)
            or set(pair) != {"model", "thinking"}
            or pair.get("model") not in expected_models
            or pair.get("thinking") not in expected_efforts
            for pair in floors.values()
        )
    ):
        return None, "invalid_values"
    return value, None


POLICY, POLICY_ERROR = load_policy()
MODEL_RANK = (POLICY or {}).get("model_rank", {})
EFFORT_RANK = (POLICY or {}).get("effort_rank", {})
DIFFICULTY_FLOOR = {
    difficulty: (pair["model"], pair["thinking"])
    for difficulty, pair in (POLICY or {}).get("difficulty_floor", {}).items()
}


def issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def sort_issues(issues: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(issues, key=lambda item: (item["path"], item["code"], item["message"]))


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def decision_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(canonical)


def _text(value: Any, path: str, issues: list[dict[str, str]]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        issues.append(issue("invalid_text", path, "Expected a non-empty string."))
        return None
    return value


def _object(
    value: Any, path: str, fields: set[str], issues: list[dict[str, str]]
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        issues.append(issue("invalid_type", path, "Expected an object."))
        return None
    for field in sorted(fields - set(value)):
        issues.append(issue("missing_field", f"{path}.{field}", "Required field is missing."))
    for field in sorted(set(value) - fields):
        issues.append(issue("unknown_field", f"{path}.{field}", "Unknown field is not allowed."))
    return value


def _string_list(
    value: Any, path: str, issues: list[dict[str, str]], *, allow_empty: bool = False
) -> list[str] | None:
    if not isinstance(value, list):
        issues.append(issue("invalid_type", path, "Expected an array."))
        return None
    if not allow_empty and not value:
        issues.append(issue("empty_list", path, "At least one entry is required."))
    result: list[str] = []
    for index, item in enumerate(value):
        parsed = _text(item, f"{path}[{index}]", issues)
        if parsed is not None:
            result.append(parsed)
    if len(result) != len(set(result)):
        issues.append(issue("duplicate_item", path, "Entries must be unique."))
    return result


def policy_pair(
    value: Any, policy: dict[str, Any] | None = None
) -> dict[str, str] | None:
    active = POLICY if policy is None else policy
    if (
        active is None
        or not isinstance(value, dict)
        or set(value) != {"model", "thinking"}
        or value.get("model") not in active.get("models", [])
        or value.get("thinking") not in active.get("efforts", [])
    ):
        return None
    return {"model": value["model"], "thinking": value["thinking"]}


def validate_pair(
    value: Any, path: str, issues: list[dict[str, str]]
) -> dict[str, str] | None:
    obj = _object(value, path, {"model", "thinking"}, issues)
    if obj is None:
        return None
    model = _text(obj.get("model"), f"{path}.model", issues)
    thinking = _text(obj.get("thinking"), f"{path}.thinking", issues)
    if thinking is not None and thinking not in EFFORT_RANK:
        issues.append(issue("unsupported_effort", f"{path}.thinking", "Unknown reasoning effort."))
        thinking = None
    if model is not None and model not in MODEL_RANK:
        issues.append(issue("unsupported_model", f"{path}.model", "Model is not allowed by route-codex-task."))
        model = None
    if model is None or thinking is None:
        return None
    return {"model": model, "thinking": thinking}


def validate_capabilities(
    value: Any, path: str, issues: list[dict[str, str]]
) -> dict[str, set[str]]:
    if not isinstance(value, list):
        issues.append(issue("invalid_type", path, "Expected an array."))
        return {}
    if not value:
        issues.append(issue("empty_capabilities", path, "Advertised task routes are required."))
    result: dict[str, set[str]] = {}
    for index, entry in enumerate(value):
        entry_path = f"{path}[{index}]"
        obj = _object(entry, entry_path, {"model", "thinking"}, issues)
        if obj is None:
            continue
        model = _text(obj.get("model"), f"{entry_path}.model", issues)
        efforts = _string_list(obj.get("thinking"), f"{entry_path}.thinking", issues)
        if model is not None and model not in MODEL_RANK:
            issues.append(issue("unsupported_model", f"{entry_path}.model", "Model is not allowed by route-codex-task."))
            model = None
        if efforts is not None:
            for effort in efforts:
                if effort not in EFFORT_RANK:
                    issues.append(issue("unsupported_effort", f"{entry_path}.thinking", f"Unknown effort {effort}."))
        if model is None or efforts is None or any(item not in EFFORT_RANK for item in efforts):
            continue
        if model in result:
            issues.append(issue("duplicate_model", f"{entry_path}.model", "Each model must appear once."))
        result[model] = set(efforts)
    return result


def all_policy_capabilities() -> dict[str, set[str]]:
    """Return the complete allow-list as a capability matrix for route prediction."""
    if POLICY is None:
        return {}
    return {model: set(POLICY["efforts"]) for model in POLICY["models"]}


def validate_capability_readback(
    value: Any,
    path: str,
    issues: list[dict[str, str]],
    *,
    expected_host_id: str | None = None,
) -> tuple[str | None, dict[str, set[str]]]:
    obj = _object(value, path, {"host_id", "readback_evidence", "supported_routes"}, issues)
    if obj is None:
        return None, {}
    host_id = _text(obj.get("host_id"), f"{path}.host_id", issues)
    _string_list(obj.get("readback_evidence"), f"{path}.readback_evidence", issues)
    capabilities = validate_capabilities(obj.get("supported_routes"), f"{path}.supported_routes", issues)
    if expected_host_id is not None and host_id != expected_host_id:
        issues.append(issue("host_id_mismatch", f"{path}.host_id", "Capability readback belongs to another host."))
    return host_id, capabilities


def supported_pair(
    pair: dict[str, str] | None,
    path: str,
    capabilities: dict[str, set[str]],
    issues: list[dict[str, str]],
) -> tuple[int, int] | None:
    if pair is None:
        return None
    if pair["thinking"] not in capabilities.get(pair["model"], set()):
        issues.append(issue("route_not_advertised", path, "Model and effort pair is absent from the host capability readback."))
        return None
    return MODEL_RANK[pair["model"]], EFFORT_RANK[pair["thinking"]]


def same_or_stronger(candidate: dict[str, str], baseline: dict[str, str]) -> bool:
    return (
        policy_pair(candidate) is not None
        and policy_pair(baseline) is not None
        and MODEL_RANK[candidate["model"]] >= MODEL_RANK[baseline["model"]]
        and EFFORT_RANK[candidate["thinking"]] >= EFFORT_RANK[baseline["thinking"]]
    )


def distinct_same_or_stronger_allowed(pair: dict[str, str]) -> bool:
    if POLICY is None or policy_pair(pair) is None:
        return False
    return any(
        candidate != pair and same_or_stronger(candidate, pair)
        for candidate in (
            {"model": model, "thinking": effort}
            for model in POLICY["models"]
            for effort in POLICY["efforts"]
        )
    )


def validate_route(
    value: Any,
    path: str,
    capabilities: dict[str, set[str]],
    issues: list[dict[str, str]],
    *,
    minimum: tuple[str, str] | None = None,
    xhigh_evidence: list[str] | None = None,
) -> dict[str, Any] | None:
    obj = _object(value, path, {"recommended", "fallbacks", "rationale"}, issues)
    if obj is None:
        return None
    rationale = _text(obj.get("rationale"), f"{path}.rationale", issues)
    recommended = validate_pair(obj.get("recommended"), f"{path}.recommended", issues)
    recommended_rank = supported_pair(recommended, f"{path}.recommended", capabilities, issues)
    fallback_values = obj.get("fallbacks")
    if not isinstance(fallback_values, list):
        issues.append(issue("invalid_type", f"{path}.fallbacks", "Expected an array."))
        fallback_values = []
    elif not fallback_values:
        issues.append(issue("fallback_missing", f"{path}.fallbacks", "At least one pre-authorized fallback is required."))
    fallbacks: list[dict[str, str]] = []
    for index, fallback_value in enumerate(fallback_values):
        fallback_path = f"{path}.fallbacks[{index}]"
        fallback = validate_pair(fallback_value, fallback_path, issues)
        fallback_rank = supported_pair(fallback, fallback_path, capabilities, issues)
        if fallback is None:
            continue
        fallbacks.append(fallback)
        if fallback == recommended and distinct_same_or_stronger_allowed(fallback):
            issues.append(issue("fallback_duplicates_recommendation", fallback_path, "Fallback must be distinct while another same-or-stronger allowed pair exists."))
        if recommended_rank is not None and fallback_rank is not None and not same_or_stronger(fallback, recommended):
            issues.append(issue("fallback_weaker", fallback_path, "Fallback must be same-or-stronger in model and effort."))
    encoded = [json.dumps(item, sort_keys=True) for item in fallbacks]
    if len(encoded) != len(set(encoded)):
        issues.append(issue("duplicate_fallback", f"{path}.fallbacks", "Fallback pairs must be unique."))
    if (
        recommended is not None
        and recommended["thinking"] == "xhigh"
        and xhigh_evidence is not None
        and not xhigh_evidence
    ):
        issues.append(issue("xhigh_without_evidence", path, "xhigh requires concrete evidence that high is inadequate."))
    if minimum is not None:
        validate_floor(recommended, minimum, path, capabilities, issues)
    if recommended is None or rationale is None:
        return None
    return {"recommended": recommended, "fallbacks": fallbacks, "rationale": rationale}


def validate_floor(
    recommended: dict[str, str] | None,
    minimum: tuple[str, str],
    path: str,
    capabilities: dict[str, set[str]],
    issues: list[dict[str, str]],
) -> None:
    rank = supported_pair(recommended, f"{path}.recommended", capabilities, [])
    if rank is not None and (
        rank[0] < MODEL_RANK[minimum[0]] or rank[1] < EFFORT_RANK[minimum[1]]
    ):
        issues.append(issue("route_below_floor", path, f"Route must be at least {minimum[0]}/{minimum[1]}."))


def validate_task_readback(
    value: Any,
    path: str,
    issues: list[dict[str, str]],
    *,
    identity_field: str,
    expected_identity: str,
    expected_target: str,
    expected_task_id: str,
    route: dict[str, Any] | None,
    capabilities: dict[str, set[str]],
    xhigh_evidence: list[str] | None = None,
) -> dict[str, Any] | None:
    fields = {
        "schema_version", identity_field, "task_id", "target", "requested", "applied",
        "selection", "substitution_reason", "readback_evidence",
    }
    obj = _object(value, path, fields, issues)
    if obj is None:
        return None
    if obj.get("schema_version") != 1:
        issues.append(issue("schema_version_mismatch", f"{path}.schema_version", "Readback schema version must be 1."))
    if obj.get(identity_field) != expected_identity:
        issues.append(issue(f"{identity_field}_mismatch", f"{path}.{identity_field}", "Routing artifacts belong to another route."))
    if obj.get("target") != expected_target:
        issues.append(issue("route_target_mismatch", f"{path}.target", "Readback target does not match the route."))
    task_id = _text(obj.get("task_id"), f"{path}.task_id", issues)
    _string_list(obj.get("readback_evidence"), f"{path}.readback_evidence", issues)
    requested = validate_pair(obj.get("requested"), f"{path}.requested", issues)
    applied = validate_pair(obj.get("applied"), f"{path}.applied", issues)
    supported_pair(requested, f"{path}.requested", capabilities, issues)
    supported_pair(applied, f"{path}.applied", capabilities, issues)
    if task_id != expected_task_id:
        issues.append(issue("task_id_mismatch", f"{path}.task_id", "Readback does not name the task created for this route."))
    selection = obj.get("selection")
    reason = obj.get("substitution_reason")
    if selection not in {"recommended", "fallback"}:
        issues.append(issue("invalid_route_selection", f"{path}.selection", "Selection must be recommended or fallback."))
    elif route is not None and requested is not None:
        if selection == "recommended":
            if requested != route["recommended"]:
                issues.append(issue("recommendation_not_requested", f"{path}.requested", "Recommended selection must request the locked recommendation."))
            if reason is not None:
                issues.append(issue("unexpected_substitution_reason", f"{path}.substitution_reason", "Exact recommendation needs no substitution reason."))
        else:
            if requested not in route["fallbacks"]:
                issues.append(issue("fallback_not_preapproved", f"{path}.requested", "Requested fallback was not locked."))
            _text(reason, f"{path}.substitution_reason", issues)
    if requested is not None and requested["thinking"] == "xhigh" and not xhigh_evidence:
        issues.append(issue("xhigh_without_evidence", f"{path}.requested", "xhigh requires concrete evidence that high is inadequate."))
    if requested is not None and applied is not None and requested != applied:
        issues.append(issue("silent_route_drift", f"{path}.applied", "Applied model and effort must exactly match the explicit request."))
    return obj


#: Fields a provider or turn level execution record must carry to prove that the
#: requested route actually ran. Anything less stays ``unavailable``.
EXECUTION_EVIDENCE_FIELDS = ("source", "turn_id", "model", "thinking", "observed_at")


def validate_execution_evidence(
    value: Any, path: str, issues: list[dict[str, str]]
) -> str:
    """Return a proof label for real execution evidence, else ``unavailable``.

    A request, a route selection, or a task title is not execution evidence. Only
    a record that names the provider or turn source, the turn, the observed model
    and effort, and when it was observed counts as proof.
    """
    if value is None:
        return EVIDENCE_UNAVAILABLE
    if not isinstance(value, dict) or set(value) != set(EXECUTION_EVIDENCE_FIELDS):
        issues.append(issue("invalid_execution_evidence", path, "Execution evidence must carry exactly source, turn_id, model, thinking, and observed_at."))
        return EVIDENCE_UNAVAILABLE
    for field in EXECUTION_EVIDENCE_FIELDS:
        if not isinstance(value.get(field), str) or not value[field].strip():
            issues.append(issue("invalid_execution_evidence", f"{path}.{field}", "Execution evidence fields must be non-empty strings."))
            return EVIDENCE_UNAVAILABLE
    if policy_pair({"model": value["model"], "thinking": value["thinking"]}) is None:
        issues.append(issue("invalid_execution_evidence", path, "Execution evidence pair must be allowed by route-codex-task."))
        return EVIDENCE_UNAVAILABLE
    return "provider_or_turn_readback"


def build_route_receipt(
    *,
    identity_name: str,
    identity: str,
    target: str,
    task_id: str,
    selection: str,
    applied: dict[str, str],
    route: dict[str, Any],
    record_hash_name: str,
    record_hash: str,
    readback_hash_name: str,
    readback_hash: str,
    identity_evidence: str = EVIDENCE_UNAVAILABLE,
    capability_evidence: str = EVIDENCE_UNAVAILABLE,
    configured_route_evidence: str = EVIDENCE_UNAVAILABLE,
    execution_evidence: str = EVIDENCE_UNAVAILABLE,
    gate: str = "task_route",
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "decision": "allow",
        "gate": gate,
        identity_name: identity,
        "target": target,
        "task_id": task_id,
        "selection": selection,
        "applied": applied,
        "locked_route": {"recommended": route["recommended"], "fallbacks": route["fallbacks"]},
        "evidence_levels": {
            "identity_evidence": identity_evidence,
            "capability_evidence": capability_evidence,
            "configured_route_evidence": configured_route_evidence,
            "execution_evidence": execution_evidence,
        },
        record_hash_name: record_hash,
        readback_hash_name: readback_hash,
    }
    if extra_fields:
        payload.update(extra_fields)
    payload["receipt_sha256"] = decision_hash(payload)
    return payload


EVIDENCE_LEVEL_FIELDS = (
    "identity_evidence",
    "capability_evidence",
    "configured_route_evidence",
    "execution_evidence",
)


def evidence_level_issues(value: Any, path: str) -> list[dict[str, str]]:
    """Validate the four recorded evidence levels on a route receipt."""
    if not isinstance(value, dict) or set(value) != set(EVIDENCE_LEVEL_FIELDS):
        return [issue("invalid_evidence_levels", path, "Route receipt must record identity, capability, configured route, and execution evidence levels.")]
    issues: list[dict[str, str]] = []
    for field in EVIDENCE_LEVEL_FIELDS:
        if not isinstance(value.get(field), str) or not value[field].strip():
            issues.append(issue("invalid_evidence_levels", f"{path}.{field}", "Evidence levels must be non-empty strings; use unavailable when nothing was observed."))
    if value.get("configured_route_evidence") == EVIDENCE_UNAVAILABLE:
        issues.append(issue("configured_route_evidence_missing", f"{path}.configured_route_evidence", "An allow receipt requires a post-create configured route readback."))
    return issues


def route_receipt_issues(
    value: Any,
    path: str,
    *,
    run_id: str,
    target: str,
    task_id: str,
    selection: Any,
    model: Any,
    thinking: Any,
    planning_record_sha256: str | None,
) -> list[dict[str, str]]:
    fields = {
        "schema_version", "decision", "gate", "run_id", "target", "task_id",
        "selection", "applied", "locked_route", "planning_record_sha256",
        "route_readback_sha256", "receipt_sha256",
    }
    # evidence_levels is written by current receipts but stays optional on
    # readback so receipts minted before the field existed still validate.
    required = fields | ({"evidence_levels"} if isinstance(value, dict) and "evidence_levels" in value else set())
    if not isinstance(value, dict) or set(value) != required:
        return [issue("invalid_route_receipt", path, "Route receipt must be the exact task_route allow receipt.")]
    issues: list[dict[str, str]] = []
    for field, expected in (("schema_version", 1), ("decision", "allow"), ("gate", "task_route"), ("run_id", run_id), ("target", target), ("task_id", task_id), ("selection", selection)):
        if value.get(field) != expected:
            issues.append(issue(f"route_receipt_{field}_mismatch", f"{path}.{field}", f"Route receipt {field} must match the dispatch identity."))
    applied = policy_pair(value.get("applied"))
    event_pair = policy_pair({"model": model, "thinking": thinking})
    if applied is None:
        issues.append(issue("invalid_route_receipt_pair", f"{path}.applied", "Route receipt pair must be allowed by route-codex-task."))
    elif event_pair is None or applied != event_pair:
        issues.append(issue("route_receipt_pair_mismatch", f"{path}.applied", "Route receipt pair must equal the persisted dispatch pair."))
    locked = value.get("locked_route")
    recommended = None
    fallbacks: list[dict[str, str]] = []
    if not isinstance(locked, dict) or set(locked) != {"recommended", "fallbacks"} or not isinstance(locked.get("fallbacks"), list) or not locked["fallbacks"]:
        issues.append(issue("invalid_locked_route", f"{path}.locked_route", "Receipt must retain one recommendation and at least one fallback."))
    else:
        recommended = policy_pair(locked.get("recommended"))
        parsed = [policy_pair(item) for item in locked["fallbacks"]]
        if recommended is None or any(item is None for item in parsed):
            issues.append(issue("invalid_locked_route", f"{path}.locked_route", "Every locked route pair must be allowed by route-codex-task."))
        else:
            fallbacks = [item for item in parsed if item is not None]
            if len({json.dumps(item, sort_keys=True) for item in fallbacks}) != len(fallbacks):
                issues.append(issue("duplicate_locked_fallback", f"{path}.locked_route.fallbacks", "Locked fallback pairs must be unique."))
            for index, fallback in enumerate(fallbacks):
                fallback_path = f"{path}.locked_route.fallbacks[{index}]"
                if not same_or_stronger(fallback, recommended):
                    issues.append(issue("locked_fallback_weaker", fallback_path, "Locked fallback must be same-or-stronger than the recommendation."))
                if fallback == recommended and distinct_same_or_stronger_allowed(recommended):
                    issues.append(issue("locked_fallback_not_distinct", fallback_path, "Same-pair fallback is valid only at the maximum allowed route."))
    if applied is not None and recommended is not None:
        if selection == "recommended" and applied != recommended:
            issues.append(issue("route_not_locked_recommendation", f"{path}.applied", "Recommended dispatch must use the exact locked recommendation."))
        elif selection == "fallback" and applied not in fallbacks:
            issues.append(issue("route_not_preapproved_fallback", f"{path}.applied", "Fallback dispatch must use an exact preapproved fallback."))
    for field in ("planning_record_sha256", "route_readback_sha256", "receipt_sha256"):
        digest = value.get(field)
        if not isinstance(digest, str) or len(digest) != SHA256_LENGTH or any(char not in "0123456789abcdef" for char in digest):
            issues.append(issue("invalid_route_receipt_hash", f"{path}.{field}", "Route receipt hashes must be lowercase SHA-256 values."))
    if "evidence_levels" in value:
        issues.extend(evidence_level_issues(value["evidence_levels"], f"{path}.evidence_levels"))
    if value.get("planning_record_sha256") != planning_record_sha256:
        issues.append(issue("route_receipt_planning_mismatch", f"{path}.planning_record_sha256", "Route receipt must bind the archived planning record."))
    receipt_body = dict(value)
    receipt_sha = receipt_body.pop("receipt_sha256", None)
    if decision_hash(receipt_body) != receipt_sha:
        issues.append(issue("route_receipt_hash_mismatch", f"{path}.receipt_sha256", "Route receipt identity does not match its canonical content."))
    return sort_issues(issues)


def persisted_route_issues(
    route: dict[str, Any],
    *,
    path: str,
    run_id: str,
    spec_id: str,
    owner_id: str,
) -> list[dict[str, str]]:
    """Validate a persisted owner route against its immutable allow receipt."""
    issues: list[dict[str, str]] = []
    receipt = route["receipt"]
    selected_pair = policy_pair({"model": route["model"], "thinking": route["thinking"]})
    applied = policy_pair(receipt["applied"])
    recommended = policy_pair(receipt["locked_route"]["recommended"])
    fallbacks = [policy_pair(item) for item in receipt["locked_route"]["fallbacks"]]
    if selected_pair is None or applied is None or recommended is None or any(item is None for item in fallbacks):
        return [issue("invalid_persisted_model_policy", path, "Persisted route and receipt pairs must be allowed by route-codex-task.")]
    parsed_fallbacks = [item for item in fallbacks if item is not None]
    for field, expected in (("run_id", run_id), ("target", spec_id), ("task_id", owner_id), ("selection", route["selection"]), ("applied", selected_pair)):
        if receipt[field] != expected:
            issues.append(issue("persisted_route_receipt_mismatch", f"{path}.receipt.{field}", "Route receipt must match the persisted run, owner, selection, and pair."))
    receipt_body = dict(receipt)
    receipt_sha = receipt_body.pop("receipt_sha256")
    if decision_hash(receipt_body) != receipt_sha:
        issues.append(issue("persisted_route_receipt_hash_mismatch", f"{path}.receipt.receipt_sha256", "Persisted route receipt identity does not match its canonical content."))
    for index, fallback in enumerate(parsed_fallbacks):
        fallback_path = f"{path}.receipt.locked_route.fallbacks[{index}]"
        if not same_or_stronger(fallback, recommended):
            issues.append(issue("persisted_fallback_weaker", fallback_path, "Persisted fallback must be same-or-stronger than the recommendation."))
        if fallback == recommended and distinct_same_or_stronger_allowed(recommended):
            issues.append(issue("persisted_fallback_not_distinct", fallback_path, "Same-pair fallback is valid only at the maximum allowed route."))
    if route["selection"] == "recommended" and selected_pair != recommended:
        issues.append(issue("persisted_route_not_locked", path, "Recommended route must equal the exact locked recommendation."))
    if route["selection"] == "fallback" and selected_pair not in parsed_fallbacks:
        issues.append(issue("persisted_route_not_locked", path, "Fallback route must equal an exact preapproved fallback."))
    return issues
