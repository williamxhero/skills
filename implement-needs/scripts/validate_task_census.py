"""Fail closed unless every controller-owned Codex child is actually archived."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--task-tree", type=Path)
    parser.add_argument("--census", type=Path, required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    errors: list[str] = []
    try:
        state = json.loads(args.state.read_text(encoding="utf-8"))
        census = json.loads(args.census.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        state, census = {}, {}
        errors.append(f"unreadable input: {exc.__class__.__name__}")
    if state.get("run_id") != args.expected_run_id or census.get("run_id") != args.expected_run_id:
        errors.append("run_id mismatch")
    recorded = {x.get("id") for x in state.get("child_tasks", []) if isinstance(x, dict)}
    if args.task_tree:
        try:
            task_tree = json.loads(args.task_tree.read_text(encoding="utf-8"))
            tree_tasks = task_tree.get("tasks", [])
            tree_ids = {x.get("id") for x in tree_tasks if isinstance(x, dict)}
            if tree_ids != recorded:
                errors.append("task-tree IDs do not exactly match controller child_tasks")
            for task in tree_tasks:
                if isinstance(task, dict) and task.get("lifecycle") != "archived":
                    errors.append(f"{task.get('id', '<missing>')}: task-tree lifecycle is not archived")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"unreadable task-tree: {exc.__class__.__name__}")
    tasks = census.get("tasks", [])
    if not isinstance(tasks, list):
        tasks = []
        errors.append("tasks must be an array")
    discovered = {x.get("id") for x in tasks if isinstance(x, dict)}
    if recorded != discovered:
        errors.append("discovered tasks do not exactly match controller child_tasks")
    for task in tasks:
        if not isinstance(task, dict):
            errors.append("invalid task entry")
            continue
        task_id = task.get("id", "<missing>")
        if task.get("archived") is not True:
            errors.append(f"{task_id}: not archived")
        if task.get("lifecycle") != "archived":
            errors.append(f"{task_id}: census lifecycle is not archived")
        if not task.get("archive_operation_evidence"):
            errors.append(f"{task_id}: missing archive operation evidence")
        if not task.get("archive_readback_evidence"):
            errors.append(f"{task_id}: missing archive readback evidence")
    receipt = {"schema_version": 1, "gate": "task_census", "run_id": args.expected_run_id,
               "decision": "allow" if not errors else "reject", "errors": errors,
               "recorded_task_ids": sorted(recorded), "discovered_task_ids": sorted(discovered)}
    args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
