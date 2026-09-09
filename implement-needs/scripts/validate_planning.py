#!/usr/bin/env python3
"""Fail-closed planning and task-routing gates for Implement Needs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

MODEL_CLASS_RANK = {"fast": 0, "balanced": 1, "reliable": 2, "strongest": 3}
EFFORT_RANK = {
    "none": 0,
    "minimal": 1,
    "low": 2,
    "medium": 3,
    "high": 4,
    "xhigh": 5,
    "max": 6,
    "ultra": 7,
}
DIFFICULTY_FLOOR = {
    "easy": ("fast", "medium"),
    "standard": ("balanced", "high"),
    "hard": ("reliable", "xhigh"),
    "extreme": ("strongest", "max"),
}
AUTO_APPROVAL = {
    "confirmation_mode": "auto_approve",
    "approval_source": "implement-needs",
    "approval_text": "同意",
    "spec_state": "auto_approved",
    "approval_provenance": "controller_decision",
}
HAN_RE = re.compile(r"[\u3400-\u9fff]")
SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _sorted(issues: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(issues, key=lambda item: (item["path"], item["code"], item["message"]))


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_json(
    path: Path, label: str, issues: list[dict[str, str]]
) -> tuple[Any, bytes | None]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        issues.append(
            _issue(
                f"{label}_unreadable",
                str(path),
                f"Cannot read required artifact: {exc.__class__.__name__}.",
            )
        )
        return None, None
    try:
        return json.loads(raw.decode("utf-8-sig")), raw
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        issues.append(
            _issue(
                f"{label}_malformed",
                str(path),
                f"Artifact is not valid UTF-8 JSON: {exc.__class__.__name__}.",
            )
        )
        return None, raw


def _object(
    value: Any,
    path: str,
    fields: set[str],
    issues: list[dict[str, str]],
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        issues.append(_issue("invalid_type", path, "Expected an object."))
        return None
    for field in sorted(fields - set(value)):
        issues.append(_issue("missing_field", f"{path}.{field}", "Required field is missing."))
    for field in sorted(set(value) - fields):
        issues.append(_issue("unknown_field", f"{path}.{field}", "Unknown field is not allowed."))
    return value


def _list(value: Any, path: str, issues: list[dict[str, str]]) -> list[Any] | None:
    if not isinstance(value, list):
        issues.append(_issue("invalid_type", path, "Expected an array."))
        return None
    return value


def _text(value: Any, path: str, issues: list[dict[str, str]]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        issues.append(_issue("invalid_text", path, "Expected a non-empty string."))
        return None
    return value


def _string_list(
    value: Any,
    path: str,
    issues: list[dict[str, str]],
    *,
    allow_empty: bool = False,
) -> list[str] | None:
    items = _list(value, path, issues)
    if items is None:
        return None
    if not allow_empty and not items:
        issues.append(_issue("empty_list", path, "At least one entry is required."))
    result: list[str] = []
    for index, item in enumerate(items):
        parsed = _text(item, f"{path}[{index}]", issues)
        if parsed is not None:
            result.append(parsed)
    if len(result) != len(set(result)):
        issues.append(_issue("duplicate_item", path, "Entries must be unique."))
    return result


def _pair(
    value: Any, path: str, issues: list[dict[str, str]]
) -> dict[str, str] | None:
    obj = _object(value, path, {"model", "thinking"}, issues)
    if obj is None:
        return None
    model = _text(obj.get("model"), f"{path}.model", issues)
    thinking = obj.get("thinking")
    if thinking not in EFFORT_RANK:
        issues.append(_issue("unsupported_effort", f"{path}.thinking", "Unknown reasoning effort."))
        return None
    if model is None:
        return None
    return {"model": model, "thinking": thinking}


def _capabilities(
    record: dict[str, Any], issues: list[dict[str, str]]
) -> dict[str, tuple[str, set[str]]]:
    entries = _list(record.get("supported_routes"), "$.supported_routes", issues)
    if entries is None:
        return {}
    if not entries:
        issues.append(_issue("empty_capabilities", "$.supported_routes", "Advertised task routes are required."))
    result: dict[str, tuple[str, set[str]]] = {}
    for index, entry in enumerate(entries):
        path = f"$.supported_routes[{index}]"
        obj = _object(entry, path, {"model", "model_class", "thinking"}, issues)
        if obj is None:
            continue
        model = _text(obj.get("model"), f"{path}.model", issues)
        model_class = obj.get("model_class")
        if model_class not in MODEL_CLASS_RANK:
            issues.append(_issue("unsupported_model_class", f"{path}.model_class", "Unknown model class."))
        thinking = _string_list(obj.get("thinking"), f"{path}.thinking", issues)
        if thinking is not None:
            for effort in thinking:
                if effort not in EFFORT_RANK:
                    issues.append(_issue("unsupported_effort", f"{path}.thinking", f"Unknown effort {effort}."))
        if model is None or model_class not in MODEL_CLASS_RANK or thinking is None:
            continue
        if model in result:
            issues.append(_issue("duplicate_model", f"{path}.model", "Each model must appear once."))
        result[model] = (model_class, set(thinking))
    return result


def _supported_pair(
    pair: dict[str, str] | None,
    path: str,
    capabilities: dict[str, tuple[str, set[str]]],
    issues: list[dict[str, str]],
) -> tuple[int, int] | None:
    if pair is None:
        return None
    capability = capabilities.get(pair["model"])
    if capability is None or pair["thinking"] not in capability[1]:
        issues.append(_issue("route_not_advertised", path, "Model and effort pair is not in the captured capability matrix."))
        return None
    return MODEL_CLASS_RANK[capability[0]], EFFORT_RANK[pair["thinking"]]


def _route(
    value: Any,
    path: str,
    capabilities: dict[str, tuple[str, set[str]]],
    issues: list[dict[str, str]],
) -> dict[str, Any] | None:
    obj = _object(value, path, {"recommended", "fallbacks", "rationale"}, issues)
    if obj is None:
        return None
    rationale = _text(obj.get("rationale"), f"{path}.rationale", issues)
    recommended = _pair(obj.get("recommended"), f"{path}.recommended", issues)
    recommended_rank = _supported_pair(recommended, f"{path}.recommended", capabilities, issues)
    fallback_values = _list(obj.get("fallbacks"), f"{path}.fallbacks", issues)
    fallbacks: list[dict[str, str]] = []
    if fallback_values is not None:
        if not fallback_values:
            issues.append(_issue("fallback_missing", f"{path}.fallbacks", "At least one pre-authorized fallback is required."))
        for index, fallback_value in enumerate(fallback_values):
            fallback_path = f"{path}.fallbacks[{index}]"
            fallback = _pair(fallback_value, fallback_path, issues)
            fallback_rank = _supported_pair(fallback, fallback_path, capabilities, issues)
            if fallback is None:
                continue
            fallbacks.append(fallback)
            if fallback == recommended:
                issues.append(_issue("fallback_duplicates_recommendation", fallback_path, "Fallback must be an alternative pair."))
            if recommended_rank is not None and fallback_rank is not None and (
                fallback_rank[0] < recommended_rank[0] or fallback_rank[1] < recommended_rank[1]
            ):
                issues.append(_issue("fallback_weaker", fallback_path, "Fallback must be same-or-stronger in model class and effort."))
        encoded = [json.dumps(item, sort_keys=True) for item in fallbacks]
        if len(encoded) != len(set(encoded)):
            issues.append(_issue("duplicate_fallback", f"{path}.fallbacks", "Fallback pairs must be unique."))
    if recommended is None or rationale is None:
        return None
    return {"recommended": recommended, "fallbacks": fallbacks, "rationale": rationale}


def _floor(
    route: dict[str, Any] | None,
    model_floor: str,
    effort_floor: str,
    path: str,
    capabilities: dict[str, tuple[str, set[str]]],
    issues: list[dict[str, str]],
) -> None:
    if route is None:
        return
    recommended = route["recommended"]
    rank = _supported_pair(recommended, f"{path}.recommended", capabilities, [])
    if rank is not None and (
        rank[0] < MODEL_CLASS_RANK[model_floor] or rank[1] < EFFORT_RANK[effort_floor]
    ):
        issues.append(_issue("route_below_floor", path, f"Route must be at least {model_floor}/{effort_floor}."))


def _route_scaffold(
    record: Any, expected_run_id: str, issues: list[dict[str, str]]
) -> tuple[dict[str, Any] | None, dict[str, tuple[str, set[str]]]]:
    if not isinstance(record, dict):
        issues.append(_issue("invalid_type", "$", "Planning record must be an object."))
        return None, {}
    if record.get("schema_version") != 1:
        issues.append(_issue("schema_version_mismatch", "$.schema_version", "Planning schema version must be 1."))
    if record.get("run_id") != expected_run_id:
        issues.append(_issue("run_id_mismatch", "$.run_id", "Planning record belongs to another run."))
    evidence = _string_list(record.get("capability_evidence"), "$.capability_evidence", issues)
    capabilities = _capabilities(record, issues)
    if evidence is None:
        evidence = []
    return record, capabilities


def _planning_task(
    record: dict[str, Any],
    capabilities: dict[str, tuple[str, set[str]]],
    issues: list[dict[str, str]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    task = _object(record.get("planning_task"), "$.planning_task", {"id", "generation", "route"}, issues)
    if task is None:
        return None, None
    _text(task.get("id"), "$.planning_task.id", issues)
    generation = task.get("generation")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        issues.append(_issue("invalid_generation", "$.planning_task.generation", "Generation must be a positive integer."))
    route = _route(task.get("route"), "$.planning_task.route", capabilities, issues)
    scope = record.get("scope")
    if scope == "bounded":
        _floor(route, "reliable", "xhigh", "$.planning_task.route", capabilities, issues)
    elif scope == "broad_or_ambiguous":
        _floor(route, "strongest", "max", "$.planning_task.route", capabilities, issues)
    else:
        issues.append(_issue("invalid_scope", "$.scope", "Scope must be bounded or broad_or_ambiguous."))
    return task, route


def _graph_order_issues(ids: list[str], blockers: dict[str, list[str]], path: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    positions = {item: index for index, item in enumerate(ids)}
    for item, dependencies in blockers.items():
        for dependency in dependencies:
            if dependency not in positions:
                issues.append(_issue("unknown_blocker", f"{path}.{item}", f"Unknown blocker {dependency}."))
            elif dependency == item:
                issues.append(_issue("self_blocker", f"{path}.{item}", "An item cannot block itself."))
            elif positions[dependency] >= positions[item]:
                issues.append(_issue("blocker_not_ordered", f"{path}.{item}", "Blockers must precede the blocked item."))
    return issues


def _record_issues(record: Any, expected_run_id: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    top_fields = {
        "schema_version",
        "run_id",
        "scope",
        "capability_evidence",
        "supported_routes",
        "planning_task",
        "ownership",
        "requirements",
        "grill_rounds",
        "frontier_empty",
        "specs",
        "release_train",
        "code_read_only",
        "handoff_evidence",
    }
    obj = _object(record, "$", top_fields, issues)
    if obj is None:
        return _sorted(issues)
    record_obj, capabilities = _route_scaffold(obj, expected_run_id, issues)
    assert record_obj is not None
    planning_task, _ = _planning_task(record_obj, capabilities, issues)
    planning_id = planning_task.get("id") if planning_task else None

    ownership = _object(record_obj.get("ownership"), "$.ownership", {"grill", "specs", "tickets", "routing"}, issues)
    if ownership is not None and planning_id:
        for phase in sorted(ownership):
            if ownership[phase] != planning_id:
                issues.append(_issue("planning_ownership_drift", f"$.ownership.{phase}", "Every planning phase must be owned by the one planning task."))

    requirements = _string_list(record_obj.get("requirements"), "$.requirements", issues) or []
    rounds = _list(record_obj.get("grill_rounds"), "$.grill_rounds", issues)
    if rounds is not None:
        if not rounds:
            issues.append(_issue("grill_missing", "$.grill_rounds", "At least one Grill round is required."))
        for round_index, round_value in enumerate(rounds):
            round_path = f"$.grill_rounds[{round_index}]"
            round_obj = _object(
                round_value,
                round_path,
                {"round", "questions", "commentary_evidence", "acceptance_source", "acceptance_evidence", "planner_resume_evidence"},
                issues,
            )
            if round_obj is None:
                continue
            if round_obj.get("round") != round_index + 1:
                issues.append(_issue("round_number_invalid", f"{round_path}.round", "Rounds must be numbered consecutively from 1."))
            for field in ("commentary_evidence", "acceptance_evidence", "planner_resume_evidence"):
                _string_list(round_obj.get(field), f"{round_path}.{field}", issues)
            if round_obj.get("acceptance_source") != "implement-needs-standing-authorization":
                issues.append(_issue("manual_confirmation_required", f"{round_path}.acceptance_source", "Grill defaults must be accepted by standing authorization."))
            questions = _list(round_obj.get("questions"), f"{round_path}.questions", issues)
            if questions is None:
                continue
            if not questions:
                issues.append(_issue("empty_frontier_round", f"{round_path}.questions", "A Grill round must contain its complete frontier."))
            for question_index, question_value in enumerate(questions):
                question_path = f"{round_path}.questions[{question_index}]"
                question = _object(question_value, question_path, {"number", "question", "recommendation", "rationale"}, issues)
                if question is None:
                    continue
                if question.get("number") != question_index + 1:
                    issues.append(_issue("question_number_invalid", f"{question_path}.number", "Questions must be numbered consecutively within the round."))
                for field in ("question", "recommendation", "rationale"):
                    content = _text(question.get(field), f"{question_path}.{field}", issues)
                    if content is not None and HAN_RE.search(content) is None:
                        issues.append(_issue("relay_not_chinese", f"{question_path}.{field}", "Relayed Grill content must be Chinese."))
    if record_obj.get("frontier_empty") is not True:
        issues.append(_issue("grill_incomplete", "$.frontier_empty", "The planning handoff requires an empty Grill frontier."))

    spec_values = _list(record_obj.get("specs"), "$.specs", issues)
    spec_ids: list[str] = []
    spec_blockers: dict[str, list[str]] = {}
    requirement_owners: Counter[str] = Counter()
    checkpoints: list[str] = []
    if spec_values is not None:
        if not spec_values:
            issues.append(_issue("specs_missing", "$.specs", "At least one scoped SPEC is required."))
        for spec_index, spec_value in enumerate(spec_values):
            spec_path = f"$.specs[{spec_index}]"
            spec = _object(
                spec_value,
                spec_path,
                {"id", "artifact", "requirements", "blocked_by", "auto_approval", "tickets", "ticket_self_check", "difficulty", "route", "checkpoint"},
                issues,
            )
            if spec is None:
                continue
            spec_id = _text(spec.get("id"), f"{spec_path}.id", issues)
            _text(spec.get("artifact"), f"{spec_path}.artifact", issues)
            owned = _string_list(spec.get("requirements"), f"{spec_path}.requirements", issues) or []
            for requirement in owned:
                requirement_owners[requirement] += 1
            blocked_by = _string_list(spec.get("blocked_by"), f"{spec_path}.blocked_by", issues, allow_empty=True) or []
            if spec_id:
                spec_ids.append(spec_id)
                spec_blockers[spec_id] = blocked_by

            approval = _object(spec.get("auto_approval"), f"{spec_path}.auto_approval", set(AUTO_APPROVAL), issues)
            if approval is not None:
                for field, expected in AUTO_APPROVAL.items():
                    if approval.get(field) != expected:
                        issues.append(_issue("auto_approval_invalid", f"{spec_path}.auto_approval.{field}", f"Expected {expected!r}."))

            ticket_values = _list(spec.get("tickets"), f"{spec_path}.tickets", issues)
            ticket_ids: list[str] = []
            ticket_blockers: dict[str, list[str]] = {}
            if ticket_values is not None:
                if not ticket_values:
                    issues.append(_issue("tickets_missing", f"{spec_path}.tickets", "Every SPEC requires tickets."))
                for ticket_index, ticket_value in enumerate(ticket_values):
                    ticket_path = f"{spec_path}.tickets[{ticket_index}]"
                    ticket = _object(ticket_value, ticket_path, {"id", "artifact", "blocked_by", "vertical_slice"}, issues)
                    if ticket is None:
                        continue
                    ticket_id = _text(ticket.get("id"), f"{ticket_path}.id", issues)
                    _text(ticket.get("artifact"), f"{ticket_path}.artifact", issues)
                    _text(ticket.get("vertical_slice"), f"{ticket_path}.vertical_slice", issues)
                    ticket_dependencies = _string_list(ticket.get("blocked_by"), f"{ticket_path}.blocked_by", issues, allow_empty=True) or []
                    if ticket_id:
                        ticket_ids.append(ticket_id)
                        ticket_blockers[ticket_id] = ticket_dependencies
                if len(ticket_ids) != len(set(ticket_ids)):
                    issues.append(_issue("duplicate_ticket_id", f"{spec_path}.tickets", "Ticket IDs must be unique within a SPEC."))
                issues.extend(_graph_order_issues(ticket_ids, ticket_blockers, f"{spec_path}.ticket_blockers"))

            self_check = _object(spec.get("ticket_self_check"), f"{spec_path}.ticket_self_check", {"granularity", "blocking_edges", "acyclic", "evidence"}, issues)
            if self_check is not None:
                for field in ("granularity", "blocking_edges", "acyclic"):
                    if self_check.get(field) != "pass":
                        issues.append(_issue("ticket_self_check_failed", f"{spec_path}.ticket_self_check.{field}", "Ticket self-check must pass."))
                _string_list(self_check.get("evidence"), f"{spec_path}.ticket_self_check.evidence", issues)

            difficulty = spec.get("difficulty")
            if difficulty not in DIFFICULTY_FLOOR:
                issues.append(_issue("invalid_difficulty", f"{spec_path}.difficulty", "Unknown implementation difficulty."))
            spec_route = _route(spec.get("route"), f"{spec_path}.route", capabilities, issues)
            if difficulty in DIFFICULTY_FLOOR:
                _floor(spec_route, *DIFFICULTY_FLOOR[difficulty], f"{spec_path}.route", capabilities, issues)
            checkpoint = _text(spec.get("checkpoint"), f"{spec_path}.checkpoint", issues)
            if checkpoint:
                checkpoints.append(checkpoint)

    if len(spec_ids) != len(set(spec_ids)):
        issues.append(_issue("duplicate_spec_id", "$.specs", "SPEC IDs must be unique."))
    issues.extend(_graph_order_issues(spec_ids, spec_blockers, "$.spec_blockers"))
    for requirement in requirements:
        if requirement_owners[requirement] != 1:
            issues.append(_issue("requirement_partition_invalid", "$.specs", f"Requirement {requirement} must belong to exactly one SPEC."))
    for requirement in sorted(set(requirement_owners) - set(requirements)):
        issues.append(_issue("unknown_requirement", "$.specs", f"SPEC owns undeclared requirement {requirement}."))

    train = _object(
        record_obj.get("release_train"),
        "$.release_train",
        {"owners", "repositories", "acceptance_scopes", "public_contract_specs", "environment_specs", "baselines", "checkpoints"},
        issues,
    )
    if train is not None:
        for field in ("owners", "repositories", "acceptance_scopes", "baselines"):
            _string_list(train.get(field), f"$.release_train.{field}", issues)
        for field in ("public_contract_specs", "environment_specs"):
            flagged = _string_list(train.get(field), f"$.release_train.{field}", issues, allow_empty=True) or []
            for spec_id in flagged:
                if spec_id not in spec_ids:
                    issues.append(_issue("unknown_train_spec", f"$.release_train.{field}", f"Unknown SPEC {spec_id}."))
        train_checkpoints = _string_list(train.get("checkpoints"), "$.release_train.checkpoints", issues) or []
        if set(train_checkpoints) != set(checkpoints):
            issues.append(_issue("checkpoint_mismatch", "$.release_train.checkpoints", "Release-train checkpoints must match every SPEC checkpoint."))

    code_check = _object(
        record_obj.get("code_read_only"),
        "$.code_read_only",
        {"product_test_tree_before_sha256", "product_test_tree_after_sha256", "changed_product_or_test_paths", "evidence"},
        issues,
    )
    if code_check is not None:
        before = code_check.get("product_test_tree_before_sha256")
        after = code_check.get("product_test_tree_after_sha256")
        for field, value in (("product_test_tree_before_sha256", before), ("product_test_tree_after_sha256", after)):
            if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
                issues.append(_issue("invalid_sha256", f"$.code_read_only.{field}", "Expected a lowercase SHA-256 digest."))
        if before != after:
            issues.append(_issue("planning_code_changed", "$.code_read_only", "Planning changed the product/test tree."))
        changed = _string_list(code_check.get("changed_product_or_test_paths"), "$.code_read_only.changed_product_or_test_paths", issues, allow_empty=True)
        if changed:
            issues.append(_issue("planning_code_changed", "$.code_read_only.changed_product_or_test_paths", "Planning must not change product or test paths."))
        _string_list(code_check.get("evidence"), "$.code_read_only.evidence", issues)

    _string_list(record_obj.get("handoff_evidence"), "$.handoff_evidence", issues)
    return _sorted(issues)


def _controller_issues(
    state: Any, record: dict[str, Any]
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not isinstance(state, dict):
        return [_issue("invalid_type", "$controller", "Controller state must be an object.")]
    if state.get("run_id") != record.get("run_id"):
        issues.append(_issue("controller_run_id_mismatch", "$controller.run_id", "Controller and planning records must share a run ID."))
    tasks = state.get("child_tasks")
    if not isinstance(tasks, list):
        issues.append(_issue("invalid_type", "$controller.child_tasks", "Controller child_tasks must be an array."))
        return _sorted(issues)
    planners = [task for task in tasks if isinstance(task, dict) and task.get("kind") == "planning"]
    if len(planners) != 1:
        issues.append(_issue("planning_task_count", "$controller.child_tasks", "Exactly one planning task must exist."))
        return _sorted(issues)
    planner = planners[0]
    expected_id = record.get("planning_task", {}).get("id") if isinstance(record.get("planning_task"), dict) else None
    if planner.get("id") != expected_id:
        issues.append(_issue("planning_task_id_mismatch", "$controller.child_tasks", "Controller planning task must match the planning record."))
    if planner.get("lifecycle") != "archived":
        issues.append(_issue("planning_not_archived", "$controller.child_tasks", "Planning must be verified and archived before implementation dispatch."))
    generation = record.get("planning_task", {}).get("generation") if isinstance(record.get("planning_task"), dict) else None
    spec_tasks = [task for task in tasks if isinstance(task, dict) and task.get("kind") == "spec"]
    if generation == 1 and spec_tasks:
        issues.append(_issue("implementation_preceded_initial_planning_gate", "$controller.child_tasks", "Initial planning must pass before any SPEC task is created."))
    if generation != 1 and any(task.get("lifecycle") != "archived" for task in spec_tasks):
        issues.append(_issue("implementation_active_during_planning_revision", "$controller.child_tasks", "Existing SPEC tasks must be archived before revising planning."))
    for task in tasks:
        if isinstance(task, dict) and task is not planner and task.get("lifecycle") != "archived":
            issues.append(_issue("other_task_not_archived", "$controller.child_tasks", "No other child may remain active at the planning dispatch gate."))
            break
    return _sorted(issues)


def _readback_issues(
    record: dict[str, Any],
    readback: Any,
    expected_run_id: str,
    target: str,
    capabilities: dict[str, tuple[str, set[str]]],
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    obj = _object(
        readback,
        "$readback",
        {"schema_version", "run_id", "task_id", "target", "requested", "applied", "selection", "substitution_reason", "readback_evidence"},
        issues,
    )
    if obj is None:
        return _sorted(issues)
    if obj.get("schema_version") != 1:
        issues.append(_issue("schema_version_mismatch", "$readback.schema_version", "Readback schema version must be 1."))
    if obj.get("run_id") != expected_run_id or record.get("run_id") != expected_run_id:
        issues.append(_issue("run_id_mismatch", "$readback.run_id", "Routing artifacts belong to another run."))
    if obj.get("target") != target:
        issues.append(_issue("route_target_mismatch", "$readback.target", "Readback target does not match the requested gate."))
    _text(obj.get("task_id"), "$readback.task_id", issues)
    _string_list(obj.get("readback_evidence"), "$readback.readback_evidence", issues)
    requested = _pair(obj.get("requested"), "$readback.requested", issues)
    applied = _pair(obj.get("applied"), "$readback.applied", issues)
    _supported_pair(requested, "$readback.requested", capabilities, issues)
    _supported_pair(applied, "$readback.applied", capabilities, issues)

    route: dict[str, Any] | None = None
    expected_task_id: str | None = None
    if target == "planning":
        task, route = _planning_task(record, capabilities, issues)
        expected_task_id = task.get("id") if task else None
    else:
        specs = record.get("specs")
        match = next((spec for spec in specs if isinstance(spec, dict) and spec.get("id") == target), None) if isinstance(specs, list) else None
        if match is None:
            issues.append(_issue("route_target_unknown", "$readback.target", "Target SPEC is absent from the planning record."))
        else:
            route = _route(match.get("route"), f"$.specs[{target}].route", capabilities, issues)
    if expected_task_id is not None and obj.get("task_id") != expected_task_id:
        issues.append(_issue("planning_task_id_mismatch", "$readback.task_id", "Planning readback names a different task."))

    selection = obj.get("selection")
    reason = obj.get("substitution_reason")
    if selection not in {"recommended", "fallback"}:
        issues.append(_issue("invalid_route_selection", "$readback.selection", "Selection must be recommended or fallback."))
    elif route is not None and requested is not None:
        if selection == "recommended":
            if requested != route["recommended"]:
                issues.append(_issue("recommendation_not_requested", "$readback.requested", "Recommended selection must request the locked recommendation."))
            if reason is not None:
                issues.append(_issue("unexpected_substitution_reason", "$readback.substitution_reason", "Exact recommendation needs no substitution reason."))
        else:
            if requested not in route["fallbacks"]:
                issues.append(_issue("fallback_not_preapproved", "$readback.requested", "Requested fallback was not locked by planning."))
            _text(reason, "$readback.substitution_reason", issues)
    if requested is not None and applied is not None and requested != applied:
        issues.append(_issue("silent_route_drift", "$readback.applied", "Applied model and effort must exactly match the explicit request."))
    return _sorted(issues)


def _decision_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256(canonical)


def evaluate_route(
    record_path: Path,
    readback_path: Path,
    expected_run_id: str,
    target: str,
) -> tuple[dict[str, Any], int]:
    issues: list[dict[str, str]] = []
    record, record_raw = _read_json(record_path, "planning_record", issues)
    readback, readback_raw = _read_json(readback_path, "route_readback", issues)
    capabilities: dict[str, tuple[str, set[str]]] = {}
    if isinstance(record, dict):
        _, capabilities = _route_scaffold(record, expected_run_id, issues)
        if target == "planning":
            _planning_task(record, capabilities, issues)
        if readback is not None:
            issues.extend(_readback_issues(record, readback, expected_run_id, target, capabilities))
    issues = _sorted(issues)
    if issues:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "decision": "reject",
            "gate": "task_route",
            "target": target,
            "reasons": issues,
            "next_action": {
                "kind": "hold_task_at_bootstrap",
                "target": readback.get("task_id") if isinstance(readback, dict) else target,
                "instruction": "Keep the task code-read-only, repair or explicitly select a locked fallback, then capture a fresh applied model/effort readback.",
            },
        }
        payload["decision_sha256"] = _decision_hash(payload)
        return payload, 1
    assert isinstance(record, dict) and isinstance(readback, dict)
    assert record_raw is not None and readback_raw is not None
    payload = {
        "schema_version": 1,
        "decision": "allow",
        "gate": "task_route",
        "run_id": expected_run_id,
        "target": target,
        "task_id": readback["task_id"],
        "selection": readback["selection"],
        "applied": readback["applied"],
        "planning_record_sha256": _sha256(record_raw),
        "route_readback_sha256": _sha256(readback_raw),
    }
    payload["receipt_sha256"] = _decision_hash(payload)
    return payload, 0


def evaluate_handoff(
    record_path: Path,
    state_path: Path,
    planning_readback_path: Path,
    expected_run_id: str,
) -> tuple[dict[str, Any], int]:
    issues: list[dict[str, str]] = []
    record, record_raw = _read_json(record_path, "planning_record", issues)
    state, state_raw = _read_json(state_path, "controller_state", issues)
    readback, readback_raw = _read_json(planning_readback_path, "route_readback", issues)
    capabilities: dict[str, tuple[str, set[str]]] = {}
    if record is not None:
        issues.extend(_record_issues(record, expected_run_id))
    if isinstance(record, dict):
        _, capabilities = _route_scaffold(record, expected_run_id, [])
        issues.extend(_controller_issues(state, record))
        if readback is not None:
            issues.extend(_readback_issues(record, readback, expected_run_id, "planning", capabilities))
    issues = _sorted(issues)
    if issues:
        planning_id = record.get("planning_task", {}).get("id") if isinstance(record, dict) and isinstance(record.get("planning_task"), dict) else "planning"
        payload: dict[str, Any] = {
            "schema_version": 1,
            "decision": "reject",
            "gate": "planning_handoff",
            "reasons": issues,
            "next_action": {
                "kind": "return_planning_handoff",
                "target": planning_id,
                "instruction": "Unarchive the same planning task if needed, return these deterministic failures, and rerun the gate after a corrected handoff.",
            },
        }
        payload["decision_sha256"] = _decision_hash(payload)
        return payload, 1
    assert isinstance(record, dict) and isinstance(state, dict) and isinstance(readback, dict)
    assert record_raw is not None and state_raw is not None and readback_raw is not None
    question_count = sum(len(item["questions"]) for item in record["grill_rounds"])
    payload = {
        "schema_version": 1,
        "decision": "allow",
        "gate": "planning_handoff",
        "run_id": expected_run_id,
        "planning_task_id": record["planning_task"]["id"],
        "generation": record["planning_task"]["generation"],
        "spec_ids": [spec["id"] for spec in record["specs"]],
        "grill_question_count": question_count,
        "planning_record_sha256": _sha256(record_raw),
        "controller_state_sha256": _sha256(state_raw),
        "route_readback_sha256": _sha256(readback_raw),
    }
    payload["receipt_sha256"] = _decision_hash(payload)
    return payload, 0


def _serialize(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    route = subparsers.add_parser("route", help="Validate requested versus applied task routing")
    route.add_argument("--record", required=True, type=Path)
    route.add_argument("--readback", required=True, type=Path)
    route.add_argument("--expected-run-id", required=True)
    route.add_argument("--target", required=True)
    route.add_argument("--receipt", type=Path)
    handoff = subparsers.add_parser("handoff", help="Validate the complete planning handoff and dispatch boundary")
    handoff.add_argument("--record", required=True, type=Path)
    handoff.add_argument("--controller-state", required=True, type=Path)
    handoff.add_argument("--planning-readback", required=True, type=Path)
    handoff.add_argument("--expected-run-id", required=True)
    handoff.add_argument("--receipt", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "route":
        payload, exit_code = evaluate_route(args.record, args.readback, args.expected_run_id, args.target)
    else:
        payload, exit_code = evaluate_handoff(args.record, args.controller_state, args.planning_readback, args.expected_run_id)
    serialized = _serialize(payload)
    if args.receipt is not None:
        try:
            _write_atomic(args.receipt, serialized)
        except OSError as exc:
            payload = {
                "schema_version": 1,
                "decision": "reject",
                "gate": payload.get("gate"),
                "reasons": [_issue("receipt_unwritable", str(args.receipt), f"Cannot persist receipt: {exc.__class__.__name__}.")],
                "next_action": {
                    "kind": "repair_planning_receipt",
                    "target": str(args.receipt),
                    "instruction": "Repair receipt persistence and rerun the same planning gate.",
                },
            }
            payload["decision_sha256"] = _decision_hash(payload)
            serialized = _serialize(payload)
            exit_code = 1
    print(serialized, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
