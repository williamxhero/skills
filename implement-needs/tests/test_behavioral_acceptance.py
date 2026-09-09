"""End-to-end controller protocol acceptance using the installed validators."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
PLANNING = SKILL_ROOT / "scripts" / "validate_planning.py"
LIFECYCLE = SKILL_ROOT / "scripts" / "validate_controller_lifecycle.py"
TERMINAL = SKILL_ROOT / "scripts" / "validate_controller_terminal.py"
RUN_ID = "acceptance-run"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProtocolFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.record_path = root / "planning-record.json"
        self.planning_state_path = root / "planning-controller-state.json"
        self.readback_path = root / "route-readback.json"
        self.lifecycle_log_path = root / "lifecycle-log.jsonl"
        self.delivery_map_path = root / "delivery-map.md"
        self.task_tree_path = root / "task-tree.json"
        self.terminal_state_path = root / "controller-state.json"

    @staticmethod
    def pair(model: str, thinking: str) -> dict[str, str]:
        return {"model": model, "thinking": thinking}

    def route(
        self, recommended: dict[str, str], fallback: dict[str, str]
    ) -> dict[str, Any]:
        return {
            "recommended": recommended,
            "fallbacks": [fallback],
            "rationale": "Risk and coupling justify this route.",
        }

    @staticmethod
    def checkpoint_plan(spec_ids: list[str]) -> list[dict[str, Any]]:
        checkpoints = []
        for start in range(0, len(spec_ids), 10):
            members = spec_ids[start : start + 10]
            end = start + len(members)
            checkpoints.append(
                {
                    "id": f"checkpoint-{end}",
                    "start_spec_index": start + 1,
                    "end_spec_index": end,
                    "specs": members,
                    "final_tail": len(members) < 10,
                    "affected_owners": ["owner-a"],
                    "affected_repositories": ["repo-a"],
                }
            )
        return checkpoints

    def planning_record(self) -> dict[str, Any]:
        approval = {
            "confirmation_mode": "auto_approve",
            "approval_source": "implement-needs",
            "approval_text": "\u540c\u610f",
            "spec_state": "auto_approved",
            "approval_provenance": "controller_decision",
        }
        return {
            "schema_version": 1,
            "run_id": RUN_ID,
            "scope": "bounded",
            "capability_evidence": ["tool://create-thread-schema"],
            "supported_routes": [
                {
                    "model": "reliable-1",
                    "model_class": "reliable",
                    "thinking": ["xhigh", "max"],
                },
                {
                    "model": "strongest-1",
                    "model_class": "strongest",
                    "thinking": ["max", "ultra"],
                },
            ],
            "planning_task": {
                "id": "plan-1",
                "generation": 1,
                "route": self.route(
                    self.pair("reliable-1", "xhigh"),
                    self.pair("strongest-1", "max"),
                ),
            },
            "ownership": {
                "grill": "plan-1",
                "specs": "plan-1",
                "tickets": "plan-1",
                "routing": "plan-1",
            },
            "requirements": ["R1"],
            "grill_rounds": [
                {
                    "round": 1,
                    "questions": [
                        {
                            "number": 1,
                            "question": "\u662f\u5426\u4fdd\u6301\u73b0\u6709\u884c\u4e3a\uff1f",
                            "recommendation": "\u5efa\u8bae\u4fdd\u6301\u517c\u5bb9\u3002",
                            "rationale": "\u8fd9\u80fd\u964d\u4f4e\u56de\u5f52\u98ce\u9669\u3002",
                        }
                    ],
                    "commentary_evidence": ["chat://round-1"],
                    "acceptance_source": "implement-needs-standing-authorization",
                    "acceptance_evidence": ["controller://accepted-round-1"],
                    "planner_resume_evidence": ["thread://plan-1/round-1"],
                }
            ],
            "frontier_empty": True,
            "specs": [
                {
                    "id": "SPEC-1",
                    "artifact": "tracker://spec-1",
                    "requirements": ["R1"],
                    "blocked_by": [],
                    "auto_approval": approval,
                    "tickets": [
                        {
                            "id": "T1",
                            "artifact": "tracker://ticket-1",
                            "blocked_by": [],
                            "vertical_slice": "Delivers the behavior end to end.",
                        }
                    ],
                    "ticket_self_check": {
                        "granularity": "pass",
                        "blocking_edges": "pass",
                        "acyclic": "pass",
                        "evidence": ["tracker://ticket-self-check"],
                    },
                    "difficulty": "hard",
                    "route": self.route(
                        self.pair("reliable-1", "xhigh"),
                        self.pair("strongest-1", "max"),
                    ),
                    "checkpoint": "checkpoint-1",
                }
            ],
            "release_train": {
                "owners": ["owner-a"],
                "repositories": ["repo-a"],
                "acceptance_scopes": ["scope-a"],
                "public_contract_specs": ["SPEC-1"],
                "environment_specs": [],
                "baselines": ["main@base-0"],
                "checkpoint_size": 10,
                "checkpoints": self.checkpoint_plan(["SPEC-1"]),
            },
            "code_read_only": {
                "product_test_tree_before_sha256": "a" * 64,
                "product_test_tree_after_sha256": "a" * 64,
                "changed_product_or_test_paths": [],
                "evidence": ["git://planning-read-only-check"],
            },
            "handoff_evidence": ["thread://plan-1/handoff"],
        }

    def readback(self, target: str, task_id: str) -> dict[str, Any]:
        pair = self.pair("reliable-1", "xhigh")
        return {
            "schema_version": 1,
            "run_id": RUN_ID,
            "task_id": task_id,
            "target": target,
            "requested": pair,
            "applied": copy.deepcopy(pair),
            "selection": "recommended",
            "substitution_reason": None,
            "readback_evidence": [f"thread://{task_id}/settings"],
        }

    def write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def write_route_artifacts(self, target: str, task_id: str) -> None:
        self.write_json(self.record_path, self.planning_record())
        self.write_json(self.readback_path, self.readback(target, task_id))

    def write_planning_handoff_state(self) -> None:
        self.write_json(
            self.planning_state_path,
            {
                "run_id": RUN_ID,
                "child_tasks": [
                    {
                        "id": "plan-1",
                        "kind": "planning",
                        "spec_id": None,
                        "lifecycle": "archived",
                    }
                ],
            },
        )

    def event(
        self,
        events: list[dict[str, Any]],
        event_type: str,
        data: dict[str, Any],
        actor: str = "controller",
    ) -> None:
        sequence = len(events) + 1
        events.append(
            {
                "schema_version": 1,
                "run_id": RUN_ID,
                "sequence": sequence,
                "type": event_type,
                "actor": actor,
                "data": data,
                "evidence": [f"evidence://{sequence}/{event_type}"],
            }
        )

    def success_events(self) -> list[dict[str, Any]]:
        wait_task = {
            "kind": "wait",
            "target": "task-1",
            "instruction": "Wait for the current SPEC task.",
        }
        wait_repair = {
            "kind": "wait",
            "target": "repair-1",
            "instruction": "Wait for the focused repair task.",
        }
        events: list[dict[str, Any]] = []
        self.event(
            events,
            "planning_archived",
            {
                "task_id": "plan-1",
                "specs": [{"id": "SPEC-1"}],
                "checkpoint_size": 10,
                "checkpoints": self.checkpoint_plan(["SPEC-1"]),
                "default_branch": "main",
                "default_revision": "base-0",
            },
        )
        self.event(
            events,
            "spec_dispatched",
            {"task_id": "task-1", "spec_id": "SPEC-1", "base_revision": "base-0"},
        )
        self.event(
            events,
            "commentary",
            {
                "category": "heartbeat",
                "text": "\u5b9e\u73b0\u4efb\u52a1\u4ecd\u5728\u8fd0\u884c\uff0c\u7ee7\u7eed\u7b49\u5f85\u3002",
                "next_action": wait_task,
            },
        )
        self.event(events, "waited", {"task_id": "task-1"})
        self.event(
            events,
            "controller_resumed",
            {
                "persisted_next_action": wait_task,
                "observed_task_ids": ["plan-1", "task-1"],
            },
        )
        self.event(events, "child_reconnected", {"task_id": "task-1"})
        self.event(events, "stored_action_resumed", {"action": wait_task})
        self.event(events, "waited", {"task_id": "task-1"})
        self.event(
            events,
            "blocker_opened",
            {
                "repair_task_id": "repair-1",
                "parent_task_id": "task-1",
                "fingerprint": "ci:network",
                "blocked_action": wait_task,
            },
        )
        self.event(
            events,
            "commentary",
            {
                "category": "heartbeat",
                "text": "\u4fee\u590d\u4efb\u52a1\u4ecd\u5728\u8fd0\u884c\uff0c\u7ee7\u7eed\u7b49\u5f85\u3002",
                "next_action": wait_repair,
            },
        )
        self.event(events, "waited", {"task_id": "repair-1"})
        self.event(
            events,
            "child_handoff",
            {"task_id": "repair-1", "boundary": "repair_evidence", "revision": None},
            actor="repair_child",
        )
        self.event(
            events,
            "handoff_verified",
            {"task_id": "repair-1", "result": "pass", "revision": None},
        )
        self.event(events, "child_archived", {"task_id": "repair-1"})
        self.event(
            events,
            "blocked_action_resumed",
            {
                "repair_task_id": "repair-1",
                "parent_task_id": "task-1",
                "action": wait_task,
            },
        )
        self.event(events, "waited", {"task_id": "task-1"})
        self.event(
            events,
            "child_handoff",
            {"task_id": "task-1", "boundary": "merged_evidence", "revision": "merge-1"},
            actor="spec_child",
        )
        self.event(
            events,
            "handoff_verified",
            {"task_id": "task-1", "result": "pass", "revision": "merge-1"},
        )
        self.event(events, "child_archived", {"task_id": "task-1"})
        self.event(
            events,
            "default_branch_verified",
            {"spec_id": "SPEC-1", "revision": "merge-1"},
        )
        self.event(
            events,
            "checkpoint_passed",
            {
                "checkpoint_id": "checkpoint-1",
                "revision": "merge-1",
                "candidate_revisions": ["merge-1"],
                "affected_owners": ["owner-a"],
                "affected_repositories": ["repo-a"],
            },
        )
        self.event(events, "release_candidate_frozen", {"revision": "merge-1"})
        self.event(
            events,
            "artifact_built",
            {"revision": "merge-1", "artifact_id": "artifact-1"},
        )
        self.event(
            events,
            "final_tests_passed",
            {
                "revision": "merge-1",
                "artifact_id": "artifact-1",
                "candidate_revisions": ["merge-1"],
                "l4_reused_checkpoint": "checkpoint-1",
            },
        )
        self.event(
            events,
            "package_completed",
            {
                "revision": "merge-1",
                "artifact_id": "artifact-1",
                "package_id": "package-1",
            },
        )
        self.event(
            events,
            "deployment_completed",
            {
                "revision": "merge-1",
                "artifact_id": "artifact-1",
                "package_id": "package-1",
                "target": "production",
            },
        )
        self.event(
            events,
            "smoke_passed",
            {
                "revision": "merge-1",
                "artifact_id": "artifact-1",
                "target": "production",
            },
        )
        self.event(
            events,
            "terminal_ready",
            {
                "controller_state": "terminal_success",
                "candidate_revision": "merge-1",
                "next_action": None,
            },
        )
        return events

    def write_lifecycle_log(self, events: list[dict[str, Any]]) -> None:
        self.lifecycle_log_path.write_text(
            "".join(
                json.dumps(
                    event, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                + "\n"
                for event in events
            ),
            encoding="utf-8",
            newline="\n",
        )

    def write_terminal_state(self, lifecycle_receipt: dict[str, Any]) -> None:
        child_tasks = lifecycle_receipt["child_tasks"]
        self.delivery_map_path.write_text(
            "# Delivery map\n\nPlanning, implementation, repair, release, and smoke evidence verified.\n",
            encoding="utf-8",
            newline="\n",
        )
        self.write_json(
            self.task_tree_path,
            {
                "run_id": RUN_ID,
                "tasks": sorted(child_tasks, key=lambda task: task["id"]),
            },
        )
        state = {
            "schema_version": 1,
            "run_id": RUN_ID,
            "state_revision": 42,
            "controller_state": "terminal_success",
            "active_phase": "complete",
            "active_task_stack": [],
            "child_tasks": child_tasks,
            "pending_specs": lifecycle_receipt["pending_specs"],
            "unverified_handoffs": [],
            "unarchived_tasks": [],
            "test_state": {
                "status": "passed",
                "candidate_revision": lifecycle_receipt["candidate_revision"],
                "evidence": ["receipt://lifecycle/final-tests"],
                "l4_checkpoints": lifecycle_receipt["l4_checkpoints"],
            },
            "release_state": {
                "status": lifecycle_receipt["release_status"],
                "candidate_revision": lifecycle_receipt["candidate_revision"],
                "evidence": ["receipt://lifecycle/release"],
            },
            "next_action": None,
            "resume_action": None,
            "freshness": {
                "delivery_map_sha256": _sha256(self.delivery_map_path),
                "task_tree_sha256": _sha256(self.task_tree_path),
            },
            "terminal": {
                "reason": "All acceptance protocol gates passed.",
                "evidence": [
                    "receipt://planning",
                    "receipt://lifecycle",
                    "receipt://terminal",
                ],
                "stopping_rule_met": False,
                "user_stop_recorded": False,
            },
        }
        self.write_json(self.terminal_state_path, state)


class BehavioralAcceptance(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = ProtocolFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_validator(self, args: list[str]) -> tuple[dict[str, Any], int]:
        completed = subprocess.run(
            [sys.executable, *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        self.assertTrue(
            completed.stdout.strip(),
            f"validator produced no JSON\nstderr:\n{completed.stderr}",
        )
        return json.loads(completed.stdout), completed.returncode

    @staticmethod
    def codes(payload: dict[str, Any]) -> set[str]:
        return {reason["code"] for reason in payload.get("reasons", [])}

    def test_full_protocol_trace_reaches_one_terminal_success(self) -> None:
        self.fixture.write_route_artifacts("planning", "plan-1")
        route_payload, route_code = self.run_validator(
            [
                str(PLANNING),
                "route",
                "--record",
                str(self.fixture.record_path),
                "--readback",
                str(self.fixture.readback_path),
                "--expected-run-id",
                RUN_ID,
                "--target",
                "planning",
                "--expected-task-id",
                "plan-1",
                "--receipt",
                str(self.root / "planning-route-receipt.json"),
            ]
        )
        self.assertEqual(0, route_code)
        self.assertEqual("allow", route_payload["decision"])

        self.fixture.write_planning_handoff_state()
        handoff_payload, handoff_code = self.run_validator(
            [
                str(PLANNING),
                "handoff",
                "--record",
                str(self.fixture.record_path),
                "--controller-state",
                str(self.fixture.planning_state_path),
                "--planning-readback",
                str(self.fixture.readback_path),
                "--expected-run-id",
                RUN_ID,
                "--receipt",
                str(self.root / "planning-handoff-receipt.json"),
            ]
        )
        self.assertEqual(0, handoff_code)
        self.assertEqual(["SPEC-1"], handoff_payload["spec_ids"])

        self.fixture.write_route_artifacts("SPEC-1", "task-1")
        spec_route_payload, spec_route_code = self.run_validator(
            [
                str(PLANNING),
                "route",
                "--record",
                str(self.fixture.record_path),
                "--readback",
                str(self.fixture.readback_path),
                "--expected-run-id",
                RUN_ID,
                "--target",
                "SPEC-1",
                "--expected-task-id",
                "task-1",
            ]
        )
        self.assertEqual(0, spec_route_code)
        self.assertEqual("task-1", spec_route_payload["task_id"])

        events = self.fixture.success_events()
        self.assertEqual(1, sum(event["type"] == "terminal_ready" for event in events))
        self.fixture.write_lifecycle_log(events)
        lifecycle_payload, lifecycle_code = self.run_validator(
            [
                str(LIFECYCLE),
                "--log",
                str(self.fixture.lifecycle_log_path),
                "--expected-run-id",
                RUN_ID,
                "--expected-state",
                "terminal_success",
                "--receipt",
                str(self.root / "lifecycle-receipt.json"),
            ]
        )
        self.assertEqual(0, lifecycle_code)
        self.assertEqual(
            "terminal_success", lifecycle_payload["derived_controller_state"]
        )
        self.assertIsNone(lifecycle_payload["next_action"])
        self.assertEqual([], lifecycle_payload["pending_specs"])
        self.assertTrue(
            all(
                task["lifecycle"] == "archived"
                for task in lifecycle_payload["child_tasks"]
            )
        )

        self.fixture.write_terminal_state(lifecycle_payload)
        terminal_payload, terminal_code = self.run_validator(
            [
                str(TERMINAL),
                "--state",
                str(self.fixture.terminal_state_path),
                "--delivery-map",
                str(self.fixture.delivery_map_path),
                "--task-tree",
                str(self.fixture.task_tree_path),
                "--expected-run-id",
                RUN_ID,
                "--proposed-state",
                "terminal_success",
                "--goal-status",
                "complete",
                "--receipt",
                str(self.root / "terminal-receipt.json"),
            ]
        )
        self.assertEqual(0, terminal_code)
        self.assertEqual("allow", terminal_payload["decision"])
        self.assertEqual("terminal_success", terminal_payload["terminal_state"])

    def test_audit_failure_trace_is_rejected_by_real_lifecycle_gate(self) -> None:
        events: list[dict[str, Any]] = []
        self.fixture.event(
            events,
            "planning_archived",
            {
                "task_id": "plan-1",
                "specs": [{"id": "SPEC-1"}],
                "checkpoint_size": 10,
                "checkpoints": self.fixture.checkpoint_plan(["SPEC-1"]),
                "default_branch": "main",
                "default_revision": "base-0",
            },
        )
        self.fixture.event(
            events,
            "spec_dispatched",
            {"task_id": "task-1", "spec_id": "SPEC-1", "base_revision": "base-0"},
        )
        self.fixture.event(
            events,
            "release_candidate_frozen",
            {"revision": "base-0"},
            actor="spec_child",
        )
        self.fixture.event(events, "release_candidate_frozen", {"revision": "base-0"})
        self.fixture.write_lifecycle_log(events)
        payload, code = self.run_validator(
            [
                str(LIFECYCLE),
                "--log",
                str(self.fixture.lifecycle_log_path),
                "--expected-run-id",
                RUN_ID,
                "--expected-state",
                "active",
            ]
        )
        self.assertEqual(1, code)
        self.assertIn("actor_mismatch", self.codes(payload))
        self.assertIn("release_before_specs_complete", self.codes(payload))

    def test_second_terminal_event_is_rejected(self) -> None:
        events = self.fixture.success_events()
        duplicate = copy.deepcopy(events[-1])
        duplicate["sequence"] = len(events) + 1
        duplicate["evidence"] = ["evidence://duplicate-terminal"]
        events.append(duplicate)
        self.fixture.write_lifecycle_log(events)
        payload, code = self.run_validator(
            [
                str(LIFECYCLE),
                "--log",
                str(self.fixture.lifecycle_log_path),
                "--expected-run-id",
                RUN_ID,
                "--expected-state",
                "terminal_success",
            ]
        )
        self.assertEqual(1, code)
        self.assertIn("event_after_terminal", self.codes(payload))


if __name__ == "__main__":
    unittest.main()
