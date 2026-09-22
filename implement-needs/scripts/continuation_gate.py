"""Verify a controller continuation from identity-bound execution and state readbacks.

This gate observes receipts; it never advances the controller or sends a turn.
Tool availability probes and terminal message text are not business progress.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

IDENTITY_FIELDS = ("formal_thread_id", "host_id", "run_id", "project_id", "cwd")


def verify_continuation(receipt: dict) -> dict:
    before, after, turn = (receipt.get(key) for key in ("before", "after", "turn"))
    if not all(isinstance(value, dict) for value in (before, after, turn)):
        return {"decision": "blocked", "reasons": ["continuation_readbacks_missing"]}
    reasons = []
    for field in IDENTITY_FIELDS:
        value = before.get(field)
        if not isinstance(value, str) or not value.strip() or after.get(field) != value or turn.get(field) != value:
            reasons.append(f"continuation_identity_mismatch:{field}")
    if not before.get("turn_id") or not turn.get("turn_id") or turn["turn_id"] == before["turn_id"] or after.get("turn_id") != turn["turn_id"]:
        reasons.append("continuation_turn_not_fresh")
    if turn.get("status") != "completed":
        reasons.append("continuation_not_completed")
    count = turn.get("successful_tool_calls")
    if type(count) is not int or count < 1 or not turn.get("execution_evidence"):
        reasons.append("continuation_execution_missing")
    for name, state in (("before", before), ("after", after)):
        if not isinstance(state.get("evidence"), list) or not state["evidence"]:
            reasons.append(f"continuation_{name}_evidence_missing")
    old_version, new_version = before.get("business_version"), after.get("business_version")
    version_advanced = type(old_version) is int and type(new_version) is int and new_version > old_version
    old_frontier, new_frontier = before.get("frontier"), after.get("frontier")
    frontier_advanced = isinstance(old_frontier, dict) and bool(old_frontier) and isinstance(new_frontier, dict) and bool(new_frontier) and old_frontier != new_frontier
    if not version_advanced or not frontier_advanced:
        reasons.append("continuation_business_progress_missing")
    action = after.get("next_action")
    kind = action.get("kind") if isinstance(action, dict) else None
    if not isinstance(kind, str) or not kind.strip() or kind.startswith(("repair", "blocked")):
        reasons.append("continuation_next_action_blocked")
    if after.get("recovery_action") or after.get("lost_wakeup") is not False:
        reasons.append("continuation_recovery_unreconciled")
    if after.get("handoff_reconciled") is not True or after.get("no_orphans") is not True:
        reasons.append("continuation_cleanup_unreconciled")
    return {"decision": "verified" if not reasons else "blocked", "reasons": sorted(set(reasons)),
            "formal_thread_id": after.get("formal_thread_id"), "run_id": after.get("run_id"),
            "turn_id": turn.get("turn_id")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify_continuation(json.loads(args.receipt.read_text(encoding="utf-8")))
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["decision"] == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
