#!/usr/bin/env python3
"""Fail-closed terminal validator for the Implement Needs controller."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = SKILL_ROOT / "references" / "controller-state.schema.json"
TERMINAL_STATES = {"terminal_success", "terminal_blocked", "user_stopped"}
GOAL_STATUSES = {"unchanged", "complete", "blocked"}

REPAIR_STATE_ACTION = {
    "kind": "repair_state",
    "target": "controller-state.json",
    "instruction": "Rebuild controller state from the delivery map and current task tree, then rerun the terminal validator.",
}
REFRESH_STATE_ACTION = {
    "kind": "refresh_state",
    "target": "controller-state.json",
    "instruction": "Refresh source snapshots and fingerprints, persist a new state revision, then rerun the terminal validator.",
}


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _sorted(issues: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(
        issues, key=lambda item: (item["path"], item["code"], item["message"])
    )


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_bytes(path: Path, label: str, issues: list[dict[str, str]]) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError as exc:
        issues.append(
            _issue(
                f"{label}_unreadable",
                str(path),
                f"Cannot read required artifact: {exc.__class__.__name__}.",
            )
        )
        return None


def _read_json(
    path: Path, label: str, issues: list[dict[str, str]]
) -> tuple[Any, bytes | None]:
    raw = _read_bytes(path, label, issues)
    if raw is None:
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


def _resolve_ref(root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise ValueError("Only local JSON Schema references are supported.")
    value: Any = root
    for token in reference[2:].split("/"):
        value = value[token.replace("~1", "/").replace("~0", "~")]
    if not isinstance(value, dict):
        raise TypeError("JSON Schema reference did not resolve to an object.")
    return value


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "null": value is None,
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
    }.get(expected, False)


def _schema_accepts_type(
    value: Any, schema: dict[str, Any], root: dict[str, Any]
) -> bool:
    if "$ref" in schema:
        return _schema_accepts_type(value, _resolve_ref(root, schema["$ref"]), root)
    expected = schema.get("type")
    if isinstance(expected, str):
        return _matches_type(value, expected)
    if isinstance(expected, list):
        return any(_matches_type(value, item) for item in expected)
    return True


def _schema_issues(
    value: Any, schema: dict[str, Any], root: dict[str, Any], path: str
) -> list[dict[str, str]]:
    """Evaluate the deterministic JSON Schema subset used by this skill."""
    if "$ref" in schema:
        return _schema_issues(value, _resolve_ref(root, schema["$ref"]), root, path)

    if "oneOf" in schema:
        branches = [
            (branch, _schema_issues(value, branch, root, path))
            for branch in schema["oneOf"]
        ]
        passing = [errors for _, errors in branches if not errors]
        if len(passing) == 1:
            return []
        type_matches = [
            errors
            for branch, errors in branches
            if _schema_accepts_type(value, branch, root)
        ]
        if not passing and type_matches:
            return min(
                type_matches,
                key=lambda errors: (len(errors), json.dumps(errors, sort_keys=True)),
            )
        return [
            _issue(
                "invalid_one_of", path, "Value must match exactly one allowed shape."
            )
        ]

    issues: list[dict[str, str]] = []
    expected = schema.get("type")
    if isinstance(expected, str) and not _matches_type(value, expected):
        return [_issue("invalid_type", path, f"Expected JSON type {expected}.")]
    if isinstance(expected, list) and not any(
        _matches_type(value, item) for item in expected
    ):
        return [_issue("invalid_type", path, "Value has no allowed JSON type.")]

    if "const" in schema and value != schema["const"]:
        issues.append(
            _issue("invalid_const", path, "Value does not match the required constant.")
        )
    if "enum" in schema and not any(value == allowed for allowed in schema["enum"]):
        issues.append(
            _issue("invalid_enum", path, "Value is not in the allowed enumeration.")
        )
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and "minimum" in schema
        and value < schema["minimum"]
    ):
        issues.append(
            _issue(
                "below_minimum", path, f"Value must be at least {schema['minimum']}."
            )
        )
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            issues.append(
                _issue(
                    "invalid_string",
                    path,
                    "String is shorter than the contract permits.",
                )
            )
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            code = "invalid_sha256" if "sha256" in path else "pattern_mismatch"
            issues.append(
                _issue(code, path, "String does not match the required pattern.")
            )
    if isinstance(value, list):
        if schema.get("uniqueItems"):
            encoded = [
                json.dumps(
                    item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                for item in value
            ]
            if len(encoded) != len(set(encoded)):
                issues.append(
                    _issue("duplicate_item", path, "Array entries must be unique.")
                )
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                issues.extend(
                    _schema_issues(item, item_schema, root, f"{path}[{index}]")
                )
    if isinstance(value, dict):
        required = schema.get("required", [])
        for field in sorted(set(required) - set(value)):
            issues.append(
                _issue("missing_field", f"{path}.{field}", "Required field is missing.")
            )
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for field in sorted(set(value) - set(properties)):
                issues.append(
                    _issue(
                        "unknown_field",
                        f"{path}.{field}",
                        "Unknown field is not allowed.",
                    )
                )
        for field, field_schema in properties.items():
            if field in value:
                issues.extend(
                    _schema_issues(value[field], field_schema, root, f"{path}.{field}")
                )
    return _sorted(issues)


def _terminal_record_issues(
    terminal: Any, stopping: bool, user_stop: bool
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not isinstance(terminal, dict):
        return [
            _issue(
                "terminal_record_missing",
                "$.terminal",
                "Terminal state requires a terminal record.",
            )
        ]
    if not terminal["evidence"]:
        issues.append(
            _issue(
                "terminal_evidence_missing",
                "$.terminal.evidence",
                "Terminal evidence is required.",
            )
        )
    if terminal["stopping_rule_met"] is not stopping:
        issues.append(
            _issue(
                "terminal_stopping_rule_mismatch",
                "$.terminal.stopping_rule_met",
                "Stopping-rule assertion does not match terminal state.",
            )
        )
    if terminal["user_stop_recorded"] is not user_stop:
        issues.append(
            _issue(
                "terminal_user_stop_mismatch",
                "$.terminal.user_stop_recorded",
                "User-stop assertion does not match terminal state.",
            )
        )
    return issues


def _state_consistency_issues(state: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    child_ids = [child["id"] for child in state["child_tasks"]]
    if len(child_ids) != len(set(child_ids)):
        issues.append(
            _issue("duplicate_child_id", "$.child_tasks", "Child IDs must be unique.")
        )
    children = {child["id"]: child for child in state["child_tasks"]}

    expected_unarchived = {
        task_id for task_id, task in children.items() if task["lifecycle"] != "archived"
    }
    if set(state["unarchived_tasks"]) != expected_unarchived:
        issues.append(
            _issue(
                "unarchived_tasks_mismatch",
                "$.unarchived_tasks",
                "Aggregate must equal all non-archived child IDs.",
            )
        )
    expected_unverified = {
        task_id
        for task_id, task in children.items()
        if task["lifecycle"] == "handoff_received"
    }
    if set(state["unverified_handoffs"]) != expected_unverified:
        issues.append(
            _issue(
                "unverified_handoffs_mismatch",
                "$.unverified_handoffs",
                "Aggregate must equal all handoff_received child IDs.",
            )
        )

    stack = set(state["active_task_stack"])
    if any(
        task_id not in children or children[task_id]["lifecycle"] == "archived"
        for task_id in stack
    ):
        issues.append(
            _issue(
                "invalid_task_stack",
                "$.active_task_stack",
                "Stack IDs must name non-archived child tasks.",
            )
        )
    required_stack = {
        task_id
        for task_id, task in children.items()
        if task["lifecycle"] in {"queued", "active", "paused"}
    }
    if not required_stack.issubset(stack):
        issues.append(
            _issue(
                "active_task_missing_from_stack",
                "$.active_task_stack",
                "Every queued, active, or paused child must be on the stack.",
            )
        )
    for index, child in enumerate(state["child_tasks"]):
        if child["kind"] == "spec" and child["spec_id"] is None:
            issues.append(
                _issue(
                    "spec_id_missing",
                    f"$.child_tasks[{index}].spec_id",
                    "A SPEC child requires spec_id.",
                )
            )
    planning_tasks = [
        child for child in state["child_tasks"] if child["kind"] == "planning"
    ]
    if len(planning_tasks) > 1:
        issues.append(
            _issue(
                "planning_task_count",
                "$.child_tasks",
                "Controller state may record at most one planning task.",
            )
        )
    spec_owners: dict[str, list[str]] = {}
    for child in state["child_tasks"]:
        if child["kind"] == "spec" and child["spec_id"] is not None:
            spec_owners.setdefault(child["spec_id"], []).append(child["id"])
    for spec_id, task_ids in sorted(spec_owners.items()):
        if len(task_ids) > 1:
            issues.append(
                _issue(
                    "spec_task_owner_count",
                    "$.child_tasks",
                    f"SPEC {spec_id} has multiple implementation task owners: {', '.join(sorted(task_ids))}.",
                )
            )

    controller_state = state["controller_state"]
    phase = state["active_phase"]
    if controller_state == "active":
        if phase in {"blocked", "stopped", "complete"}:
            issues.append(
                _issue(
                    "active_phase_is_terminal",
                    "$.active_phase",
                    "Active state requires a non-terminal phase.",
                )
            )
        if state["next_action"] is None:
            issues.append(
                _issue(
                    "active_next_action_missing",
                    "$.next_action",
                    "Active state requires one next action.",
                )
            )
        if state["resume_action"] is not None or state["terminal"] is not None:
            issues.append(
                _issue(
                    "active_terminal_data_present",
                    "$",
                    "Active state cannot carry resume or terminal data.",
                )
            )
        return _sorted(issues)

    if state["next_action"] is not None:
        issues.append(
            _issue(
                "terminal_next_action",
                "$.next_action",
                "A terminal state cannot have a next action.",
            )
        )

    if controller_state == "terminal_success":
        if phase != "complete":
            issues.append(
                _issue(
                    "success_phase_mismatch",
                    "$.active_phase",
                    "terminal_success requires phase complete.",
                )
            )
        if state["resume_action"] is not None:
            issues.append(
                _issue(
                    "success_resume_action",
                    "$.resume_action",
                    "terminal_success cannot have a resume action.",
                )
            )
        for field, code in (
            ("active_task_stack", "terminal_active_task_stack"),
            ("pending_specs", "terminal_pending_specs"),
            ("unverified_handoffs", "terminal_unverified_handoffs"),
            ("unarchived_tasks", "terminal_unarchived_tasks"),
        ):
            if state[field]:
                issues.append(
                    _issue(
                        code, f"$.{field}", f"terminal_success requires empty {field}."
                    )
                )
        if any(task["lifecycle"] != "archived" for task in children.values()):
            issues.append(
                _issue(
                    "terminal_child_not_archived",
                    "$.child_tasks",
                    "Every child must be archived.",
                )
            )
        if not planning_tasks:
            issues.append(
                _issue(
                    "terminal_planning_task_missing",
                    "$.child_tasks",
                    "A planning task must be recorded.",
                )
            )
        if not any(task["kind"] == "spec" for task in children.values()):
            issues.append(
                _issue(
                    "terminal_spec_task_missing",
                    "$.child_tasks",
                    "At least one SPEC task must be recorded.",
                )
            )

        test_state = state["test_state"]
        if (
            test_state["status"] != "passed"
            or test_state["candidate_revision"] is None
            or not test_state["evidence"]
        ):
            issues.append(
                _issue(
                    "terminal_test_incomplete",
                    "$.test_state",
                    "Passed tests, candidate revision, and evidence are required.",
                )
            )
        release_state = state["release_state"]
        if (
            release_state["status"] not in {"deployed", "not_applicable"}
            or release_state["candidate_revision"] is None
            or not release_state["evidence"]
        ):
            issues.append(
                _issue(
                    "terminal_release_incomplete",
                    "$.release_state",
                    "Terminal release, candidate revision, and evidence are required.",
                )
            )
        if (
            test_state["candidate_revision"] is not None
            and release_state["candidate_revision"] is not None
            and test_state["candidate_revision"] != release_state["candidate_revision"]
        ):
            issues.append(
                _issue(
                    "candidate_revision_mismatch",
                    "$.release_state.candidate_revision",
                    "Test and release revisions must match.",
                )
            )
        issues.extend(_terminal_record_issues(state["terminal"], False, False))
        return _sorted(issues)

    expected_phase = "blocked" if controller_state == "terminal_blocked" else "stopped"
    if phase != expected_phase:
        issues.append(
            _issue(
                "terminal_phase_mismatch",
                "$.active_phase",
                f"{controller_state} requires phase {expected_phase}.",
            )
        )
    if state["resume_action"] is None:
        issues.append(
            _issue(
                "terminal_resume_action_missing",
                "$.resume_action",
                "Blocked and stopped states require a resume action.",
            )
        )
    forbidden = (
        {"queued", "active", "handoff_received", "verified"}
        if controller_state == "terminal_blocked"
        else {"queued", "active"}
    )
    if any(task["lifecycle"] in forbidden for task in children.values()):
        issues.append(
            _issue(
                "terminal_executable_child_work",
                "$.child_tasks",
                "Child work incompatible with terminal state remains.",
            )
        )
    issues.extend(
        _terminal_record_issues(
            state["terminal"],
            controller_state == "terminal_blocked",
            controller_state == "user_stopped",
        )
    )
    return _sorted(issues)


def _task_tree_consistency_issues(
    state: dict[str, Any], task_tree: dict[str, Any]
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    tree_ids = [task["id"] for task in task_tree["tasks"]]
    if len(tree_ids) != len(set(tree_ids)):
        issues.append(
            _issue(
                "duplicate_task_tree_id",
                "$task_tree.tasks",
                "Task-tree IDs must be unique.",
            )
        )
    if task_tree["run_id"] != state["run_id"]:
        issues.append(
            _issue(
                "task_tree_run_id_mismatch",
                "$task_tree.run_id",
                "Task tree belongs to another run.",
            )
        )
    state_tasks = {task["id"]: task for task in state["child_tasks"]}
    tree_tasks = {task["id"]: task for task in task_tree["tasks"]}
    if set(state_tasks) != set(tree_tasks):
        issues.append(
            _issue(
                "task_tree_task_set_mismatch",
                "$task_tree.tasks",
                "Task tree and child ledger must contain the same IDs.",
            )
        )
    for task_id in sorted(set(state_tasks) & set(tree_tasks)):
        if state_tasks[task_id] != tree_tasks[task_id]:
            issues.append(
                _issue(
                    "task_tree_task_mismatch",
                    f"$task_tree.tasks[{task_id}]",
                    "Observed task fields differ from state.",
                )
            )
    if tree_ids != sorted(tree_ids):
        issues.append(
            _issue(
                "task_tree_unsorted",
                "$task_tree.tasks",
                "Task-tree entries must be sorted by ID.",
            )
        )
    return _sorted(issues)


def _decision_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256(canonical)


def evaluate(
    state_path: Path,
    delivery_map_path: Path,
    task_tree_path: Path,
    expected_run_id: str,
    proposed_state: str,
    goal_status: str = "unchanged",
) -> tuple[dict[str, Any], int]:
    """Return a deterministic decision payload and process exit code."""
    issues: list[dict[str, str]] = []
    if proposed_state not in TERMINAL_STATES:
        issues.append(
            _issue(
                "invalid_proposed_state",
                "$arguments.proposed_state",
                "Proposed state must be terminal.",
            )
        )
    if goal_status not in GOAL_STATUSES:
        issues.append(
            _issue(
                "invalid_goal_status", "$arguments.goal_status", "Unknown goal status."
            )
        )

    schema, _ = _read_json(SCHEMA_PATH, "schema", issues)
    state, state_raw = _read_json(state_path, "state", issues)
    task_tree, task_tree_raw = _read_json(task_tree_path, "task_tree", issues)
    delivery_raw = _read_bytes(delivery_map_path, "delivery_map", issues)

    state_shape: list[dict[str, str]] = []
    tree_shape: list[dict[str, str]] = []
    if not isinstance(schema, dict) or not isinstance(
        schema.get("$defs", {}).get("taskTree"), dict
    ):
        issues.append(
            _issue(
                "schema_invalid",
                str(SCHEMA_PATH),
                "Schema lacks the taskTree contract.",
            )
        )
    else:
        try:
            if state is not None:
                state_shape = _schema_issues(state, schema, schema, "$")
                issues.extend(state_shape)
            if task_tree is not None:
                tree_shape = _schema_issues(
                    task_tree, schema["$defs"]["taskTree"], schema, "$task_tree"
                )
                issues.extend(tree_shape)
        except (KeyError, TypeError, ValueError) as exc:
            schema_issue = _issue(
                "schema_invalid",
                str(SCHEMA_PATH),
                f"Schema cannot be evaluated: {exc.__class__.__name__}.",
            )
            issues.append(schema_issue)
            state_shape = [schema_issue]
            tree_shape = [schema_issue]

    if isinstance(state, dict) and not state_shape and isinstance(schema, dict):
        issues.extend(_state_consistency_issues(state))
        if isinstance(task_tree, dict) and not tree_shape:
            issues.extend(_task_tree_consistency_issues(state, task_tree))
        if state["run_id"] != expected_run_id:
            issues.append(
                _issue(
                    "run_id_mismatch",
                    "$.run_id",
                    "State belongs to a different controller run.",
                )
            )
        if state["controller_state"] != proposed_state:
            issues.append(
                _issue(
                    "proposed_state_mismatch",
                    "$.controller_state",
                    "Proposed terminal state does not match state.",
                )
            )
        if delivery_raw is not None and state["freshness"][
            "delivery_map_sha256"
        ] != _sha256(delivery_raw):
            issues.append(
                _issue(
                    "stale_delivery_map",
                    "$.freshness.delivery_map_sha256",
                    "Delivery-map fingerprint is stale.",
                )
            )
        if task_tree_raw is not None and state["freshness"][
            "task_tree_sha256"
        ] != _sha256(task_tree_raw):
            issues.append(
                _issue(
                    "stale_task_tree",
                    "$.freshness.task_tree_sha256",
                    "Task-tree fingerprint is stale.",
                )
            )
        if (
            goal_status == "complete"
            and state["controller_state"] != "terminal_success"
        ):
            issues.append(
                _issue(
                    "goal_status_mismatch",
                    "$.controller_state",
                    "Goal complete requires terminal_success.",
                )
            )
        if goal_status == "blocked" and state["controller_state"] != "terminal_blocked":
            issues.append(
                _issue(
                    "goal_status_mismatch",
                    "$.controller_state",
                    "Goal blocked requires terminal_blocked.",
                )
            )
        if state["controller_state"] == "user_stopped" and goal_status != "unchanged":
            issues.append(
                _issue(
                    "goal_status_mismatch",
                    "$.controller_state",
                    "user_stopped requires unchanged goal status.",
                )
            )

    issues = _sorted(issues)
    if issues:
        codes = {item["code"] for item in issues}
        mismatch_codes = {"proposed_state_mismatch", "goal_status_mismatch"}
        stale_codes = {"stale_delivery_map", "stale_task_tree", "run_id_mismatch"}
        if (
            isinstance(state, dict)
            and not state_shape
            and state.get("controller_state") == "active"
            and isinstance(state.get("next_action"), dict)
            and codes <= mismatch_codes
        ):
            next_action = state["next_action"]
        elif codes & stale_codes and codes <= stale_codes | mismatch_codes:
            next_action = REFRESH_STATE_ACTION
        else:
            next_action = REPAIR_STATE_ACTION
        payload: dict[str, Any] = {
            "schema_version": 1,
            "decision": "reject",
            "required_controller_state": "active",
            "observed_controller_state": state.get("controller_state")
            if isinstance(state, dict)
            else None,
            "reasons": issues,
            "next_action": next_action,
        }
        payload["decision_sha256"] = _decision_hash(payload)
        return payload, 1

    assert isinstance(state, dict) and isinstance(task_tree, dict)
    assert (
        state_raw is not None and task_tree_raw is not None and delivery_raw is not None
    )
    payload = {
        "schema_version": 1,
        "decision": "allow",
        "run_id": state["run_id"],
        "state_revision": state["state_revision"],
        "terminal_state": state["controller_state"],
        "goal_status": goal_status,
        "state_sha256": _sha256(state_raw),
        "delivery_map_sha256": _sha256(delivery_raw),
        "task_tree_sha256": _sha256(task_tree_raw),
        "terminal_evidence": state["terminal"]["evidence"],
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
    parser.add_argument(
        "--state", required=True, type=Path, help="Path to controller-state.json"
    )
    parser.add_argument(
        "--delivery-map",
        required=True,
        type=Path,
        help="Path to the current delivery map",
    )
    parser.add_argument(
        "--task-tree",
        required=True,
        type=Path,
        help="Path to the current task-tree snapshot",
    )
    parser.add_argument(
        "--expected-run-id",
        required=True,
        help="Run ID expected by the invoking controller",
    )
    parser.add_argument(
        "--proposed-state", required=True, choices=sorted(TERMINAL_STATES)
    )
    parser.add_argument(
        "--goal-status", choices=sorted(GOAL_STATUSES), default="unchanged"
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        help="Optional path for the atomic JSON decision receipt",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload, exit_code = evaluate(
        args.state,
        args.delivery_map,
        args.task_tree,
        args.expected_run_id,
        args.proposed_state,
        args.goal_status,
    )
    serialized = _serialize(payload)
    if args.receipt is not None:
        try:
            _write_atomic(args.receipt, serialized)
        except OSError as exc:
            failure = {
                "schema_version": 1,
                "decision": "reject",
                "required_controller_state": "active",
                "observed_controller_state": payload.get("terminal_state")
                or payload.get("observed_controller_state"),
                "reasons": [
                    _issue(
                        "receipt_unwritable",
                        str(args.receipt),
                        f"Cannot persist receipt: {exc.__class__.__name__}.",
                    )
                ],
                "next_action": REPAIR_STATE_ACTION,
            }
            failure["decision_sha256"] = _decision_hash(failure)
            serialized = _serialize(failure)
            exit_code = 1
    print(serialized, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
