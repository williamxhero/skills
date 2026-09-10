#!/usr/bin/env python3
"""Replay and validate the observable Implement Needs controller lifecycle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROUTE_POLICY_DIR = Path(__file__).resolve().parents[2] / "route-codex-task" / "scripts"
sys.path.insert(0, str(ROUTE_POLICY_DIR))
from route_policy import (
    POLICY as MODEL_POLICY,
    POLICY_ERROR as MODEL_POLICY_ERROR,
    decision_hash as _decision_hash,
    route_receipt_issues as _route_receipt_issues,
)

CONTROLLER_STATES = {
    "active",
    "terminal_success",
    "terminal_blocked",
    "user_stopped",
}
ACTION_KINDS = {
    "wait",
    "verify",
    "archive",
    "repair",
    "dispatch",
    "advance",
    "resume",
    "refresh_state",
    "repair_state",
}
COMMON_FIELDS = {
    "schema_version",
    "run_id",
    "sequence",
    "type",
    "actor",
    "data",
    "evidence",
}
DATA_FIELDS = {
    "planning_archived": {
        "task_id",
        "specs",
        "checkpoint_size",
        "checkpoints",
        "default_branch",
        "default_revision",
        "planning_record_sha256",
    },
    "spec_dispatched": {
        "task_id",
        "spec_id",
        "base_revision",
        "route_selection",
        "model",
        "thinking",
        "route_receipt",
    },
    "commentary": {"category", "text", "next_action"},
    "waited": {"task_id"},
    "ticket_evidence": {
        "spec_id",
        "ticket_id",
        "owner_task_id",
        "blocked_by",
        "commits",
        "test_evidence",
        "tracker_state",
    },
    "ticket_implementation_artifact": {
        "artifact_type",
        "id",
        "spec_id",
        "ticket_id",
    },
    "role_limited_task": {
        "task_id",
        "spec_id",
        "parent_task_id",
        "role",
        "writes_product_code",
        "merge_commits",
    },
    "child_handoff": {"task_id", "boundary", "revision"},
    "handoff_verified": {"task_id", "result", "revision"},
    "child_archived": {"task_id"},
    "checkpoint_passed": {
        "checkpoint_id",
        "revision",
        "candidate_revisions",
        "affected_owners",
        "affected_repositories",
    },
    "checkpoint_failed": {
        "checkpoint_id",
        "revision",
        "candidate_revisions",
        "affected_owners",
        "affected_repositories",
        "reason",
    },
    "default_branch_verified": {"spec_id", "revision"},
    "blocker_opened": {
        "repair_task_id",
        "parent_task_id",
        "fingerprint",
        "blocked_action",
    },
    "blocked_action_resumed": {"repair_task_id", "parent_task_id", "action"},
    "controller_resumed": {"persisted_next_action", "observed_task_ids"},
    "child_reconnected": {"task_id", "route_receipt"},
    "stored_action_resumed": {"action"},
    "release_candidate_frozen": {"revision"},
    "artifact_built": {"revision", "artifact_id"},
    "final_tests_passed": {
        "revision",
        "artifact_id",
        "candidate_revisions",
        "l4_reused_checkpoint",
    },
    "package_completed": {"revision", "artifact_id", "package_id"},
    "deployment_completed": {
        "revision",
        "artifact_id",
        "package_id",
        "target",
    },
    "smoke_passed": {"revision", "artifact_id", "target"},
    "deployment_not_applicable": {
        "revision",
        "artifact_id",
        "package_id",
        "reason",
    },
    "child_paused": {"task_id"},
    "blocker_stopped": {
        "repair_task_id",
        "parent_task_id",
        "fingerprint",
        "resume_action",
        "attempts",
        "same_fingerprint_count",
        "external_authority_required",
        "no_safe_action",
    },
    "user_stop_recorded": {"resume_action"},
    "terminal_ready": {"controller_state", "candidate_revision", "next_action"},
}
EXPECTED_ACTORS = {
    event_type: "controller"
    for event_type in DATA_FIELDS
    if event_type not in {"child_handoff", "ticket_evidence"}
}
COMMENTARY_CATEGORIES = {"heartbeat", "side_question_answer", "progress"}
ROUTE_SELECTIONS = {"recommended", "fallback"}
TICKET_IMPLEMENTATION_ARTIFACTS = {
    "task": "tasks",
    "thread": "threads",
    "worktree": "worktrees",
    "branch": "branches",
    "pull_request": "pull_requests",
}
ROLE_LIMITED_ROLES = {
    "blocker_repair",
    "read_only_exploration",
    "read_only_review",
}
CHINESE_RE = re.compile(r"[\u3400-\u9fff]")
SHA256_RE = re.compile(r"[0-9a-f]{64}")

REPAIR_LOG_ACTION = {
    "kind": "repair_state",
    "target": "lifecycle-log.jsonl",
    "instruction": "Repair the lifecycle log from observed task and delivery evidence, then replay the lifecycle gate.",
}
CHECKPOINT_SIZE = 10


def _allowed_pair(model: Any, thinking: Any) -> bool:
    return (
        MODEL_POLICY is not None
        and model in MODEL_POLICY["models"]
        and thinking in MODEL_POLICY["efforts"]
    )


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


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _sorted(issues: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(
        issues, key=lambda item: (item["path"], item["code"], item["message"])
    )


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _candidate_revision_set(revisions: list[str]) -> frozenset[str]:
    """Return the order-independent identity of repository candidate revisions."""
    return frozenset(revisions)


def _nullable_text(value: Any) -> bool:
    return value is None or _text(value)


def _string_list(value: Any, *, nonempty: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (bool(value) or not nonempty)
        and all(_text(item) for item in value)
        and len(value) == len(set(value))
    )


def _empty_ticket_artifacts() -> dict[str, list[str]]:
    return {
        "tasks": [],
        "threads": [],
        "worktrees": [],
        "branches": [],
        "pull_requests": [],
    }


def _action_issues(value: Any, path: str) -> list[dict[str, str]]:
    if not isinstance(value, dict):
        return [_issue("invalid_action", path, "Action must be an object.")]
    expected = {"kind", "target", "instruction"}
    if set(value) != expected:
        return [
            _issue(
                "invalid_action_fields",
                path,
                "Action must contain exactly kind, target, and instruction.",
            )
        ]
    issues: list[dict[str, str]] = []
    if value["kind"] not in ACTION_KINDS:
        issues.append(
            _issue("invalid_action_kind", f"{path}.kind", "Unknown action kind.")
        )
    for field in ("target", "instruction"):
        if not _text(value[field]):
            issues.append(
                _issue(
                    "invalid_action_text", f"{path}.{field}", "Action text is required."
                )
            )
    return issues


def _read_log(path: Path) -> tuple[list[Any], bytes | None, list[dict[str, str]]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return (
            [],
            None,
            [
                _issue(
                    "lifecycle_log_unreadable",
                    str(path),
                    f"Cannot read lifecycle log: {exc.__class__.__name__}.",
                )
            ],
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return (
            [],
            raw,
            [
                _issue(
                    "lifecycle_log_encoding",
                    str(path),
                    "Lifecycle log must be UTF-8 JSON Lines.",
                )
            ],
        )
    if not text.strip():
        return (
            [],
            raw,
            [
                _issue(
                    "lifecycle_log_empty", str(path), "Lifecycle log cannot be empty."
                )
            ],
        )
    events: list[Any] = []
    issues: list[dict[str, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            issues.append(
                _issue(
                    "blank_log_line",
                    f"$line[{line_number}]",
                    "Blank lifecycle lines are not allowed.",
                )
            )
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            issues.append(
                _issue(
                    "malformed_log_line",
                    f"$line[{line_number}]",
                    "Lifecycle line is not valid JSON.",
                )
            )
    return events, raw, _sorted(issues)


def _event_shape_issues(
    event: Any, index: int, expected_run_id: str
) -> list[dict[str, str]]:
    path = f"$events[{index}]"
    if not isinstance(event, dict):
        return [_issue("invalid_event", path, "Lifecycle event must be an object.")]
    issues: list[dict[str, str]] = []
    if set(event) != COMMON_FIELDS:
        issues.append(
            _issue(
                "invalid_event_fields",
                path,
                "Event must contain exactly the documented common fields.",
            )
        )
        return issues
    if event["schema_version"] != 1:
        issues.append(
            _issue(
                "schema_version", f"{path}.schema_version", "Schema version must be 1."
            )
        )
    if event["run_id"] != expected_run_id:
        issues.append(
            _issue("run_id_mismatch", f"{path}.run_id", "Event belongs to another run.")
        )
    if event["sequence"] != index + 1:
        issues.append(
            _issue(
                "sequence_mismatch",
                f"{path}.sequence",
                "Sequence must be contiguous and start at 1.",
            )
        )
    event_type = event["type"]
    if event_type not in DATA_FIELDS:
        issues.append(
            _issue(
                "unknown_event_type", f"{path}.type", "Unknown lifecycle event type."
            )
        )
        return issues
    if (
        not isinstance(event["data"], dict)
        or set(event["data"]) != DATA_FIELDS[event_type]
    ):
        issues.append(
            _issue(
                "invalid_event_data_fields",
                f"{path}.data",
                "Event data does not match the documented event shape.",
            )
        )
    expected_actor = EXPECTED_ACTORS.get(event_type)
    if expected_actor is not None and event["actor"] != expected_actor:
        issues.append(
            _issue(
                "actor_mismatch",
                f"{path}.actor",
                f"{event_type} must be owned by {expected_actor}.",
            )
        )
    if not _string_list(event["evidence"], nonempty=True):
        issues.append(
            _issue(
                "invalid_evidence",
                f"{path}.evidence",
                "Every event requires unique, non-empty evidence pointers.",
            )
        )
    return _sorted(issues)


def _matches_action(action: dict[str, str], event: dict[str, Any]) -> bool:
    event_type = event["type"]
    data = event["data"]
    target = action["target"]
    kind = action["kind"]
    if kind == "wait":
        return event_type == "waited" and data.get("task_id") == target
    if kind == "verify":
        if event_type == "handoff_verified":
            return data.get("task_id") == target
        return (
            event_type in {"checkpoint_passed", "checkpoint_failed"}
            and data.get("checkpoint_id") == target
        ) or (event_type == "default_branch_verified" and data.get("spec_id") == target)
    if kind == "archive":
        return event_type == "child_archived" and data.get("task_id") == target
    if kind == "repair":
        return event_type == "blocker_opened" and target in {
            data.get("parent_task_id"),
            data.get("fingerprint"),
            data.get("blocked_action", {}).get("target")
            if isinstance(data.get("blocked_action"), dict)
            else None,
        }
    if kind == "dispatch":
        return event_type == "spec_dispatched" and data.get("spec_id") == target
    if kind == "advance":
        return event_type in {
            "checkpoint_passed",
            "checkpoint_failed",
            "default_branch_verified",
            "release_candidate_frozen",
            "artifact_built",
            "final_tests_passed",
            "package_completed",
            "deployment_completed",
            "deployment_not_applicable",
            "smoke_passed",
            "terminal_ready",
        }
    if kind == "resume":
        return event_type == "blocked_action_resumed" and target in {
            data.get("parent_task_id"),
            data.get("action", {}).get("target")
            if isinstance(data.get("action"), dict)
            else None,
        }
    if kind in {"refresh_state", "repair_state"}:
        return event_type == "controller_resumed"
    return False


class _Replay:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.issues: list[dict[str, str]] = []
        self.tasks: dict[str, dict[str, Any]] = {}
        self.specs: list[dict[str, Any]] = []
        self.spec_state: dict[str, dict[str, Any]] = {}
        self.pending_specs: list[str] = []
        self.default_branch: str | None = None
        self.default_revision: str | None = None
        self.planning_record_sha256: str | None = None
        self.checkpoint_size = CHECKPOINT_SIZE
        self.checkpoints: list[dict[str, Any]] = []
        self.checkpoint_state: dict[str, dict[str, Any]] = {}
        self.blocker: dict[str, Any] | None = None
        self.pending_action: dict[str, str] | None = None
        self.pending_action_source: str | None = None
        self.recovery: dict[str, Any] | None = None
        self.controller_state = "active"
        self.resume_action: dict[str, str] | None = None
        self.candidate_revision: str | None = None
        self.artifact_id: str | None = None
        self.tests_passed = False
        self.release_l4: dict[str, Any] | None = None
        self.package_id: str | None = None
        self.deployment_status = "pending"
        self.deployment_target: str | None = None
        self.smoke_passed = False
        self.ticket_implementation_artifacts = _empty_ticket_artifacts()
        self.role_limited_tasks: list[dict[str, Any]] = []
        self.evidence_count = 0

    def add(self, code: str, path: str, message: str) -> None:
        self.issues.append(_issue(code, path, message))

    def task(self, task_id: Any, path: str) -> dict[str, Any] | None:
        if not _text(task_id) or task_id not in self.tasks:
            self.add("unknown_task", path, "Event names an unknown child task.")
            return None
        return self.tasks[task_id]

    def _load_checkpoints(
        self, value: Any, expected: list[dict[str, Any]], path: str
    ) -> None:
        if not isinstance(value, list) or len(value) != len(expected):
            self.add(
                "checkpoint_mismatch",
                path,
                "Lifecycle checkpoints must match the deterministic fixed-size plan.",
            )
            return
        parsed: list[dict[str, Any]] = []
        for index, checkpoint in enumerate(value):
            checkpoint_path = f"{path}[{index}]"
            if not isinstance(checkpoint, dict) or set(checkpoint) != {
                "id",
                "start_spec_index",
                "end_spec_index",
                "specs",
                "final_tail",
                "affected_owners",
                "affected_repositories",
            }:
                self.add(
                    "invalid_checkpoint_plan",
                    checkpoint_path,
                    "Checkpoint needs deterministic membership and affected surfaces.",
                )
                continue
            actual_members = checkpoint.get("specs")
            affected_owners = checkpoint.get("affected_owners")
            affected_repositories = checkpoint.get("affected_repositories")
            if not _string_list(actual_members, nonempty=True):
                self.add(
                    "invalid_checkpoint_plan",
                    f"{checkpoint_path}.specs",
                    "Checkpoint SPEC membership is required.",
                )
            if not _string_list(affected_owners, nonempty=True):
                self.add(
                    "invalid_checkpoint_surfaces",
                    f"{checkpoint_path}.affected_owners",
                    "Checkpoint affected owners are required.",
                )
            if not _string_list(affected_repositories, nonempty=True):
                self.add(
                    "invalid_checkpoint_surfaces",
                    f"{checkpoint_path}.affected_repositories",
                    "Checkpoint affected repositories are required.",
                )
            expected_checkpoint = expected[index]
            for field in (
                "id",
                "start_spec_index",
                "end_spec_index",
                "specs",
                "final_tail",
            ):
                if checkpoint.get(field) != expected_checkpoint[field]:
                    self.add(
                        "checkpoint_policy_mismatch",
                        f"{checkpoint_path}.{field}",
                        f"Expected {expected_checkpoint[field]!r}.",
                    )
            members = set(expected_checkpoint["specs"])
            expected_owners = list(
                dict.fromkeys(
                    owner
                    for spec in self.specs
                    if spec["id"] in members
                    for owner in spec["owners"]
                )
            )
            expected_repositories = list(
                dict.fromkeys(
                    repository
                    for spec in self.specs
                    if spec["id"] in members
                    for repository in spec["repositories"]
                )
            )
            if affected_owners != expected_owners:
                self.add(
                    "checkpoint_owner_scope_mismatch",
                    f"{checkpoint_path}.affected_owners",
                    "Checkpoint owners must be derived from its member SPEC ownership.",
                )
            if affected_repositories != expected_repositories:
                self.add(
                    "checkpoint_repository_scope_mismatch",
                    f"{checkpoint_path}.affected_repositories",
                    "Checkpoint repositories must be derived from its member SPEC ownership.",
                )
            parsed_checkpoint = {
                "id": checkpoint.get("id"),
                "start_spec_index": checkpoint.get("start_spec_index"),
                "end_spec_index": checkpoint.get("end_spec_index"),
                "specs": checkpoint.get("specs"),
                "final_tail": checkpoint.get("final_tail"),
                "affected_owners": checkpoint.get("affected_owners"),
                "affected_repositories": checkpoint.get("affected_repositories"),
            }
            parsed.append(parsed_checkpoint)
            self.checkpoint_state[parsed_checkpoint["id"]] = {
                **parsed_checkpoint,
                "status": "pending",
                "revision": None,
                "candidate_revisions": [],
                "evidence": [],
            }
        self.checkpoints = parsed

    def _completed_spec_count(self) -> int:
        return len(self.specs) - len(self.pending_specs)

    def _due_checkpoint(self) -> dict[str, Any] | None:
        completed = self._completed_spec_count()
        for checkpoint in self.checkpoints:
            state = self.checkpoint_state.get(checkpoint["id"])
            if (
                isinstance(checkpoint.get("end_spec_index"), int)
                and checkpoint["end_spec_index"] <= completed
                and state is not None
                and state["status"] != "passed"
            ):
                return state
        return None

    def _last_checkpoint(self) -> dict[str, Any] | None:
        if not self.checkpoints:
            return None
        return self.checkpoint_state.get(self.checkpoints[-1]["id"])

    def _checkpoint_result(
        self,
        data: dict[str, Any],
        path: str,
        *,
        status: str,
    ) -> dict[str, Any] | None:
        checkpoint_id = data.get("checkpoint_id")
        if not _text(checkpoint_id) or checkpoint_id not in self.checkpoint_state:
            self.add(
                "unknown_checkpoint",
                f"{path}.data.checkpoint_id",
                "Unknown checkpoint.",
            )
            return None
        checkpoint = self.checkpoint_state[checkpoint_id]
        if (
            isinstance(checkpoint.get("end_spec_index"), int)
            and checkpoint["end_spec_index"] > self._completed_spec_count()
        ):
            self.add(
                "checkpoint_before_due",
                path,
                "Checkpoint L4 is due only after every SPEC in its segment is closed.",
            )
        if data.get("revision") != self.default_revision:
            self.add(
                "checkpoint_revision_mismatch",
                path,
                "Checkpoint must test the current default revision.",
            )
        candidate_revisions = data.get("candidate_revisions")
        affected_owners = data.get("affected_owners")
        affected_repositories = data.get("affected_repositories")
        if not _string_list(candidate_revisions, nonempty=True):
            self.add(
                "invalid_checkpoint_revisions",
                f"{path}.data.candidate_revisions",
                "Checkpoint candidate revisions are required.",
            )
            candidate_revisions = []
        if data.get("revision") not in candidate_revisions:
            self.add(
                "checkpoint_candidate_mismatch",
                path,
                "Checkpoint candidate revisions must include the tested revision.",
            )
        if affected_owners != checkpoint.get("affected_owners"):
            self.add(
                "checkpoint_owner_scope_mismatch",
                f"{path}.data.affected_owners",
                "Checkpoint must run only the affected owners planned for this segment.",
            )
        if affected_repositories != checkpoint.get("affected_repositories"):
            self.add(
                "checkpoint_repository_scope_mismatch",
                f"{path}.data.affected_repositories",
                "Checkpoint must run only the affected repositories planned for this segment.",
            )
        if status == "passed" and checkpoint["status"] == "passed":
            self.add(
                "checkpoint_repeated", path, "A passed checkpoint may not be repeated."
            )
        checkpoint["status"] = status
        checkpoint["revision"] = data.get("revision")
        checkpoint["candidate_revisions"] = sorted(candidate_revisions or [])
        return checkpoint

    def _before_event(self, event: dict[str, Any], path: str) -> None:
        if self.controller_state != "active":
            self.add(
                "event_after_terminal",
                path,
                "No event may follow a terminal lifecycle event.",
            )
        if self.pending_action is not None:
            if not _matches_action(self.pending_action, event):
                self.add(
                    "continuation_not_immediate",
                    path,
                    f"The {self.pending_action_source} action was not executed immediately.",
                )
            self.pending_action = None
            self.pending_action_source = None
        if self.recovery is not None and event["type"] not in {
            "child_reconnected",
            "stored_action_resumed",
        }:
            self.add(
                "recovery_interrupted",
                path,
                "Recovery must reconnect recorded children and resume the stored action before other work.",
            )

    def apply(self, event: dict[str, Any], index: int) -> None:
        path = f"$events[{index}]"
        self.evidence_count += len(event["evidence"])
        self._before_event(event, path)
        handler = getattr(self, f"on_{event['type']}")
        handler(event, event["data"], path)

    def on_planning_archived(
        self, event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        if self.tasks or self.specs:
            self.add(
                "duplicate_planning", path, "Planning may be initialized exactly once."
            )
            return
        if event["sequence"] != 1:
            self.add(
                "planning_not_first",
                path,
                "The archived planning handoff must start the lifecycle log.",
            )
        if not all(
            _text(data.get(field))
            for field in ("task_id", "default_branch", "default_revision")
        ):
            self.add(
                "invalid_planning_data",
                path,
                "Planning task, branch, and revision are required.",
            )
            return
        planning_record_sha256 = data.get("planning_record_sha256")
        if (
            not isinstance(planning_record_sha256, str)
            or SHA256_RE.fullmatch(planning_record_sha256) is None
        ):
            self.add(
                "invalid_planning_record_identity",
                f"{path}.data.planning_record_sha256",
                "Planning lifecycle must retain the validated planning-record SHA-256.",
            )
        specs = data.get("specs")
        if not isinstance(specs, list) or not specs:
            self.add(
                "invalid_spec_plan",
                f"{path}.data.specs",
                "At least one planned SPEC is required.",
            )
            return
        parsed: list[dict[str, Any]] = []
        for offset, spec in enumerate(specs):
            spec_path = f"{path}.data.specs[{offset}]"
            if (
                not isinstance(spec, dict)
                or set(spec) != {"id", "tickets", "owners", "repositories"}
                or not _text(spec.get("id"))
            ):
                self.add(
                    "invalid_spec_plan",
                    spec_path,
                    "SPEC needs persisted id, tickets, owners, and repositories.",
                )
                continue
            owners = spec.get("owners")
            repositories = spec.get("repositories")
            if not _string_list(owners, nonempty=True) or not _string_list(
                repositories, nonempty=True
            ):
                self.add(
                    "invalid_spec_ownership",
                    spec_path,
                    "Every SPEC must persist non-empty owner and repository ownership.",
                )
            ticket_values = spec.get("tickets")
            if not isinstance(ticket_values, list) or not ticket_values:
                self.add(
                    "invalid_ticket_plan",
                    f"{spec_path}.tickets",
                    "Every planned SPEC needs at least one ticket.",
                )
                continue
            parsed_tickets: list[dict[str, Any]] = []
            for ticket_offset, ticket in enumerate(ticket_values):
                ticket_path = f"{spec_path}.tickets[{ticket_offset}]"
                if (
                    not isinstance(ticket, dict)
                    or set(ticket) != {"id", "blocked_by"}
                    or not _text(ticket.get("id"))
                    or not _string_list(ticket.get("blocked_by"))
                ):
                    self.add(
                        "invalid_ticket_plan",
                        ticket_path,
                        "Ticket plan entries need id and blocked_by.",
                    )
                    continue
                parsed_tickets.append(
                    {"id": ticket["id"], "blocked_by": list(ticket["blocked_by"])}
                )
            ticket_ids = [ticket["id"] for ticket in parsed_tickets]
            if len(ticket_ids) != len(set(ticket_ids)) or len(parsed_tickets) != len(
                ticket_values
            ):
                self.add(
                    "invalid_ticket_plan",
                    f"{spec_path}.tickets",
                    "Planned ticket IDs must be unique and valid.",
                )
                continue
            ticket_positions = {
                ticket_id: ticket_index
                for ticket_index, ticket_id in enumerate(ticket_ids)
            }
            for ticket in parsed_tickets:
                for dependency in ticket["blocked_by"]:
                    if dependency not in ticket_positions:
                        self.add(
                            "invalid_ticket_plan",
                            f"{spec_path}.tickets",
                            f"Unknown ticket blocker {dependency}.",
                        )
                    elif ticket_positions[dependency] >= ticket_positions[ticket["id"]]:
                        self.add(
                            "invalid_ticket_plan",
                            f"{spec_path}.tickets",
                            "Ticket blockers must precede blocked tickets.",
                        )
            parsed.append(
                {
                    "id": spec["id"],
                    "tickets": parsed_tickets,
                    "owners": list(owners) if isinstance(owners, list) else [],
                    "repositories": list(repositories)
                    if isinstance(repositories, list)
                    else [],
                }
            )
        ids = [spec["id"] for spec in parsed]
        if len(ids) != len(set(ids)) or len(parsed) != len(specs):
            self.add(
                "invalid_spec_plan",
                f"{path}.data.specs",
                "Planned SPEC IDs must be unique and valid.",
            )
            return
        task_id = data["task_id"]
        self.tasks[task_id] = {
            "id": task_id,
            "kind": "planning",
            "spec_id": None,
            "lifecycle": "archived",
        }
        self.specs = parsed
        self.pending_specs = ids
        self.spec_state = {
            spec["id"]: {
                "task_id": None,
                "route_selection": None,
                "model": None,
                "thinking": None,
                "route_receipt": None,
                "owners": list(spec["owners"]),
                "repositories": list(spec["repositories"]),
                "ticket_order": [ticket["id"] for ticket in spec["tickets"]],
                "tickets": {
                    ticket["id"]: {
                        "blocked_by": ticket["blocked_by"],
                        "owner_task_id": None,
                        "commits": [],
                        "test_evidence": [],
                        "tracker_state": "planned",
                    }
                    for ticket in spec["tickets"]
                },
                "branch_verified": False,
            }
            for spec in parsed
        }
        if data.get("checkpoint_size") != CHECKPOINT_SIZE:
            self.add(
                "checkpoint_size_mismatch",
                f"{path}.data.checkpoint_size",
                "Release train checkpoint_size must be the fixed value 10.",
            )
        self._load_checkpoints(
            data.get("checkpoints"), _checkpoint_plan(ids), f"{path}.data.checkpoints"
        )
        self.default_branch = data["default_branch"]
        self.default_revision = data["default_revision"]
        self.planning_record_sha256 = planning_record_sha256

    def on_spec_dispatched(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        if not all(
            _text(data.get(field)) for field in ("task_id", "spec_id", "base_revision")
        ):
            self.add(
                "invalid_dispatch",
                path,
                "Dispatch requires task, SPEC, and base revision.",
            )
            return
        if data.get("route_selection") not in ROUTE_SELECTIONS:
            self.add(
                "invalid_spec_route",
                path,
                "Dispatch must record the validated SPEC route selection.",
            )
        if not _allowed_pair(data.get("model"), data.get("thinking")):
            self.add(
                "invalid_spec_model_policy",
                path,
                "Dispatch model and effort must be allowed by the Implement Needs policy.",
            )
        self.issues.extend(
            _route_receipt_issues(
                data.get("route_receipt"),
                f"{path}.data.route_receipt",
                run_id=self.run_id,
                target=data["spec_id"],
                task_id=data["task_id"],
                selection=data.get("route_selection"),
                model=data.get("model"),
                thinking=data.get("thinking"),
                planning_record_sha256=self.planning_record_sha256,
            )
        )
        if not self.pending_specs or data["spec_id"] != self.pending_specs[0]:
            self.add(
                "spec_order",
                path,
                "Only the next planned pending SPEC may be dispatched.",
            )
        if data["base_revision"] != self.default_revision:
            self.add(
                "stale_spec_base",
                f"{path}.data.base_revision",
                "SPEC must start from the latest verified default-branch revision.",
            )
        due_checkpoint = self._due_checkpoint()
        if due_checkpoint is not None:
            self.add(
                "checkpoint_blocks_next_segment",
                path,
                "A due or failed L4 checkpoint must pass before dispatching the next SPEC.",
            )
        active_specs = [
            task
            for task in self.tasks.values()
            if task["kind"] == "spec" and task["lifecycle"] != "archived"
        ]
        if active_specs:
            self.add(
                "overlapping_spec",
                path,
                "Only one SPEC implementation task may be unarchived.",
            )
        task_id = data["task_id"]
        if task_id in self.tasks:
            self.add(
                "duplicate_task",
                path,
                "Recovery must reuse an existing task instead of dispatching a duplicate.",
            )
            return
        spec_id = data["spec_id"]
        if spec_id not in self.spec_state:
            self.add(
                "unknown_spec", path, "Dispatched SPEC was not produced by planning."
            )
            return
        if self.spec_state[spec_id]["task_id"] is not None:
            self.add(
                "duplicate_spec_task",
                path,
                "A SPEC may own exactly one implementation task.",
            )
        self.tasks[task_id] = {
            "id": task_id,
            "kind": "spec",
            "spec_id": spec_id,
            "lifecycle": "active",
        }
        status = self.spec_state[spec_id]
        status["task_id"] = task_id
        status["route_selection"] = data.get("route_selection")
        status["model"] = data.get("model")
        status["thinking"] = data.get("thinking")
        status["route_receipt"] = data.get("route_receipt")
        for ticket in status["tickets"].values():
            ticket["owner_task_id"] = task_id

    def on_commentary(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        if data.get("category") not in COMMENTARY_CATEGORIES:
            self.add(
                "invalid_commentary_category",
                f"{path}.data.category",
                "Unknown commentary category.",
            )
        if not _text(data.get("text")) or CHINESE_RE.search(data["text"]) is None:
            self.add(
                "commentary_not_chinese",
                f"{path}.data.text",
                "Controller commentary must contain Chinese.",
            )
        action_issues = _action_issues(
            data.get("next_action"), f"{path}.data.next_action"
        )
        self.issues.extend(action_issues)
        if not action_issues:
            self.pending_action = dict(data["next_action"])
            self.pending_action_source = data.get("category", "commentary")

    def on_waited(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        task = self.task(data.get("task_id"), f"{path}.data.task_id")
        if task is not None and task["lifecycle"] != "active":
            self.add("wait_ineligible", path, "Only an active child may be waited on.")

    def on_ticket_evidence(
        self, event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        spec_id = data.get("spec_id")
        ticket_id = data.get("ticket_id")
        if event["actor"] != "spec_child":
            self.add(
                "actor_mismatch",
                f"{path}.actor",
                "Ticket evidence must be emitted by the SPEC child.",
            )
        task = self._current_spec_task(spec_id, f"{path}.data.spec_id")
        if task is None:
            return
        if task["lifecycle"] != "active":
            self.add(
                "ticket_evidence_ineligible",
                path,
                "Ticket evidence must belong to the active SPEC task.",
            )
        tickets = self.spec_state[spec_id]["tickets"]
        if not _text(ticket_id) or ticket_id not in tickets:
            self.add(
                "unknown_ticket",
                f"{path}.data.ticket_id",
                "Ticket evidence names a ticket outside the SPEC graph.",
            )
            return
        ticket = tickets[ticket_id]
        if data.get("owner_task_id") != task["id"]:
            self.add(
                "ticket_owner_mismatch",
                f"{path}.data.owner_task_id",
                "Ticket implementation owner must equal the SPEC task.",
            )
        if data.get("blocked_by") != ticket["blocked_by"]:
            self.add(
                "ticket_blockers_mismatch",
                f"{path}.data.blocked_by",
                "Ticket evidence must preserve the planned blocking edge.",
            )
        for dependency in ticket["blocked_by"]:
            dependency_ticket = tickets.get(dependency)
            if dependency_ticket is None:
                continue
            if dependency_ticket["tracker_state"] != "closed":
                self.add(
                    "ticket_frontier_not_ready",
                    path,
                    "Ticket evidence may be recorded only after blockers close.",
                )
        if ticket["tracker_state"] == "closed":
            self.add(
                "duplicate_ticket_evidence",
                path,
                "Each ticket may close once in the SPEC task.",
            )
        if not _string_list(data.get("commits"), nonempty=True):
            self.add(
                "ticket_commit_evidence_missing",
                f"{path}.data.commits",
                "Ticket evidence requires retained commit pointers.",
            )
        if not _string_list(data.get("test_evidence"), nonempty=True):
            self.add(
                "ticket_test_evidence_missing",
                f"{path}.data.test_evidence",
                "Ticket evidence requires retained test pointers.",
            )
        if data.get("tracker_state") != "closed":
            self.add(
                "ticket_not_closed",
                f"{path}.data.tracker_state",
                "Ticket evidence must close the tracker ticket.",
            )
        ticket.update(
            {
                "owner_task_id": data.get("owner_task_id"),
                "commits": list(data.get("commits") or []),
                "test_evidence": list(data.get("test_evidence") or []),
                "tracker_state": data.get("tracker_state"),
            }
        )

    def on_ticket_implementation_artifact(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        artifact_type = data.get("artifact_type")
        if artifact_type not in TICKET_IMPLEMENTATION_ARTIFACTS or not all(
            _text(data.get(field)) for field in ("id", "spec_id", "ticket_id")
        ):
            self.add(
                "invalid_ticket_implementation_artifact",
                path,
                "Forbidden ticket implementation artifacts need type, id, SPEC, and ticket.",
            )
            return
        bucket = TICKET_IMPLEMENTATION_ARTIFACTS[artifact_type]
        self.ticket_implementation_artifacts[bucket].append(data["id"])
        self.add(
            "ticket_implementation_artifact_present",
            path,
            "Ticket-level implementation tasks, threads, worktrees, branches, and PRs are forbidden.",
        )

    def on_role_limited_task(
        self, event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        if (
            not _text(data.get("task_id"))
            or not _nullable_text(data.get("spec_id"))
            or not _nullable_text(data.get("parent_task_id"))
            or data.get("role") not in ROLE_LIMITED_ROLES
        ):
            self.add(
                "invalid_role_limited_task",
                path,
                "Role-limited tasks need task, optional SPEC/parent, and a supported role.",
            )
            return
        if data["task_id"] in {
            task["id"] for task in self.tasks.values() if task["kind"] == "spec"
        }:
            self.add(
                "role_limited_task_is_owner",
                path,
                "Role-limited tasks cannot be SPEC implementation owners.",
            )
        if data.get("writes_product_code") is not False or data.get("merge_commits"):
            self.add(
                "role_limited_task_mutated_product",
                path,
                "Role-limited helpers cannot write product code or provide merge commits.",
            )
        if not isinstance(data.get("merge_commits"), list) or not all(
            _text(item) for item in data.get("merge_commits", [])
        ):
            self.add(
                "invalid_role_limited_task",
                f"{path}.data.merge_commits",
                "Merge commit evidence must be an array of strings.",
            )
        self.role_limited_tasks.append(
            {
                "task_id": data["task_id"],
                "spec_id": data.get("spec_id"),
                "parent_task_id": data.get("parent_task_id"),
                "role": data["role"],
                "writes_product_code": data.get("writes_product_code"),
                "merge_commits": list(data.get("merge_commits") or []),
                "evidence": list(event["evidence"]),
            }
        )

    def on_child_handoff(
        self, event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        task = self.task(data.get("task_id"), f"{path}.data.task_id")
        if task is None:
            return
        expected_actor = "spec_child" if task["kind"] == "spec" else "repair_child"
        if task["kind"] not in {"spec", "repair"} or event["actor"] != expected_actor:
            self.add(
                "actor_mismatch",
                f"{path}.actor",
                "Handoff actor must match the child kind.",
            )
        if task["lifecycle"] != "active":
            self.add(
                "handoff_ineligible", path, "Only an active child may return a handoff."
            )
        boundary = data.get("boundary")
        revision = data.get("revision")
        if task["kind"] == "spec":
            if boundary != "merged_evidence" or not _text(revision):
                self.add(
                    "spec_boundary",
                    path,
                    "SPEC handoff must stop at merged evidence with a revision.",
                )
            status = self.spec_state.get(task["spec_id"], {})
            tickets = status.get("tickets", {})
            missing = [
                ticket_id
                for ticket_id in status.get("ticket_order", [])
                if tickets.get(ticket_id, {}).get("tracker_state") != "closed"
            ]
            if missing:
                self.add(
                    "ticket_evidence_missing",
                    path,
                    "SPEC handoff requires closed ticket evidence for every ticket.",
                )
        elif boundary != "repair_evidence" or not _nullable_text(revision):
            self.add(
                "repair_boundary",
                path,
                "Repair handoff must contain repair evidence and an optional revision.",
            )
        task["lifecycle"] = "handoff_received"
        task["handoff_revision"] = revision

    def on_handoff_verified(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        task = self.task(data.get("task_id"), f"{path}.data.task_id")
        if task is None:
            return
        if task["lifecycle"] != "handoff_received":
            self.add(
                "verification_ineligible",
                path,
                "Verification requires a returned handoff.",
            )
        if data.get("result") not in {"pass", "fail"} or not _nullable_text(
            data.get("revision")
        ):
            self.add(
                "invalid_verification",
                path,
                "Verification needs pass/fail and an optional revision.",
            )
            return
        if data["result"] == "fail":
            task["lifecycle"] = "active"
            task.pop("handoff_revision", None)
            return
        if data["revision"] != task.get("handoff_revision"):
            self.add(
                "verified_revision_mismatch",
                path,
                "Verified revision must match the child handoff.",
            )
        if task["kind"] == "spec":
            if not _text(data["revision"]):
                self.add(
                    "verified_revision_missing",
                    path,
                    "A verified SPEC merge revision is required.",
                )
            else:
                self.default_revision = data["revision"]
        task["lifecycle"] = "verified"

    def on_child_archived(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        task = self.task(data.get("task_id"), f"{path}.data.task_id")
        if task is not None:
            if task["lifecycle"] != "verified":
                self.add(
                    "archive_before_verification",
                    path,
                    "A child may be archived only after verification.",
                )
            task["lifecycle"] = "archived"

    def _current_spec_task(self, spec_id: Any, path: str) -> dict[str, Any] | None:
        if not _text(spec_id) or spec_id not in self.spec_state:
            self.add("unknown_spec", path, "Event names an unknown SPEC.")
            return None
        task_id = self.spec_state[spec_id]["task_id"]
        return self.task(task_id, path) if task_id is not None else None

    def on_checkpoint_passed(
        self, event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        checkpoint = self._checkpoint_result(data, path, status="passed")
        if checkpoint is not None:
            checkpoint["evidence"] = list(event["evidence"])

    def on_checkpoint_failed(
        self, event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        checkpoint = self._checkpoint_result(data, path, status="failed")
        if checkpoint is not None:
            checkpoint["evidence"] = list(event["evidence"])
        if not _text(data.get("reason")):
            self.add(
                "invalid_checkpoint_failure_reason",
                f"{path}.data.reason",
                "Failed checkpoint needs a reason.",
            )

    def on_default_branch_verified(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        spec_id = data.get("spec_id")
        task = self._current_spec_task(spec_id, f"{path}.data.spec_id")
        if not self.pending_specs or spec_id != self.pending_specs[0]:
            self.add(
                "spec_close_order", path, "Only the current pending SPEC may close."
            )
        if task is not None and task["lifecycle"] != "archived":
            self.add(
                "branch_check_before_archive",
                path,
                "Default-branch health follows task archival.",
            )
        if data.get("revision") != self.default_revision:
            self.add(
                "branch_revision_mismatch",
                path,
                "Health check must use the verified merge revision.",
            )
        if spec_id in self.spec_state:
            status = self.spec_state[spec_id]
            status["branch_verified"] = True
        if self.pending_specs and spec_id == self.pending_specs[0]:
            self.pending_specs.pop(0)

    def on_blocker_opened(
        self, event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        repair_id = data.get("repair_task_id")
        parent_id = data.get("parent_task_id")
        if (
            not _text(repair_id)
            or repair_id in self.tasks
            or not _nullable_text(parent_id)
        ):
            self.add(
                "invalid_blocker_task",
                path,
                "Blocker requires a fresh repair task and optional known parent.",
            )
            return
        if self.blocker is not None:
            self.add(
                "overlapping_blocker", path, "Only one blocker repair may be active."
            )
        action_issues = _action_issues(
            data.get("blocked_action"), f"{path}.data.blocked_action"
        )
        self.issues.extend(action_issues)
        if not _text(data.get("fingerprint")):
            self.add(
                "invalid_blocker_fingerprint", path, "Blocker fingerprint is required."
            )
        spec_id = None
        if parent_id is not None:
            parent = self.task(parent_id, f"{path}.data.parent_task_id")
            if parent is not None:
                if parent["lifecycle"] != "active":
                    self.add(
                        "blocker_parent_ineligible",
                        path,
                        "Blocker parent must be active.",
                    )
                parent["lifecycle"] = "paused"
                spec_id = parent["spec_id"]
        self.tasks[repair_id] = {
            "id": repair_id,
            "kind": "repair",
            "spec_id": spec_id,
            "lifecycle": "active",
        }
        self.blocker = {
            "repair_task_id": repair_id,
            "parent_task_id": parent_id,
            "fingerprint": data.get("fingerprint"),
            "blocked_action": data.get("blocked_action"),
        }
        self.role_limited_tasks.append(
            {
                "task_id": repair_id,
                "spec_id": spec_id,
                "parent_task_id": parent_id,
                "role": "blocker_repair",
                "writes_product_code": False,
                "merge_commits": [],
                "evidence": list(event["evidence"]),
            }
        )

    def on_blocked_action_resumed(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        if self.blocker is None:
            self.add(
                "resume_without_blocker", path, "No blocker is available to resume."
            )
            return
        for field in ("repair_task_id", "parent_task_id"):
            if data.get(field) != self.blocker[field]:
                self.add(
                    "blocker_identity_mismatch",
                    f"{path}.data.{field}",
                    "Resume must match the open blocker.",
                )
        if data.get("action") != self.blocker["blocked_action"]:
            self.add(
                "resume_action_mismatch",
                f"{path}.data.action",
                "Resume must return to the exact blocked action.",
            )
        repair = self.tasks[self.blocker["repair_task_id"]]
        if repair["lifecycle"] != "archived":
            self.add(
                "repair_not_archived",
                path,
                "Repair must be verified and archived before resumption.",
            )
        parent_id = self.blocker["parent_task_id"]
        if parent_id is not None and parent_id in self.tasks:
            parent = self.tasks[parent_id]
            if parent["lifecycle"] != "paused":
                self.add(
                    "blocked_parent_not_paused",
                    path,
                    "Blocked child must remain paused during repair.",
                )
            parent["lifecycle"] = "active"
        self.blocker = None

    def on_controller_resumed(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        action_issues = _action_issues(
            data.get("persisted_next_action"), f"{path}.data.persisted_next_action"
        )
        self.issues.extend(action_issues)
        observed = data.get("observed_task_ids")
        if not _string_list(observed) or observed != sorted(self.tasks):
            self.add(
                "task_tree_not_reconciled",
                f"{path}.data.observed_task_ids",
                "Recovery must read the exact sorted task tree.",
            )
        if self.recovery is not None:
            self.add("nested_recovery", path, "Recovery is already in progress.")
        reconnect = {
            task_id
            for task_id, task in self.tasks.items()
            if task["lifecycle"] != "archived"
        }
        self.recovery = {
            "action": data.get("persisted_next_action"),
            "remaining": reconnect,
        }

    def on_child_reconnected(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        task_id = data.get("task_id")
        if self.recovery is None or task_id not in self.recovery["remaining"]:
            self.add(
                "unexpected_reconnect",
                path,
                "Reconnect must name one recorded unarchived child exactly once.",
            )
            return
        task = self.tasks[task_id]
        route_receipt = data.get("route_receipt")
        if task["kind"] == "spec":
            status = self.spec_state[task["spec_id"]]
            if route_receipt != status["route_receipt"]:
                self.add(
                    "recovery_route_mismatch",
                    f"{path}.data.route_receipt",
                    "Recovery must retain the exact persisted SPEC route receipt.",
                )
            self.issues.extend(
                _route_receipt_issues(
                    route_receipt,
                    f"{path}.data.route_receipt",
                    run_id=self.run_id,
                    target=task["spec_id"],
                    task_id=task_id,
                    selection=status["route_selection"],
                    model=status["model"],
                    thinking=status["thinking"],
                    planning_record_sha256=self.planning_record_sha256,
                )
            )
        elif route_receipt is not None:
            self.add(
                "unexpected_recovery_route",
                f"{path}.data.route_receipt",
                "Only SPEC children may carry a persisted route receipt.",
            )
        self.recovery["remaining"].remove(task_id)

    def on_stored_action_resumed(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        if self.recovery is None:
            self.add(
                "resume_without_recovery",
                path,
                "No persisted recovery action is pending.",
            )
            return
        if self.recovery["remaining"]:
            self.add(
                "children_not_reconnected",
                path,
                "Reconnect every recorded child before resuming.",
            )
        if data.get("action") != self.recovery["action"]:
            self.add(
                "stored_action_mismatch",
                path,
                "Recovery must resume the exact persisted action.",
            )
        action_issues = _action_issues(data.get("action"), f"{path}.data.action")
        self.issues.extend(action_issues)
        if not action_issues:
            self.pending_action = dict(data["action"])
            self.pending_action_source = "recovery"
        self.recovery = None

    def _release_identity(
        self, data: dict[str, Any], path: str, *, package: bool = False
    ) -> bool:
        valid = True
        if data.get("revision") != self.candidate_revision:
            self.add(
                "release_revision_mismatch",
                path,
                "Release action must use the frozen candidate revision.",
            )
            valid = False
        if "artifact_id" in data and data.get("artifact_id") != self.artifact_id:
            self.add(
                "artifact_identity_mismatch",
                path,
                "Release action must reuse the one built artifact.",
            )
            valid = False
        if package and data.get("package_id") != self.package_id:
            self.add(
                "package_identity_mismatch",
                path,
                "Deployment must reuse the verified package.",
            )
            valid = False
        return valid

    def on_release_candidate_frozen(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        if self.pending_specs or any(
            task["lifecycle"] != "archived" for task in self.tasks.values()
        ):
            self.add(
                "release_before_specs_complete",
                path,
                "Release starts only after every SPEC and child is complete.",
            )
        due_checkpoint = self._due_checkpoint()
        if due_checkpoint is not None:
            self.add(
                "release_before_checkpoint",
                path,
                "Release starts only after every due L4 checkpoint is green.",
            )
        if data.get("revision") != self.default_revision or not _text(
            data.get("revision")
        ):
            self.add(
                "candidate_revision_mismatch",
                path,
                "Candidate must be the latest verified default revision.",
            )
        if self.candidate_revision is not None:
            self.add(
                "candidate_refrozen", path, "Release candidate may be frozen once."
            )
        self.candidate_revision = data.get("revision")

    def on_artifact_built(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        if self.candidate_revision is None:
            self.add(
                "build_before_freeze",
                path,
                "Freeze the release candidate before building.",
            )
        if self.artifact_id is not None:
            self.add(
                "artifact_rebuilt",
                path,
                "The standard artifact must be built exactly once.",
            )
        if data.get("revision") != self.candidate_revision or not _text(
            data.get("artifact_id")
        ):
            self.add(
                "invalid_artifact",
                path,
                "Artifact must identify the frozen revision and immutable artifact.",
            )
        self.artifact_id = data.get("artifact_id")

    def on_final_tests_passed(
        self, event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        if self.artifact_id is None:
            self.add(
                "tests_before_build",
                path,
                "Final train runs against the built candidate artifact.",
            )
        self._release_identity(data, path)
        if self.tests_passed:
            self.add(
                "final_tests_repeated",
                path,
                "Final release train may be recorded once.",
            )
        candidate_revisions = data.get("candidate_revisions")
        if not _string_list(candidate_revisions, nonempty=True):
            self.add(
                "invalid_final_candidate_revisions",
                f"{path}.data.candidate_revisions",
                "Final L4 candidate revisions are required.",
            )
            candidate_revisions = []
        if data.get("revision") not in candidate_revisions:
            self.add(
                "final_candidate_revision_mismatch",
                path,
                "Final candidate revisions must include the tested revision.",
            )
        last_checkpoint = self._last_checkpoint()
        reused_checkpoint = data.get("l4_reused_checkpoint")
        if last_checkpoint is None or last_checkpoint["status"] != "passed":
            self.add(
                "final_checkpoint_missing",
                path,
                "The final checkpoint must pass before final tests can reuse or rerun L4.",
            )
        elif reused_checkpoint is None:
            if _candidate_revision_set(candidate_revisions) == _candidate_revision_set(
                last_checkpoint["candidate_revisions"]
            ):
                self.add(
                    "final_l4_duplicate",
                    path,
                    "Do not repeat final L4 when the final checkpoint candidate revisions are exact.",
                )
            self.release_l4 = {
                "mode": "rerun_final",
                "checkpoint_id": None,
                "candidate_revisions": sorted(candidate_revisions or []),
                "evidence": list(event["evidence"]),
            }
        elif reused_checkpoint != last_checkpoint["id"]:
            self.add(
                "final_checkpoint_reuse_mismatch",
                f"{path}.data.l4_reused_checkpoint",
                "Final L4 reuse must name the last checkpoint.",
            )
        elif _candidate_revision_set(candidate_revisions) != _candidate_revision_set(
            last_checkpoint["candidate_revisions"]
        ):
            self.add(
                "stale_final_checkpoint_revisions",
                f"{path}.data.candidate_revisions",
                "Final checkpoint L4 can be reused only for the exact same candidate revisions.",
            )
        else:
            self.release_l4 = {
                "mode": "reused_checkpoint",
                "checkpoint_id": reused_checkpoint,
                "candidate_revisions": sorted(candidate_revisions or []),
                "evidence": list(event["evidence"]),
            }
        self.tests_passed = True

    def on_package_completed(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        if not self.tests_passed:
            self.add(
                "package_before_tests",
                path,
                "Packaging follows the final release train.",
            )
        self._release_identity(data, path)
        if self.package_id is not None or not _text(data.get("package_id")):
            self.add(
                "invalid_package", path, "Exactly one immutable package is required."
            )
        self.package_id = data.get("package_id")
        self.deployment_status = "packaged"

    def on_deployment_completed(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        if self.package_id is None:
            self.add("deploy_before_package", path, "Deployment follows packaging.")
        self._release_identity(data, path, package=True)
        if self.deployment_status != "packaged" or not _text(data.get("target")):
            self.add(
                "invalid_deployment",
                path,
                "Deployment needs one configured target and package.",
            )
        self.deployment_status = "deployed"
        self.deployment_target = data.get("target")

    def on_smoke_passed(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        self._release_identity(data, path)
        if (
            self.deployment_status != "deployed"
            or data.get("target") != self.deployment_target
        ):
            self.add(
                "smoke_before_deploy",
                path,
                "Smoke verification must target the completed deployment.",
            )
        if self.smoke_passed:
            self.add("smoke_repeated", path, "Smoke verification may be recorded once.")
        self.smoke_passed = True

    def on_deployment_not_applicable(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        if self.package_id is None or not self.tests_passed:
            self.add(
                "not_applicable_before_package",
                path,
                "Inapplicability is recorded after tests and packaging.",
            )
        self._release_identity(data, path, package=True)
        if self.deployment_status != "packaged" or not _text(data.get("reason")):
            self.add(
                "invalid_not_applicable",
                path,
                "Deployment inapplicability requires explicit evidence and reason.",
            )
        self.deployment_status = "not_applicable"

    def on_child_paused(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        task = self.task(data.get("task_id"), f"{path}.data.task_id")
        if task is not None:
            if task["lifecycle"] != "active":
                self.add(
                    "pause_ineligible", path, "Only an active child may be paused."
                )
            task["lifecycle"] = "paused"

    def on_blocker_stopped(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        if self.blocker is None:
            self.add(
                "stop_without_blocker",
                path,
                "Terminal blocker requires an open repair route.",
            )
            return
        for field in ("repair_task_id", "parent_task_id", "fingerprint"):
            if data.get(field) != self.blocker[field]:
                self.add(
                    "blocker_identity_mismatch",
                    f"{path}.data.{field}",
                    "Stopped blocker must match the open blocker.",
                )
        if data.get("resume_action") != self.blocker["blocked_action"]:
            self.add(
                "resume_action_mismatch",
                f"{path}.data.resume_action",
                "Blocked terminal must preserve the exact action.",
            )
        for field in ("attempts", "same_fingerprint_count"):
            value = data.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 3:
                self.add(
                    "stopping_rule_not_met",
                    f"{path}.data.{field}",
                    "Stopping rule requires three matching failed turns.",
                )
        if (
            data.get("external_authority_required") is not True
            or data.get("no_safe_action") is not True
        ):
            self.add(
                "stopping_rule_not_met",
                path,
                "Blocker must require external state with no safe in-scope action.",
            )
        repair = self.tasks[self.blocker["repair_task_id"]]
        repair["lifecycle"] = "paused"
        self.resume_action = data.get("resume_action")
        self.controller_state = "terminal_blocked"

    def on_user_stop_recorded(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        action_issues = _action_issues(
            data.get("resume_action"), f"{path}.data.resume_action"
        )
        self.issues.extend(action_issues)
        executable = [
            task["id"]
            for task in self.tasks.values()
            if task["lifecycle"] in {"queued", "active", "handoff_received", "verified"}
        ]
        if executable:
            self.add(
                "stop_with_executable_children",
                path,
                "Pause active children before recording a user stop.",
            )
        self.resume_action = data.get("resume_action")
        self.controller_state = "user_stopped"

    def on_terminal_ready(
        self, _event: dict[str, Any], data: dict[str, Any], path: str
    ) -> None:
        if (
            data.get("controller_state") != "terminal_success"
            or data.get("next_action") is not None
        ):
            self.add(
                "invalid_terminal_ready",
                path,
                "Successful terminal state requires an empty next action.",
            )
        if data.get("candidate_revision") != self.candidate_revision:
            self.add(
                "terminal_candidate_mismatch",
                path,
                "Terminal candidate must match the frozen revision.",
            )
        if self.pending_specs or any(
            task["lifecycle"] != "archived" for task in self.tasks.values()
        ):
            self.add(
                "terminal_work_remaining",
                path,
                "All SPECs and child tasks must be complete and archived.",
            )
        if not self.tests_passed or self.package_id is None:
            self.add(
                "terminal_release_incomplete",
                path,
                "Final tests and package evidence are required.",
            )
        if self._due_checkpoint() is not None:
            self.add(
                "terminal_checkpoint_incomplete",
                path,
                "Terminal success requires every due L4 checkpoint to pass.",
            )
        if self.release_l4 is None:
            self.add(
                "terminal_l4_release_missing",
                path,
                "Terminal success requires final L4 reuse or rerun evidence.",
            )
        if self.deployment_status == "deployed" and not self.smoke_passed:
            self.add(
                "terminal_smoke_missing",
                path,
                "A deployed release requires smoke evidence.",
            )
        if self.deployment_status not in {"deployed", "not_applicable"}:
            self.add(
                "terminal_deployment_incomplete",
                path,
                "Deployment or explicit inapplicability is required.",
            )
        self.controller_state = "terminal_success"

    def next_action(self) -> dict[str, str] | None:
        if self.controller_state != "active":
            return None
        if self.pending_action is not None:
            return self.pending_action
        if self.recovery is not None:
            return {
                "kind": "resume",
                "target": "controller-recovery",
                "instruction": "Reconnect every recorded child and execute the persisted next action.",
            }
        if self.blocker is not None:
            repair = self.tasks[self.blocker["repair_task_id"]]
            actions = {
                "active": ("wait", "Wait for the focused repair task."),
                "handoff_received": (
                    "verify",
                    "Verify the repair handoff independently.",
                ),
                "verified": ("archive", "Archive the verified repair task."),
                "archived": (
                    "resume",
                    "Resume the exact action saved in the blocker packet.",
                ),
                "paused": (
                    "wait",
                    "Continue the blocker protocol or record an objective stop.",
                ),
            }
            kind, instruction = actions[repair["lifecycle"]]
            target = (
                repair["id"]
                if kind in {"wait", "verify", "archive"}
                else self.blocker["parent_task_id"]
                or self.blocker["blocked_action"]["target"]
            )
            return {"kind": kind, "target": target, "instruction": instruction}
        current_tasks = [
            task
            for task in self.tasks.values()
            if task["kind"] == "spec" and task["lifecycle"] != "archived"
        ]
        if current_tasks:
            task = current_tasks[0]
            actions = {
                "queued": ("wait", "Wait for the current SPEC task."),
                "active": ("wait", "Wait for the current SPEC task."),
                "paused": ("resume", "Resume the paused SPEC task."),
                "handoff_received": (
                    "verify",
                    "Verify the SPEC merge and evidence handoff independently.",
                ),
                "verified": ("archive", "Archive the verified SPEC task."),
            }
            kind, instruction = actions[task["lifecycle"]]
            return {"kind": kind, "target": task["id"], "instruction": instruction}
        due_checkpoint = self._due_checkpoint()
        if due_checkpoint is not None:
            return {
                "kind": "verify",
                "target": due_checkpoint["id"],
                "instruction": "Run the due release-train L4 checkpoint.",
            }
        if self.pending_specs:
            spec_id = self.pending_specs[0]
            status = self.spec_state[spec_id]
            task_id = status["task_id"]
            if task_id is not None:
                return {
                    "kind": "verify",
                    "target": spec_id,
                    "instruction": "Verify default-branch health and close the SPEC.",
                }
            return {
                "kind": "dispatch",
                "target": spec_id,
                "instruction": "Create the next SPEC task from the latest default branch.",
            }
        if self.candidate_revision is None:
            return {
                "kind": "advance",
                "target": "release-candidate",
                "instruction": "Freeze the exact integrated release candidate.",
            }
        if self.artifact_id is None:
            return {
                "kind": "advance",
                "target": "build",
                "instruction": "Build the standard candidate artifact once.",
            }
        if not self.tests_passed:
            return {
                "kind": "advance",
                "target": "final-tests",
                "instruction": "Run the final release train against the candidate artifact.",
            }
        if self.package_id is None:
            return {
                "kind": "advance",
                "target": "package",
                "instruction": "Package the already-tested artifact.",
            }
        if self.deployment_status == "packaged":
            return {
                "kind": "advance",
                "target": "deployment",
                "instruction": "Deploy the package or record verified inapplicability.",
            }
        if self.deployment_status == "deployed" and not self.smoke_passed:
            return {
                "kind": "advance",
                "target": "smoke",
                "instruction": "Smoke-test the deployed artifact.",
            }
        return {
            "kind": "advance",
            "target": "terminal-gates",
            "instruction": "Persist terminal state and run lifecycle and terminal gates.",
        }

    def finish(self) -> None:
        if self.pending_action is not None:
            self.add(
                "continuation_missing",
                "$end",
                "Commentary or recovery action was not executed in the same run.",
            )
        if self.recovery is not None:
            self.add(
                "recovery_incomplete",
                "$end",
                "Recovery did not reconnect children and resume the stored action.",
            )

    def child_tasks(self) -> list[dict[str, Any]]:
        return [
            {key: task[key] for key in ("id", "kind", "spec_id", "lifecycle")}
            for task in sorted(self.tasks.values(), key=lambda item: item["id"])
        ]

    def implementation_ownership(self) -> dict[str, Any]:
        specs: list[dict[str, Any]] = []
        for spec in self.specs:
            spec_id = spec["id"]
            status = self.spec_state[spec_id]
            task_id = status["task_id"]
            if task_id is None:
                continue
            tickets = []
            for ticket_id in status["ticket_order"]:
                ticket = status["tickets"][ticket_id]
                tickets.append(
                    {
                        "id": ticket_id,
                        "owner_task_id": ticket["owner_task_id"] or task_id,
                        "blocked_by": list(ticket["blocked_by"]),
                        "commits": list(ticket["commits"]),
                        "test_evidence": list(ticket["test_evidence"]),
                        "tracker_state": ticket["tracker_state"],
                    }
                )
            specs.append(
                {
                    "spec_id": spec_id,
                    "implementation_task_id": task_id,
                    "owners": list(status["owners"]),
                    "repositories": list(status["repositories"]),
                    "route": {
                        "target": spec_id,
                        "task_id": task_id,
                        "selection": status["route_selection"] or "recommended",
                        "model": status["model"],
                        "thinking": status["thinking"],
                        "receipt": status["route_receipt"],
                    },
                    "tickets": tickets,
                }
            )
        return {
            "specs": specs,
            "ticket_implementation_artifacts": {
                key: list(values)
                for key, values in self.ticket_implementation_artifacts.items()
            },
            "role_limited_tasks": list(self.role_limited_tasks),
        }

    def release_status(self) -> str:
        if self.deployment_status in {"deployed", "not_applicable", "packaged"}:
            return self.deployment_status
        if self.artifact_id is not None:
            return "building"
        return "pending"


def evaluate(
    log_path: Path, expected_run_id: str, expected_state: str
) -> tuple[dict[str, Any], int]:
    """Return a deterministic replay receipt and process exit code."""
    events, raw, issues = _read_log(log_path)
    if MODEL_POLICY_ERROR is not None:
        issues.append(
            _issue(
                "model_policy_invalid",
                "$.model_policy",
                f"Implement Needs model policy is invalid: {MODEL_POLICY_ERROR}.",
            )
        )
    if expected_state not in CONTROLLER_STATES:
        issues.append(
            _issue(
                "invalid_expected_state",
                "$arguments.expected_state",
                "Expected state is not part of the controller contract.",
            )
        )
    replay = _Replay(expected_run_id)
    for index, event in enumerate(events):
        shape = _event_shape_issues(event, index, expected_run_id)
        issues.extend(shape)
        if not shape:
            replay.apply(event, index)
    replay.finish()
    issues.extend(replay.issues)
    if replay.controller_state != expected_state:
        issues.append(
            _issue(
                "controller_state_mismatch",
                "$derived.controller_state",
                f"Lifecycle derives {replay.controller_state}, not {expected_state}.",
            )
        )
    issues = _sorted(issues)
    next_action = replay.next_action()
    if issues:
        mismatch_only = {item["code"] for item in issues} == {
            "controller_state_mismatch"
        }
        payload: dict[str, Any] = {
            "schema_version": 1,
            "decision": "reject",
            "run_id": expected_run_id,
            "derived_controller_state": replay.controller_state,
            "reasons": issues,
            "next_action": next_action
            if mismatch_only and next_action is not None
            else REPAIR_LOG_ACTION,
        }
        payload["decision_sha256"] = _decision_hash(payload)
        return payload, 1
    assert raw is not None
    payload = {
        "schema_version": 1,
        "decision": "allow",
        "run_id": expected_run_id,
        "derived_controller_state": replay.controller_state,
        "last_sequence": len(events),
        "next_action": next_action,
        "resume_action": replay.resume_action,
        "child_tasks": replay.child_tasks(),
        "implementation_ownership": replay.implementation_ownership(),
        "pending_specs": replay.pending_specs,
        "candidate_revision": replay.candidate_revision,
        "test_status": "passed" if replay.tests_passed else "pending",
        "l4_checkpoints": {
            "checkpoint_size": replay.checkpoint_size,
            "ordered_specs": [spec["id"] for spec in replay.specs],
            "completed_spec_count": replay._completed_spec_count(),
            "checkpoints": list(replay.checkpoint_state.values()),
            "release_l4": replay.release_l4,
        },
        "release_status": replay.release_status(),
        "lifecycle_log_sha256": _sha256(raw),
        "evidence_count": replay.evidence_count,
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
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument(
        "--expected-state", required=True, choices=sorted(CONTROLLER_STATES)
    )
    parser.add_argument("--receipt", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload, exit_code = evaluate(args.log, args.expected_run_id, args.expected_state)
    serialized = _serialize(payload)
    if args.receipt is not None:
        try:
            _write_atomic(args.receipt, serialized)
        except OSError as exc:
            payload = {
                "schema_version": 1,
                "decision": "reject",
                "run_id": args.expected_run_id,
                "derived_controller_state": payload.get("derived_controller_state"),
                "reasons": [
                    _issue(
                        "receipt_unwritable",
                        str(args.receipt),
                        f"Cannot persist lifecycle receipt: {exc.__class__.__name__}.",
                    )
                ],
                "next_action": REPAIR_LOG_ACTION,
            }
            payload["decision_sha256"] = _decision_hash(payload)
            serialized = _serialize(payload)
            exit_code = 1
    print(serialized, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
