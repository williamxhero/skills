#!/usr/bin/env python3
"""Fail-closed planning and task-routing gates for Implement Needs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

ROUTE_POLICY_DIR = Path(__file__).resolve().parents[2] / "route-codex-task" / "scripts"
sys.path.insert(0, str(ROUTE_POLICY_DIR))
from route_policy import (
    DIFFICULTY_FLOOR,
    MODEL_RANK,
    POLICY_ERROR,
    build_route_receipt,
    decision_hash,
    validate_capabilities,
    validate_floor,
    validate_pair,
    validate_route,
    validate_task_readback,
)
from route_policy import EFFORT_RANK as _EFFORT_RANK

EFFORT_RANK = _EFFORT_RANK
MODEL_CLASS_RANK = MODEL_RANK
MODEL_POLICY_ERROR = POLICY_ERROR
_decision_hash = decision_hash
_pair = validate_pair
_shared_route = validate_route

AUTO_APPROVAL = {
    "confirmation_mode": "auto_approve",
    "approval_source": "implement-needs",
    "approval_text": "同意",
    "spec_state": "auto_approved",
    "approval_provenance": "controller_decision",
}
HAN_RE = re.compile(r"[\u3400-\u9fff]")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
CHECKPOINT_SIZE = 10


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _sorted(issues: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(
        issues, key=lambda item: (item["path"], item["code"], item["message"])
    )


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
        issues.append(
            _issue("missing_field", f"{path}.{field}", "Required field is missing.")
        )
    for field in sorted(set(value) - fields):
        issues.append(
            _issue("unknown_field", f"{path}.{field}", "Unknown field is not allowed.")
        )
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


def _checkpoint_plan(spec_ids: list[str]) -> list[dict[str, Any]]:
    checkpoints: list[dict[str, Any]] = []
    for start in range(0, len(spec_ids), CHECKPOINT_SIZE):
        members = spec_ids[start : start + CHECKPOINT_SIZE]
        if not members:
            continue
        end = start + len(members)
        checkpoints.append(
            {
                "id": f"checkpoint-{end}",
                "start_spec_index": start + 1,
                "end_spec_index": end,
                "specs": members,
                "final_tail": len(members) < CHECKPOINT_SIZE,
            }
        )
    return checkpoints


def _checkpoint_lookup(spec_ids: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for checkpoint in _checkpoint_plan(spec_ids):
        for spec_id in checkpoint["specs"]:
            result[spec_id] = checkpoint["id"]
    return result


def _capabilities(
    record: dict[str, Any], issues: list[dict[str, str]]
) -> dict[str, set[str]]:
    return validate_capabilities(record.get("supported_routes"), "$.supported_routes", issues)


def _floor(
    route: dict[str, Any] | None,
    model_floor: str,
    effort_floor: str,
    path: str,
    capabilities: dict[str, set[str]],
    issues: list[dict[str, str]],
) -> None:
    validate_floor(
        route["recommended"] if route is not None else None,
        (model_floor, effort_floor),
        path,
        capabilities,
        issues,
    )


def _route_scaffold(
    record: Any, expected_run_id: str, issues: list[dict[str, str]]
) -> tuple[dict[str, Any] | None, dict[str, set[str]]]:
    if not isinstance(record, dict):
        issues.append(_issue("invalid_type", "$", "Planning record must be an object."))
        return None, {}
    if record.get("schema_version") != 1:
        issues.append(
            _issue(
                "schema_version_mismatch",
                "$.schema_version",
                "Planning schema version must be 1.",
            )
        )
    if record.get("run_id") != expected_run_id:
        issues.append(
            _issue(
                "run_id_mismatch", "$.run_id", "Planning record belongs to another run."
            )
        )
    evidence = _string_list(
        record.get("capability_evidence"), "$.capability_evidence", issues
    )
    capabilities = _capabilities(record, issues)
    if evidence is None:
        evidence = []
    return record, capabilities


def _planning_task(
    record: dict[str, Any],
    capabilities: dict[str, set[str]],
    issues: list[dict[str, str]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    task = _object(
        record.get("planning_task"),
        "$.planning_task",
        {"id", "generation", "route"},
        issues,
    )
    if task is None:
        return None, None
    _text(task.get("id"), "$.planning_task.id", issues)
    generation = task.get("generation")
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 1
    ):
        issues.append(
            _issue(
                "invalid_generation",
                "$.planning_task.generation",
                "Generation must be a positive integer.",
            )
        )
    xhigh_evidence = (
        _string_list(
            record.get("planning_xhigh_evidence"),
            "$.planning_xhigh_evidence",
            issues,
            allow_empty=True,
        )
        or []
    )
    route = _shared_route(
        task.get("route"),
        "$.planning_task.route",
        capabilities,
        issues,
        xhigh_evidence=xhigh_evidence,
    )
    scope = record.get("scope")
    if scope == "bounded" or scope == "broad_or_ambiguous":
        _floor(
            route,
            "gpt-5.6-sol",
            "high",
            "$.planning_task.route",
            capabilities,
            issues,
        )
    else:
        issues.append(
            _issue(
                "invalid_scope",
                "$.scope",
                "Scope must be bounded or broad_or_ambiguous.",
            )
        )
    return task, route


def _graph_order_issues(
    ids: list[str], blockers: dict[str, list[str]], path: str
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    positions = {item: index for index, item in enumerate(ids)}
    for item, dependencies in blockers.items():
        for dependency in dependencies:
            if dependency not in positions:
                issues.append(
                    _issue(
                        "unknown_blocker",
                        f"{path}.{item}",
                        f"Unknown blocker {dependency}.",
                    )
                )
            elif dependency == item:
                issues.append(
                    _issue(
                        "self_blocker", f"{path}.{item}", "An item cannot block itself."
                    )
                )
            elif positions[dependency] >= positions[item]:
                issues.append(
                    _issue(
                        "blocker_not_ordered",
                        f"{path}.{item}",
                        "Blockers must precede the blocked item.",
                    )
                )
    return issues


def _record_issues(record: Any, expected_run_id: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if MODEL_POLICY_ERROR is not None:
        issues.append(
            _issue(
                "model_policy_invalid",
                "$.model_policy",
                f"Implement Needs model policy is invalid: {MODEL_POLICY_ERROR}.",
            )
        )
    top_fields = {
        "schema_version",
        "run_id",
        "scope",
        "capability_evidence",
        "supported_routes",
        "planning_xhigh_evidence",
        "planning_task",
        "ownership",
        "requirements",
        "grill_rounds",
        "frontier_empty",
        "umbrella_spec",
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

    ownership = _object(
        record_obj.get("ownership"),
        "$.ownership",
        {"grill", "specs", "tickets", "routing"},
        issues,
    )
    if ownership is not None and planning_id:
        for phase in sorted(ownership):
            if ownership[phase] != planning_id:
                issues.append(
                    _issue(
                        "planning_ownership_drift",
                        f"$.ownership.{phase}",
                        "Every planning phase must be owned by the one planning task.",
                    )
                )

    requirements = (
        _string_list(record_obj.get("requirements"), "$.requirements", issues) or []
    )
    rounds = _list(record_obj.get("grill_rounds"), "$.grill_rounds", issues)
    if rounds is not None:
        if not rounds:
            issues.append(
                _issue(
                    "grill_missing",
                    "$.grill_rounds",
                    "At least one Grill round is required.",
                )
            )
        for round_index, round_value in enumerate(rounds):
            round_path = f"$.grill_rounds[{round_index}]"
            round_obj = _object(
                round_value,
                round_path,
                {
                    "round",
                    "questions",
                    "commentary_evidence",
                    "acceptance_source",
                    "acceptance_command",
                    "acceptance_evidence",
                    "planner_resume_evidence",
                },
                issues,
            )
            if round_obj is None:
                continue
            if round_obj.get("round") != round_index + 1:
                issues.append(
                    _issue(
                        "round_number_invalid",
                        f"{round_path}.round",
                        "Rounds must be numbered consecutively from 1.",
                    )
                )
            for field in (
                "commentary_evidence",
                "acceptance_evidence",
                "planner_resume_evidence",
            ):
                _string_list(round_obj.get(field), f"{round_path}.{field}", issues)
            if (
                round_obj.get("acceptance_source")
                != "implement-needs-standing-authorization"
            ):
                issues.append(
                    _issue(
                        "manual_confirmation_required",
                        f"{round_path}.acceptance_source",
                        "Grill defaults must be accepted by standing authorization.",
                    )
                )
            if round_obj.get("acceptance_command") != "全部采用推荐选项/答案":
                issues.append(_issue("verbose_grill_acceptance", f"{round_path}.acceptance_command", "The controller must accept a Grill frontier with only the compact canonical command."))
            questions = _list(
                round_obj.get("questions"), f"{round_path}.questions", issues
            )
            if questions is None:
                continue
            if not questions:
                issues.append(
                    _issue(
                        "empty_frontier_round",
                        f"{round_path}.questions",
                        "A Grill round must contain its complete frontier.",
                    )
                )
            for question_index, question_value in enumerate(questions):
                question_path = f"{round_path}.questions[{question_index}]"
                question = _object(
                    question_value,
                    question_path,
                    {"number", "question", "recommendation", "rationale"},
                    issues,
                )
                if question is None:
                    continue
                if question.get("number") != question_index + 1:
                    issues.append(
                        _issue(
                            "question_number_invalid",
                            f"{question_path}.number",
                            "Questions must be numbered consecutively within the round.",
                        )
                    )
                for field in ("question", "recommendation", "rationale"):
                    content = _text(
                        question.get(field), f"{question_path}.{field}", issues
                    )
                    if content is not None and HAN_RE.search(content) is None:
                        issues.append(
                            _issue(
                                "relay_not_chinese",
                                f"{question_path}.{field}",
                                "Relayed Grill content must be Chinese.",
                            )
                        )
    if record_obj.get("frontier_empty") is not True:
        issues.append(
            _issue(
                "grill_incomplete",
                "$.frontier_empty",
                "The planning handoff requires an empty Grill frontier.",
            )
        )

    umbrella = _object(record_obj.get("umbrella_spec"), "$.umbrella_spec", {"id", "artifact"}, issues)
    umbrella_id = None
    if umbrella is not None:
        umbrella_id = _text(umbrella.get("id"), "$.umbrella_spec.id", issues)
        _text(umbrella.get("artifact"), "$.umbrella_spec.artifact", issues)

    spec_values = _list(record_obj.get("specs"), "$.specs", issues)
    spec_ids: list[str] = []
    spec_blockers: dict[str, list[str]] = {}
    spec_ownership: dict[str, dict[str, list[str]]] = {}
    requirement_owners: Counter[str] = Counter()
    checkpoints: list[str] = []
    if spec_values is not None:
        if not spec_values:
            issues.append(
                _issue(
                    "specs_missing", "$.specs", "At least one scoped SPEC is required."
                )
            )
        for spec_index, spec_value in enumerate(spec_values):
            spec_path = f"$.specs[{spec_index}]"
            spec = _object(
                spec_value,
                spec_path,
                {
                    "id",
                    "artifact",
                    "parent_issue",
                    "requirements",
                    "blocked_by",
                    "auto_approval",
                    "tickets",
                    "ticket_self_check",
                    "difficulty",
                    "xhigh_evidence",
                    "route",
                    "checkpoint",
                    "owners",
                    "repositories",
                },
                issues,
            )
            if spec is None:
                continue
            spec_id = _text(spec.get("id"), f"{spec_path}.id", issues)
            _text(spec.get("artifact"), f"{spec_path}.artifact", issues)
            parent_issue = _object(spec.get("parent_issue"), f"{spec_path}.parent_issue", {"parent_id", "evidence"}, issues)
            if parent_issue is not None:
                parent_id = _text(parent_issue.get("parent_id"), f"{spec_path}.parent_issue.parent_id", issues)
                evidence = _string_list(parent_issue.get("evidence"), f"{spec_path}.parent_issue.evidence", issues)
                if parent_id and umbrella_id and parent_id != umbrella_id:
                    issues.append(_issue("parent_issue_mismatch", f"{spec_path}.parent_issue.parent_id", "Every child SPEC must use the umbrella SPEC as its GitHub Parent issue."))
                if evidence == []:
                    issues.append(_issue("parent_issue_evidence_missing", f"{spec_path}.parent_issue.evidence", "GitHub Parent issue readback evidence is required."))
            owned = (
                _string_list(
                    spec.get("requirements"), f"{spec_path}.requirements", issues
                )
                or []
            )
            for requirement in owned:
                requirement_owners[requirement] += 1
            blocked_by = (
                _string_list(
                    spec.get("blocked_by"),
                    f"{spec_path}.blocked_by",
                    issues,
                    allow_empty=True,
                )
                or []
            )
            if spec_id:
                spec_ids.append(spec_id)
                spec_blockers[spec_id] = blocked_by
                spec_ownership[spec_id] = {
                    "owners": _string_list(
                        spec.get("owners"), f"{spec_path}.owners", issues
                    )
                    or [],
                    "repositories": _string_list(
                        spec.get("repositories"), f"{spec_path}.repositories", issues
                    )
                    or [],
                }

            approval = _object(
                spec.get("auto_approval"),
                f"{spec_path}.auto_approval",
                set(AUTO_APPROVAL),
                issues,
            )
            if approval is not None:
                for field, expected in AUTO_APPROVAL.items():
                    if approval.get(field) != expected:
                        issues.append(
                            _issue(
                                "auto_approval_invalid",
                                f"{spec_path}.auto_approval.{field}",
                                f"Expected {expected!r}.",
                            )
                        )

            ticket_values = _list(spec.get("tickets"), f"{spec_path}.tickets", issues)
            ticket_ids: list[str] = []
            ticket_blockers: dict[str, list[str]] = {}
            if ticket_values is not None:
                if not ticket_values:
                    issues.append(
                        _issue(
                            "tickets_missing",
                            f"{spec_path}.tickets",
                            "Every SPEC requires tickets.",
                        )
                    )
                for ticket_index, ticket_value in enumerate(ticket_values):
                    ticket_path = f"{spec_path}.tickets[{ticket_index}]"
                    ticket = _object(
                        ticket_value,
                        ticket_path,
                        {"id", "artifact", "parent_issue", "blocked_by", "vertical_slice"},
                        issues,
                    )
                    if ticket is None:
                        continue
                    ticket_id = _text(ticket.get("id"), f"{ticket_path}.id", issues)
                    _text(ticket.get("artifact"), f"{ticket_path}.artifact", issues)
                    ticket_parent = _object(ticket.get("parent_issue"), f"{ticket_path}.parent_issue", {"parent_id", "evidence"}, issues)
                    if ticket_parent is not None:
                        ticket_parent_id = _text(ticket_parent.get("parent_id"), f"{ticket_path}.parent_issue.parent_id", issues)
                        ticket_parent_evidence = _string_list(ticket_parent.get("evidence"), f"{ticket_path}.parent_issue.evidence", issues)
                        if ticket_parent_id and spec_id and ticket_parent_id != spec_id:
                            issues.append(_issue("ticket_parent_issue_mismatch", f"{ticket_path}.parent_issue.parent_id", "Every ticket must use its owning SPEC as its GitHub Parent issue."))
                        if ticket_parent_evidence == []:
                            issues.append(_issue("ticket_parent_issue_evidence_missing", f"{ticket_path}.parent_issue.evidence", "GitHub ticket Parent issue readback evidence is required."))
                    _text(
                        ticket.get("vertical_slice"),
                        f"{ticket_path}.vertical_slice",
                        issues,
                    )
                    ticket_dependencies = (
                        _string_list(
                            ticket.get("blocked_by"),
                            f"{ticket_path}.blocked_by",
                            issues,
                            allow_empty=True,
                        )
                        or []
                    )
                    if ticket_id:
                        ticket_ids.append(ticket_id)
                        ticket_blockers[ticket_id] = ticket_dependencies
                if len(ticket_ids) != len(set(ticket_ids)):
                    issues.append(
                        _issue(
                            "duplicate_ticket_id",
                            f"{spec_path}.tickets",
                            "Ticket IDs must be unique within a SPEC.",
                        )
                    )
                issues.extend(
                    _graph_order_issues(
                        ticket_ids, ticket_blockers, f"{spec_path}.ticket_blockers"
                    )
                )

            self_check = _object(
                spec.get("ticket_self_check"),
                f"{spec_path}.ticket_self_check",
                {"granularity", "blocking_edges", "acyclic", "evidence"},
                issues,
            )
            if self_check is not None:
                for field in ("granularity", "blocking_edges", "acyclic"):
                    if self_check.get(field) != "pass":
                        issues.append(
                            _issue(
                                "ticket_self_check_failed",
                                f"{spec_path}.ticket_self_check.{field}",
                                "Ticket self-check must pass.",
                            )
                        )
                _string_list(
                    self_check.get("evidence"),
                    f"{spec_path}.ticket_self_check.evidence",
                    issues,
                )

            difficulty = spec.get("difficulty")
            if difficulty not in DIFFICULTY_FLOOR:
                issues.append(
                    _issue(
                        "invalid_difficulty",
                        f"{spec_path}.difficulty",
                        "Unknown implementation difficulty.",
                    )
                )
            xhigh_evidence = (
                _string_list(
                    spec.get("xhigh_evidence"),
                    f"{spec_path}.xhigh_evidence",
                    issues,
                    allow_empty=True,
                )
                or []
            )
            spec_route = _shared_route(
                spec.get("route"),
                f"{spec_path}.route",
                capabilities,
                issues,
                xhigh_evidence=xhigh_evidence,
            )
            if difficulty in DIFFICULTY_FLOOR:
                _floor(
                    spec_route,
                    *DIFFICULTY_FLOOR[difficulty],
                    f"{spec_path}.route",
                    capabilities,
                    issues,
                )
            checkpoint = _text(
                spec.get("checkpoint"), f"{spec_path}.checkpoint", issues
            )
            if checkpoint:
                checkpoints.append(checkpoint)

    if len(spec_ids) != len(set(spec_ids)):
        issues.append(
            _issue("duplicate_spec_id", "$.specs", "SPEC IDs must be unique.")
        )
    if umbrella_id and umbrella_id in spec_ids:
        issues.append(_issue("umbrella_spec_in_implementation_set", "$.specs", "The umbrella SPEC is a container and must not enter the implementation SPEC set."))
    issues.extend(_graph_order_issues(spec_ids, spec_blockers, "$.spec_blockers"))
    for requirement in requirements:
        if requirement_owners[requirement] != 1:
            issues.append(
                _issue(
                    "requirement_partition_invalid",
                    "$.specs",
                    f"Requirement {requirement} must belong to exactly one SPEC.",
                )
            )
    for requirement in sorted(set(requirement_owners) - set(requirements)):
        issues.append(
            _issue(
                "unknown_requirement",
                "$.specs",
                f"SPEC owns undeclared requirement {requirement}.",
            )
        )
    expected_checkpoint_by_spec = _checkpoint_lookup(spec_ids)
    for spec_index, spec_value in enumerate(spec_values or []):
        if not isinstance(spec_value, dict):
            continue
        spec_id = spec_value.get("id")
        checkpoint = spec_value.get("checkpoint")
        if isinstance(spec_id, str) and spec_id in expected_checkpoint_by_spec:
            expected_checkpoint = expected_checkpoint_by_spec[spec_id]
            if checkpoint != expected_checkpoint:
                issues.append(
                    _issue(
                        "checkpoint_policy_mismatch",
                        f"$.specs[{spec_index}].checkpoint",
                        f"SPEC {spec_id} must belong to {expected_checkpoint}.",
                    )
                )

    train = _object(
        record_obj.get("release_train"),
        "$.release_train",
        {
            "owners",
            "repositories",
            "acceptance_scopes",
            "public_contract_specs",
            "environment_specs",
            "baselines",
            "checkpoint_size",
            "checkpoints",
        },
        issues,
    )
    if train is not None:
        train_owners = (
            _string_list(train.get("owners"), "$.release_train.owners", issues) or []
        )
        train_repositories = (
            _string_list(
                train.get("repositories"), "$.release_train.repositories", issues
            )
            or []
        )
        for field in ("acceptance_scopes", "baselines"):
            _string_list(train.get(field), f"$.release_train.{field}", issues)
        for field in ("public_contract_specs", "environment_specs"):
            flagged = (
                _string_list(
                    train.get(field),
                    f"$.release_train.{field}",
                    issues,
                    allow_empty=True,
                )
                or []
            )
            for spec_id in flagged:
                if spec_id not in spec_ids:
                    issues.append(
                        _issue(
                            "unknown_train_spec",
                            f"$.release_train.{field}",
                            f"Unknown SPEC {spec_id}.",
                        )
                    )
        if train.get("checkpoint_size") != CHECKPOINT_SIZE:
            issues.append(
                _issue(
                    "checkpoint_size_mismatch",
                    "$.release_train.checkpoint_size",
                    "Release train checkpoint_size must be the fixed value 10.",
                )
            )
        train_checkpoints = _list(
            train.get("checkpoints"), "$.release_train.checkpoints", issues
        )
        if train_checkpoints is not None:
            expected_checkpoints = _checkpoint_plan(spec_ids)
            if len(train_checkpoints) != len(expected_checkpoints):
                issues.append(
                    _issue(
                        "checkpoint_mismatch",
                        "$.release_train.checkpoints",
                        "Release-train checkpoints must match the deterministic fixed-size plan.",
                    )
                )
            for index, checkpoint_value in enumerate(train_checkpoints):
                checkpoint_path = f"$.release_train.checkpoints[{index}]"
                checkpoint = _object(
                    checkpoint_value,
                    checkpoint_path,
                    {
                        "id",
                        "start_spec_index",
                        "end_spec_index",
                        "specs",
                        "final_tail",
                        "affected_owners",
                        "affected_repositories",
                    },
                    issues,
                )
                if checkpoint is None:
                    continue
                expected = (
                    expected_checkpoints[index]
                    if index < len(expected_checkpoints)
                    else None
                )
                checkpoint_id = _text(
                    checkpoint.get("id"), f"{checkpoint_path}.id", issues
                )
                members = (
                    _string_list(
                        checkpoint.get("specs"), f"{checkpoint_path}.specs", issues
                    )
                    or []
                )
                affected_owners = (
                    _string_list(
                        checkpoint.get("affected_owners"),
                        f"{checkpoint_path}.affected_owners",
                        issues,
                    )
                    or []
                )
                affected_repositories = (
                    _string_list(
                        checkpoint.get("affected_repositories"),
                        f"{checkpoint_path}.affected_repositories",
                        issues,
                    )
                    or []
                )
                for owner in affected_owners:
                    if owner not in train_owners:
                        issues.append(
                            _issue(
                                "unknown_checkpoint_owner",
                                f"{checkpoint_path}.affected_owners",
                                f"Unknown owner {owner}.",
                            )
                        )
                for repository in affected_repositories:
                    if repository not in train_repositories:
                        issues.append(
                            _issue(
                                "unknown_checkpoint_repository",
                                f"{checkpoint_path}.affected_repositories",
                                f"Unknown repository {repository}.",
                            )
                        )
                for field in ("start_spec_index", "end_spec_index"):
                    value = checkpoint.get(field)
                    if not isinstance(value, int) or isinstance(value, bool):
                        issues.append(
                            _issue(
                                "invalid_checkpoint_index",
                                f"{checkpoint_path}.{field}",
                                "Checkpoint indexes must be integers.",
                            )
                        )
                if not isinstance(checkpoint.get("final_tail"), bool):
                    issues.append(
                        _issue(
                            "invalid_checkpoint_tail",
                            f"{checkpoint_path}.final_tail",
                            "final_tail must be a boolean.",
                        )
                    )
                if expected is not None:
                    comparisons = {
                        "id": checkpoint_id,
                        "start_spec_index": checkpoint.get("start_spec_index"),
                        "end_spec_index": checkpoint.get("end_spec_index"),
                        "specs": members,
                        "final_tail": checkpoint.get("final_tail"),
                    }
                    for field, actual in comparisons.items():
                        if actual != expected[field]:
                            issues.append(
                                _issue(
                                    "checkpoint_policy_mismatch",
                                    f"{checkpoint_path}.{field}",
                                    f"Expected {expected[field]!r}.",
                                )
                            )
                    expected_owners = list(
                        dict.fromkeys(
                            owner
                            for spec_id in expected["specs"]
                            for owner in spec_ownership.get(spec_id, {}).get(
                                "owners", []
                            )
                        )
                    )
                    expected_repositories = list(
                        dict.fromkeys(
                            repository
                            for spec_id in expected["specs"]
                            for repository in spec_ownership.get(spec_id, {}).get(
                                "repositories", []
                            )
                        )
                    )
                    if affected_owners != expected_owners:
                        issues.append(
                            _issue(
                                "checkpoint_owner_scope_mismatch",
                                f"{checkpoint_path}.affected_owners",
                                "Checkpoint owners must be derived from its member SPEC ownership.",
                            )
                        )
                    if affected_repositories != expected_repositories:
                        issues.append(
                            _issue(
                                "checkpoint_repository_scope_mismatch",
                                f"{checkpoint_path}.affected_repositories",
                                "Checkpoint repositories must be derived from its member SPEC ownership.",
                            )
                        )
            train_checkpoint_ids = [
                checkpoint.get("id")
                for checkpoint in train_checkpoints
                if isinstance(checkpoint, dict)
            ]
            if set(train_checkpoint_ids) != set(checkpoints):
                issues.append(
                    _issue(
                        "checkpoint_mismatch",
                        "$.release_train.checkpoints",
                        "Release-train checkpoints must match every SPEC checkpoint.",
                    )
                )

    code_check = _object(
        record_obj.get("code_read_only"),
        "$.code_read_only",
        {
            "product_test_tree_before_sha256",
            "product_test_tree_after_sha256",
            "changed_product_or_test_paths",
            "evidence",
        },
        issues,
    )
    if code_check is not None:
        before = code_check.get("product_test_tree_before_sha256")
        after = code_check.get("product_test_tree_after_sha256")
        for field, value in (
            ("product_test_tree_before_sha256", before),
            ("product_test_tree_after_sha256", after),
        ):
            if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
                issues.append(
                    _issue(
                        "invalid_sha256",
                        f"$.code_read_only.{field}",
                        "Expected a lowercase SHA-256 digest.",
                    )
                )
        if before != after:
            issues.append(
                _issue(
                    "planning_code_changed",
                    "$.code_read_only",
                    "Planning changed the product/test tree.",
                )
            )
        changed = _string_list(
            code_check.get("changed_product_or_test_paths"),
            "$.code_read_only.changed_product_or_test_paths",
            issues,
            allow_empty=True,
        )
        if changed:
            issues.append(
                _issue(
                    "planning_code_changed",
                    "$.code_read_only.changed_product_or_test_paths",
                    "Planning must not change product or test paths.",
                )
            )
        _string_list(code_check.get("evidence"), "$.code_read_only.evidence", issues)

    _string_list(record_obj.get("handoff_evidence"), "$.handoff_evidence", issues)
    return _sorted(issues)


def _controller_issues(state: Any, record: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not isinstance(state, dict):
        return [
            _issue("invalid_type", "$controller", "Controller state must be an object.")
        ]
    if state.get("run_id") != record.get("run_id"):
        issues.append(
            _issue(
                "controller_run_id_mismatch",
                "$controller.run_id",
                "Controller and planning records must share a run ID.",
            )
        )
    tasks = state.get("child_tasks")
    if not isinstance(tasks, list):
        issues.append(
            _issue(
                "invalid_type",
                "$controller.child_tasks",
                "Controller child_tasks must be an array.",
            )
        )
        return _sorted(issues)
    planners = [
        task
        for task in tasks
        if isinstance(task, dict) and task.get("kind") == "planning"
    ]
    if len(planners) != 1:
        issues.append(
            _issue(
                "planning_task_count",
                "$controller.child_tasks",
                "Exactly one planning task must exist.",
            )
        )
        return _sorted(issues)
    planner = planners[0]
    expected_id = (
        record.get("planning_task", {}).get("id")
        if isinstance(record.get("planning_task"), dict)
        else None
    )
    if planner.get("id") != expected_id:
        issues.append(
            _issue(
                "planning_task_id_mismatch",
                "$controller.child_tasks",
                "Controller planning task must match the planning record.",
            )
        )
    if planner.get("lifecycle") != "archived":
        issues.append(
            _issue(
                "planning_not_archived",
                "$controller.child_tasks",
                "Planning must be verified and archived before implementation dispatch.",
            )
        )
    generation = (
        record.get("planning_task", {}).get("generation")
        if isinstance(record.get("planning_task"), dict)
        else None
    )
    spec_tasks = [
        task for task in tasks if isinstance(task, dict) and task.get("kind") == "spec"
    ]
    if generation == 1 and spec_tasks:
        issues.append(
            _issue(
                "implementation_preceded_initial_planning_gate",
                "$controller.child_tasks",
                "Initial planning must pass before any SPEC task is created.",
            )
        )
    if generation != 1 and any(
        task.get("lifecycle") != "archived" for task in spec_tasks
    ):
        issues.append(
            _issue(
                "implementation_active_during_planning_revision",
                "$controller.child_tasks",
                "Existing SPEC tasks must be archived before revising planning.",
            )
        )
    for task in tasks:
        if (
            isinstance(task, dict)
            and task is not planner
            and task.get("lifecycle") != "archived"
        ):
            issues.append(
                _issue(
                    "other_task_not_archived",
                    "$controller.child_tasks",
                    "No other child may remain active at the planning dispatch gate.",
                )
            )
            break
    return _sorted(issues)


def _readback_issues(
    record: dict[str, Any],
    readback: Any,
    expected_run_id: str,
    target: str,
    capabilities: dict[str, set[str]],
    expected_task_id: str,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    route: dict[str, Any] | None = None
    xhigh_evidence: list[str] = []
    if target == "planning":
        task, route = _planning_task(record, capabilities, issues)
        recorded_task_id = task.get("id") if task else None
        if recorded_task_id != expected_task_id:
            issues.append(
                _issue(
                    "expected_task_id_mismatch",
                    "$record.planning_task.id",
                    "Expected task does not match the planning record.",
                )
            )
        raw_evidence = record.get("planning_xhigh_evidence")
        if isinstance(raw_evidence, list):
            xhigh_evidence = [item for item in raw_evidence if isinstance(item, str) and item.strip()]
    else:
        specs = record.get("specs")
        match = (
            next(
                (
                    spec
                    for spec in specs
                    if isinstance(spec, dict) and spec.get("id") == target
                ),
                None,
            )
            if isinstance(specs, list)
            else None
        )
        if match is None:
            issues.append(
                _issue(
                    "route_target_unknown",
                    "$readback.target",
                    "Target SPEC is absent from the planning record.",
                )
            )
        else:
            raw_evidence = match.get("xhigh_evidence")
            if isinstance(raw_evidence, list):
                xhigh_evidence = [item for item in raw_evidence if isinstance(item, str) and item.strip()]
            route = _shared_route(
                match.get("route"),
                f"$.specs[{target}].route",
                capabilities,
                issues,
                xhigh_evidence=xhigh_evidence,
            )
    validate_task_readback(
        readback,
        "$readback",
        issues,
        identity_field="run_id",
        expected_identity=expected_run_id,
        expected_target=target,
        expected_task_id=expected_task_id,
        route=route,
        capabilities=capabilities,
        xhigh_evidence=xhigh_evidence,
    )
    return _sorted(issues)


def evaluate_route(
    record_path: Path,
    readback_path: Path,
    expected_run_id: str,
    target: str,
    expected_task_id: str,
) -> tuple[dict[str, Any], int]:
    issues: list[dict[str, str]] = []
    if MODEL_POLICY_ERROR is not None:
        issues.append(
            _issue(
                "model_policy_invalid",
                "$.model_policy",
                f"Implement Needs model policy is invalid: {MODEL_POLICY_ERROR}.",
            )
        )
    record, record_raw = _read_json(record_path, "planning_record", issues)
    readback, readback_raw = _read_json(readback_path, "route_readback", issues)
    capabilities: dict[str, set[str]] = {}
    if isinstance(record, dict):
        _, capabilities = _route_scaffold(record, expected_run_id, issues)
        if target == "planning":
            _planning_task(record, capabilities, issues)
        if readback is not None:
            issues.extend(
                _readback_issues(
                    record,
                    readback,
                    expected_run_id,
                    target,
                    capabilities,
                    expected_task_id,
                )
            )
    issues = _sorted(issues)
    if issues:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "decision": "reject",
            "gate": "task_route",
            "target": target,
            "reasons": issues,
            "next_action": {
                "kind": "repair",
                "target": readback.get("task_id")
                if isinstance(readback, dict)
                else target,
                "instruction": "Keep the task code-read-only, repair or explicitly select a locked fallback, then capture a fresh applied model/effort readback.",
            },
        }
        payload["decision_sha256"] = _decision_hash(payload)
        return payload, 1
    assert isinstance(record, dict) and isinstance(readback, dict)
    assert record_raw is not None and readback_raw is not None
    if target == "planning":
        route = record["planning_task"]["route"]
    else:
        route = next(spec["route"] for spec in record["specs"] if spec["id"] == target)
    return build_route_receipt(
        identity_name="run_id",
        identity=expected_run_id,
        target=target,
        task_id=readback["task_id"],
        selection=readback["selection"],
        applied=readback["applied"],
        route=route,
        record_hash_name="planning_record_sha256",
        record_hash=_sha256(record_raw),
        readback_hash_name="route_readback_sha256",
        readback_hash=_sha256(readback_raw),
        identity_evidence="formal_readback",
        capability_evidence="host_capability_readback",
        configured_route_evidence="post_create_readback",
    ), 0


def evaluate_handoff(
    record_path: Path,
    state_path: Path,
    planning_readback_path: Path,
    expected_run_id: str,
) -> tuple[dict[str, Any], int]:
    issues: list[dict[str, str]] = []
    if MODEL_POLICY_ERROR is not None:
        issues.append(
            _issue(
                "model_policy_invalid",
                "$.model_policy",
                f"Implement Needs model policy is invalid: {MODEL_POLICY_ERROR}.",
            )
        )
    record, record_raw = _read_json(record_path, "planning_record", issues)
    state, state_raw = _read_json(state_path, "controller_state", issues)
    readback, readback_raw = _read_json(
        planning_readback_path, "route_readback", issues
    )
    capabilities: dict[str, set[str]] = {}
    if record is not None:
        issues.extend(_record_issues(record, expected_run_id))
    if isinstance(record, dict):
        _, capabilities = _route_scaffold(record, expected_run_id, [])
        issues.extend(_controller_issues(state, record))
        if readback is not None:
            planning_task = (
                record.get("planning_task")
                if isinstance(record.get("planning_task"), dict)
                else {}
            )
            issues.extend(
                _readback_issues(
                    record,
                    readback,
                    expected_run_id,
                    "planning",
                    capabilities,
                    planning_task.get("id", ""),
                )
            )
    issues = _sorted(issues)
    if issues:
        planning_id = (
            record.get("planning_task", {}).get("id")
            if isinstance(record, dict)
            and isinstance(record.get("planning_task"), dict)
            else "planning"
        )
        payload: dict[str, Any] = {
            "schema_version": 1,
            "decision": "reject",
            "gate": "planning_handoff",
            "reasons": issues,
            "next_action": {
                "kind": "repair",
                "target": planning_id,
                "instruction": "Unarchive the same planning task if needed, return these deterministic failures, and rerun the gate after a corrected handoff.",
            },
        }
        payload["decision_sha256"] = _decision_hash(payload)
        return payload, 1
    assert (
        isinstance(record, dict)
        and isinstance(state, dict)
        and isinstance(readback, dict)
    )
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
        "checkpoint_size": record["release_train"]["checkpoint_size"],
        "checkpoints": record["release_train"]["checkpoints"],
        "grill_question_count": question_count,
        "planning_record_sha256": _sha256(record_raw),
        "controller_state_sha256": _sha256(state_raw),
        "route_readback_sha256": _sha256(readback_raw),
    }
    payload["receipt_sha256"] = _decision_hash(payload)
    return payload, 0


def _serialize(payload: dict[str, Any]) -> str:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )


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
    route = subparsers.add_parser(
        "route", help="Validate requested versus applied task routing"
    )
    route.add_argument("--record", required=True, type=Path)
    route.add_argument("--readback", required=True, type=Path)
    route.add_argument("--expected-run-id", required=True)
    route.add_argument("--target", required=True)
    route.add_argument("--expected-task-id", required=True)
    route.add_argument("--receipt", type=Path)
    handoff = subparsers.add_parser(
        "handoff", help="Validate the complete planning handoff and dispatch boundary"
    )
    handoff.add_argument("--record", required=True, type=Path)
    handoff.add_argument("--controller-state", required=True, type=Path)
    handoff.add_argument("--planning-readback", required=True, type=Path)
    handoff.add_argument("--expected-run-id", required=True)
    handoff.add_argument("--receipt", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "route":
        payload, exit_code = evaluate_route(
            args.record,
            args.readback,
            args.expected_run_id,
            args.target,
            args.expected_task_id,
        )
    else:
        payload, exit_code = evaluate_handoff(
            args.record,
            args.controller_state,
            args.planning_readback,
            args.expected_run_id,
        )
    serialized = _serialize(payload)
    if args.receipt is not None:
        try:
            _write_atomic(args.receipt, serialized)
        except OSError as exc:
            payload = {
                "schema_version": 1,
                "decision": "reject",
                "gate": payload.get("gate"),
                "reasons": [
                    _issue(
                        "receipt_unwritable",
                        str(args.receipt),
                        f"Cannot persist receipt: {exc.__class__.__name__}.",
                    )
                ],
                "next_action": {
                    "kind": "repair",
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
