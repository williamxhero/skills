"""Plan a safe Implement Needs entry point from verified external state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


class TakeoverInventoryError(ValueError):
    """The supplied lifecycle inventory cannot support a safe takeover."""


_ALLOWED = {
    "controller": {"missing", "active", "terminal"},
    "planning": {"absent", "partial", "complete"},
    "tickets": {"absent", "partial", "complete"},
    "implementation": {"absent", "active", "complete"},
    "merge": {"absent", "open", "merged"},
    "cleanup": {"not_required", "pending", "complete"},
    "release": {"absent", "pending", "complete"},
    "synchronization": {"absent", "pending", "complete"},
}
_INACTIVE = {"missing", "absent", "not_required"}


def _section(inventory: dict[str, Any], name: str) -> dict[str, Any]:
    value = inventory.get(name)
    if not isinstance(value, dict):
        raise TakeoverInventoryError(f"{name} inventory is required")
    status = value.get("status")
    if status not in _ALLOWED[name]:
        raise TakeoverInventoryError(f"unsupported {name} status: {status}")
    evidence = value.get("evidence")
    if status not in _INACTIVE and (
        not isinstance(evidence, list)
        or not evidence
        or any(not isinstance(item, str) or not item.strip() for item in evidence)
    ):
        raise TakeoverInventoryError(f"{name} status requires readback evidence")
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TakeoverInventoryError(message)


def plan_takeover(inventory: dict[str, Any]) -> dict[str, Any]:
    """Return the unique non-mutating entry action for a lifecycle inventory."""
    if not isinstance(inventory, dict) or inventory.get("schema_version") != 1:
        raise TakeoverInventoryError("takeover inventory schema_version must be 1")
    requirement = inventory.get("requirement")
    if not isinstance(requirement, dict) or requirement.get("status") != "verified":
        raise TakeoverInventoryError("verified requirement identity is required")
    requirement_evidence = requirement.get("evidence")
    if not isinstance(requirement_evidence, list) or not requirement_evidence:
        raise TakeoverInventoryError("requirement readback evidence is required")

    sections = {name: _section(inventory, name) for name in _ALLOWED}
    controller = sections["controller"]
    if controller["status"] == "active":
        _require(isinstance(controller.get("run_id"), str) and bool(controller["run_id"].strip()),
                 "active controller requires run_id")
        _require(isinstance(controller.get("next_action"), dict) and bool(controller["next_action"]),
                 "active controller requires next_action readback")
        return {
            "decision": "allow",
            "mode": "resume",
            "entry_stage": "managed_run",
            "next_action": "resume_managed_run",
            "controller_next_action": controller["next_action"],
            "resources_to_create": [],
            "reconciliation_required": True,
        }
    if controller["status"] == "terminal":
        return {
            "decision": "allow",
            "mode": "verify",
            "entry_stage": "terminal",
            "next_action": "terminal_readback",
            "resources_to_create": [],
            "reconciliation_required": True,
        }

    planning = sections["planning"]["status"]
    tickets = sections["tickets"]["status"]
    implementation = sections["implementation"]["status"]
    merge = sections["merge"]["status"]
    cleanup = sections["cleanup"]["status"]
    release = sections["release"]["status"]
    synchronization = sections["synchronization"]["status"]

    _require(tickets == "absent" or planning == "complete",
             "ticket state requires complete planning readback")
    _require(implementation == "absent" or tickets == "complete",
             "implementation state requires complete ticket readback")
    _require(merge == "absent" or implementation == "complete",
             "merge state requires completed implementation readback")
    _require(cleanup == "not_required" or merge == "merged",
             "cleanup state requires merged pull-request readback")
    _require(release == "absent" or cleanup == "complete",
             "release state requires complete merge cleanup")
    _require(synchronization == "absent" or release == "complete",
             "synchronization state requires complete release readback")
    if merge == "merged":
        _require(cleanup != "not_required", "merged work requires cleanup readback")

    if planning == "absent":
        stage, action = "requirement", "run_grill"
    elif planning == "partial":
        stage, action = "planning", "reconcile_planning"
    elif tickets != "complete":
        stage, action = "ticketing", "reconcile_tickets"
    elif implementation == "absent":
        stage, action = "implementation", "dispatch_spec"
    elif implementation == "active":
        stage, action = "implementation", "reconcile_implementation"
    elif merge != "merged":
        stage, action = "verification", "verify_and_merge"
    elif cleanup != "complete":
        stage, action = "merge_cleanup", "reconcile_cleanup"
    elif release == "absent":
        stage, action = "final_verification", "run_final_verification"
    elif release == "pending":
        stage, action = "release", "advance_release"
    elif synchronization != "complete":
        stage, action = "synchronization", "synchronize_repository"
    else:
        stage, action = "terminal", "terminal_readback"

    return {
        "decision": "allow",
        "mode": "adopt",
        "entry_stage": stage,
        "next_action": action,
        "resources_to_create": [],
        "reconciliation_required": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    args = parser.parse_args()
    try:
        inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
        result = plan_takeover(inventory)
    except (OSError, UnicodeError, json.JSONDecodeError, TakeoverInventoryError) as exc:
        result = {"decision": "blocked", "error": "takeover_inventory_invalid", "detail": str(exc)}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
