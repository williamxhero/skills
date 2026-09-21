from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from takeover import TakeoverInventoryError, plan_takeover


def inventory(**states):
    payload = {
        "schema_version": 1,
        "requirement": {"status": "verified", "evidence": ["github://issue/1"]},
        "controller": {"status": "missing"},
        "planning": {"status": "absent"},
        "tickets": {"status": "absent"},
        "implementation": {"status": "absent"},
        "merge": {"status": "absent"},
        "cleanup": {"status": "not_required"},
        "release": {"status": "absent"},
        "synchronization": {"status": "absent"},
    }
    for name, status in states.items():
        payload[name] = {"status": status}
        if status not in {"absent", "missing", "not_required"}:
            payload[name]["evidence"] = [f"readback://{name}/{status}"]
    return payload


class TakeoverPlanTests(unittest.TestCase):
    def test_every_lifecycle_frontier_has_one_safe_entry_action(self) -> None:
        cases = (
            ({}, ("requirement", "run_grill")),
            ({"planning": "partial"}, ("planning", "reconcile_planning")),
            ({"planning": "complete", "tickets": "partial"}, ("ticketing", "reconcile_tickets")),
            ({"planning": "complete", "tickets": "complete"}, ("implementation", "dispatch_spec")),
            ({"planning": "complete", "tickets": "complete", "implementation": "active"}, ("implementation", "reconcile_implementation")),
            ({"planning": "complete", "tickets": "complete", "implementation": "complete", "merge": "open"}, ("verification", "verify_and_merge")),
            ({"planning": "complete", "tickets": "complete", "implementation": "complete", "merge": "merged", "cleanup": "pending"}, ("merge_cleanup", "reconcile_cleanup")),
            ({"planning": "complete", "tickets": "complete", "implementation": "complete", "merge": "merged", "cleanup": "complete", "release": "pending"}, ("release", "advance_release")),
            ({"planning": "complete", "tickets": "complete", "implementation": "complete", "merge": "merged", "cleanup": "complete", "release": "complete", "synchronization": "pending"}, ("synchronization", "synchronize_repository")),
            ({"planning": "complete", "tickets": "complete", "implementation": "complete", "merge": "merged", "cleanup": "complete", "release": "complete", "synchronization": "complete"}, ("terminal", "terminal_readback")),
        )
        for states, expected in cases:
            with self.subTest(states=states):
                result = plan_takeover(inventory(**states))
                self.assertEqual(expected, (result["entry_stage"], result["next_action"]))
                self.assertEqual([], result["resources_to_create"])

    def test_existing_managed_run_is_resumed_before_external_adoption(self) -> None:
        payload = inventory(planning="complete")
        payload["controller"] = {
            "status": "active",
            "run_id": "run-1",
            "next_action": {"kind": "wait_spec", "target": "SPEC-2"},
            "evidence": ["sqlite://run-1/snapshot"],
        }
        result = plan_takeover(payload)
        self.assertEqual("managed_run", result["entry_stage"])
        self.assertEqual("resume_managed_run", result["next_action"])
        self.assertEqual(payload["controller"]["next_action"], result["controller_next_action"])

    def test_downstream_fact_without_upstream_fact_is_rejected(self) -> None:
        with self.assertRaises(TakeoverInventoryError):
            plan_takeover(inventory(implementation="active"))

    def test_non_absent_fact_without_readback_evidence_is_rejected(self) -> None:
        payload = inventory(planning="partial")
        del payload["planning"]["evidence"]
        with self.assertRaises(TakeoverInventoryError):
            plan_takeover(payload)

    def test_input_is_not_mutated(self) -> None:
        payload = inventory(planning="complete", tickets="complete")
        before = copy.deepcopy(payload)
        plan_takeover(payload)
        self.assertEqual(before, payload)


if __name__ == "__main__":
    unittest.main()
