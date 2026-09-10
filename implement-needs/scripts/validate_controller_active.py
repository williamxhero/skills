#!/usr/bin/env python3
"""Validate a persisted active controller state before executing its next action."""
import argparse
import json
import sys
from pathlib import Path

import validate_controller_terminal as terminal


def evaluate(state_path: Path, delivery_map: Path, task_tree: Path, run_id: str):
    payload, _ = terminal.evaluate(state_path, delivery_map, task_tree, run_id, "terminal_success", "unchanged")
    reasons = payload.get("reasons", [])
    codes = {reason.get("code") for reason in reasons if isinstance(reason, dict)}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    expected = state.get("next_action") if isinstance(state, dict) else None
    valid = (
        payload.get("decision") == "reject"
        and payload.get("required_controller_state") == "active"
        and payload.get("observed_controller_state") == "active"
        and codes == {"proposed_state_mismatch"}
        and payload.get("next_action") == expected
        and isinstance(expected, dict)
    )
    return {"schema_version": 1, "decision": "continue" if valid else "repair", "next_action": expected if valid else payload.get("next_action"), "reasons": [] if valid else reasons}, 0 if valid else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--delivery-map", type=Path, required=True)
    parser.add_argument("--task-tree", type=Path, required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    try:
        payload, code = evaluate(args.state, args.delivery_map, args.task_tree, args.expected_run_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        payload, code = {"schema_version": 1, "decision": "repair", "next_action": None, "reasons": [{"code": "active_state_unreadable", "message": str(exc)}]}, 1
    output = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    if args.receipt:
        terminal._write_atomic(args.receipt, output)
    print(output, end="")
    return code


if __name__ == "__main__":
    sys.exit(main())
