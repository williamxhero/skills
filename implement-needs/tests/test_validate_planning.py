from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SKILL_ROOT / "scripts" / "validate_planning.py"
TERMINAL_SCRIPT_PATH = SKILL_ROOT / "scripts" / "validate_controller_terminal.py"
SCHEMA_PATH = SKILL_ROOT / "references" / "controller-state.schema.json"
SPEC = importlib.util.spec_from_file_location("validate_planning", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)
TERMINAL_SPEC = importlib.util.spec_from_file_location(
    "validate_controller_terminal_for_planning_tests", TERMINAL_SCRIPT_PATH
)
assert TERMINAL_SPEC is not None and TERMINAL_SPEC.loader is not None
terminal_validator = importlib.util.module_from_spec(TERMINAL_SPEC)
TERMINAL_SPEC.loader.exec_module(terminal_validator)


class PlanningValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.record_path = self.root / "planning-record.json"
        self.state_path = self.root / "controller-state.json"
        self.readback_path = self.root / "route-readback.json"
        self.receipt_path = self.root / "receipt.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def pair(model: str, thinking: str) -> dict:
        return {"model": model, "thinking": thinking}

    def route(self, recommended: dict, fallback: dict) -> dict:
        return {
            "recommended": recommended,
            "fallbacks": [fallback],
            "rationale": "Risk and coupling justify this route.",
        }

    @staticmethod
    def checkpoint_plan(spec_ids: list[str]) -> list[dict]:
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

    def record(self) -> dict:
        approval = {
            "confirmation_mode": "auto_approve",
            "approval_source": "implement-needs",
            "approval_text": "同意",
            "spec_state": "auto_approved",
            "approval_provenance": "controller_decision",
        }
        ticket_check = {
            "granularity": "pass",
            "blocking_edges": "pass",
            "acyclic": "pass",
            "evidence": ["tracker://ticket-self-check"],
        }
        return {
            "schema_version": 1,
            "run_id": "run-001",
            "scope": "bounded",
            "capability_evidence": ["tool://create-thread-schema"],
            "supported_routes": [
                {
                    "model": "fast-1",
                    "model_class": "fast",
                    "thinking": ["medium", "high"],
                },
                {
                    "model": "balanced-1",
                    "model_class": "balanced",
                    "thinking": ["high", "xhigh"],
                },
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
                    self.pair("reliable-1", "xhigh"), self.pair("strongest-1", "max")
                ),
            },
            "ownership": {
                "grill": "plan-1",
                "specs": "plan-1",
                "tickets": "plan-1",
                "routing": "plan-1",
            },
            "requirements": ["R1", "R2"],
            "grill_rounds": [
                {
                    "round": 1,
                    "questions": [
                        {
                            "number": 1,
                            "question": "是否保持现有兼容行为？",
                            "recommendation": "建议保持向后兼容。",
                            "rationale": "这样可以降低迁移风险。",
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
                    "auto_approval": copy.deepcopy(approval),
                    "tickets": [
                        {
                            "id": "T1",
                            "artifact": "tracker://ticket-1",
                            "blocked_by": [],
                            "vertical_slice": "Delivers the first behavior end to end.",
                        }
                    ],
                    "ticket_self_check": copy.deepcopy(ticket_check),
                    "difficulty": "easy",
                    "route": self.route(
                        self.pair("fast-1", "medium"), self.pair("balanced-1", "high")
                    ),
                    "checkpoint": "checkpoint-2",
                },
                {
                    "id": "SPEC-2",
                    "artifact": "tracker://spec-2",
                    "requirements": ["R2"],
                    "blocked_by": ["SPEC-1"],
                    "auto_approval": copy.deepcopy(approval),
                    "tickets": [
                        {
                            "id": "T2",
                            "artifact": "tracker://ticket-2",
                            "blocked_by": [],
                            "vertical_slice": "Delivers the second behavior end to end.",
                        }
                    ],
                    "ticket_self_check": copy.deepcopy(ticket_check),
                    "difficulty": "hard",
                    "route": self.route(
                        self.pair("reliable-1", "xhigh"),
                        self.pair("strongest-1", "max"),
                    ),
                    "checkpoint": "checkpoint-2",
                },
            ],
            "release_train": {
                "owners": ["owner-a"],
                "repositories": ["repo-a"],
                "acceptance_scopes": ["scope-a"],
                "public_contract_specs": ["SPEC-2"],
                "environment_specs": [],
                "baselines": ["main@abc"],
                "checkpoint_size": 10,
                "checkpoints": self.checkpoint_plan(["SPEC-1", "SPEC-2"]),
            },
            "code_read_only": {
                "product_test_tree_before_sha256": "a" * 64,
                "product_test_tree_after_sha256": "a" * 64,
                "changed_product_or_test_paths": [],
                "evidence": ["git://planning-read-only-check"],
            },
            "handoff_evidence": ["thread://plan-1/handoff"],
        }

    @staticmethod
    def state() -> dict:
        return {
            "run_id": "run-001",
            "child_tasks": [
                {
                    "id": "plan-1",
                    "kind": "planning",
                    "spec_id": None,
                    "lifecycle": "archived",
                }
            ],
        }

    def readback(
        self,
        *,
        target: str = "planning",
        task_id: str = "plan-1",
        requested: dict | None = None,
        applied: dict | None = None,
        selection: str = "recommended",
        reason: str | None = None,
    ) -> dict:
        requested = requested or self.pair("reliable-1", "xhigh")
        return {
            "schema_version": 1,
            "run_id": "run-001",
            "task_id": task_id,
            "target": target,
            "requested": requested,
            "applied": applied or copy.deepcopy(requested),
            "selection": selection,
            "substitution_reason": reason,
            "readback_evidence": [f"thread://{task_id}/settings"],
        }

    def write(
        self, record: dict, state: dict | None = None, readback: dict | None = None
    ) -> None:
        self.record_path.write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8"
        )
        self.state_path.write_text(
            json.dumps(state or self.state(), ensure_ascii=False), encoding="utf-8"
        )
        self.readback_path.write_text(
            json.dumps(readback or self.readback(), ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def codes(payload: dict) -> set[str]:
        return {reason["code"] for reason in payload.get("reasons", [])}

    def assert_persistable_next_action(self, action: dict) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        allowed = set(schema["$defs"]["action"]["properties"]["kind"]["enum"])
        self.assertIn(action["kind"], allowed)

        delivery_map = self.root / "delivery-map.md"
        task_tree = self.root / "task-tree.json"
        state_path = self.root / "active-controller-state.json"
        delivery_map.write_text(
            "# Delivery map\n\nPlanning repair pending.\n",
            encoding="utf-8",
            newline="\n",
        )
        task_tree.write_text(
            json.dumps(
                {
                    "run_id": "run-001",
                    "tasks": [],
                    "implementation_ownership": {
                        "specs": [],
                        "ticket_implementation_artifacts": {
                            "tasks": [],
                            "threads": [],
                            "worktrees": [],
                            "branches": [],
                            "pull_requests": [],
                        },
                        "role_limited_tasks": [],
                    },
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        state = {
            "schema_version": 1,
            "run_id": "run-001",
            "state_revision": 1,
            "controller_state": "active",
            "active_phase": "planning",
            "active_task_stack": [],
            "child_tasks": [],
            "implementation_ownership": {
                "specs": [],
                "ticket_implementation_artifacts": {
                    "tasks": [],
                    "threads": [],
                    "worktrees": [],
                    "branches": [],
                    "pull_requests": [],
                },
                "role_limited_tasks": [],
            },
            "pending_specs": [],
            "unverified_handoffs": [],
            "unarchived_tasks": [],
            "test_state": {
                "status": "pending",
                "candidate_revision": None,
                "evidence": [],
                "l4_checkpoints": {
                    "checkpoint_size": 10,
                    "ordered_specs": [],
                    "completed_spec_count": 0,
                    "checkpoints": [],
                    "release_l4": None,
                },
            },
            "release_state": {
                "status": "pending",
                "candidate_revision": None,
                "evidence": [],
            },
            "next_action": action,
            "resume_action": None,
            "freshness": {
                "delivery_map_sha256": hashlib.sha256(
                    delivery_map.read_bytes()
                ).hexdigest(),
                "task_tree_sha256": hashlib.sha256(task_tree.read_bytes()).hexdigest(),
            },
            "terminal": None,
        }
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        payload, code = terminal_validator.evaluate(
            state_path,
            delivery_map,
            task_tree,
            "run-001",
            "terminal_success",
        )
        self.assertEqual(1, code)
        self.assertEqual(action, payload["next_action"])
        self.assertEqual({"proposed_state_mismatch"}, self.codes(payload))

    def handoff(
        self, record: dict, state: dict | None = None, readback: dict | None = None
    ):
        self.write(record, state, readback)
        return validator.evaluate_handoff(
            self.record_path, self.state_path, self.readback_path, "run-001"
        )

    def route_gate(self, record: dict, readback: dict, target: str):
        self.write(record, readback=readback)
        return validator.evaluate_route(
            self.record_path, self.readback_path, "run-001", target, readback["task_id"]
        )

    def test_complete_handoff_is_allowed_and_deterministic(self) -> None:
        first = self.handoff(self.record())
        second = self.handoff(self.record())
        self.assertEqual(first, second)
        self.assertEqual(0, first[1])
        self.assertEqual("allow", first[0]["decision"])
        self.assertEqual(["SPEC-1", "SPEC-2"], first[0]["spec_ids"])
        self.assertEqual(1, first[0]["grill_question_count"])

    def test_fixed_checkpoint_policy_is_table_driven(self) -> None:
        cases = {
            1: [1],
            7: [7],
            10: [10],
            11: [10, 11],
            20: [10, 20],
            23: [10, 20, 23],
            30: [10, 20, 30],
            32: [10, 20, 30, 32],
        }
        for count, expected_positions in cases.items():
            with self.subTest(count=count):
                spec_ids = [f"SPEC-{index}" for index in range(1, count + 1)]
                checkpoints = self.checkpoint_plan(spec_ids)
                self.assertEqual(
                    expected_positions,
                    [checkpoint["end_spec_index"] for checkpoint in checkpoints],
                )
                self.assertEqual(
                    ["checkpoint-" + str(position) for position in expected_positions],
                    [checkpoint["id"] for checkpoint in checkpoints],
                )

    def test_release_train_checkpoint_plan_is_fixed_and_exact(self) -> None:
        record = self.record()
        record["release_train"]["checkpoint_size"] = 6
        payload, code = self.handoff(record)
        self.assertEqual(1, code)
        self.assertIn("checkpoint_size_mismatch", self.codes(payload))

        record = self.record()
        record["release_train"]["checkpoints"][0]["specs"] = ["SPEC-2", "SPEC-1"]
        payload, code = self.handoff(record)
        self.assertEqual(1, code)
        self.assertIn("checkpoint_policy_mismatch", self.codes(payload))

    def test_dispatch_gate_requires_exactly_one_archived_planner_before_any_spec(
        self,
    ) -> None:
        cases = []
        active = self.state()
        active["child_tasks"][0]["lifecycle"] = "verified"
        cases.append((active, "planning_not_archived"))
        duplicate = self.state()
        duplicate["child_tasks"].append(
            {
                "id": "plan-2",
                "kind": "planning",
                "spec_id": None,
                "lifecycle": "archived",
            }
        )
        cases.append((duplicate, "planning_task_count"))
        early_spec = self.state()
        early_spec["child_tasks"].append(
            {
                "id": "spec-1",
                "kind": "spec",
                "spec_id": "SPEC-1",
                "lifecycle": "archived",
            }
        )
        cases.append((early_spec, "implementation_preceded_initial_planning_gate"))
        for state, expected in cases:
            with self.subTest(expected=expected):
                payload, code = self.handoff(self.record(), state)
                self.assertEqual(1, code)
                self.assertIn(expected, self.codes(payload))

    def test_grill_must_be_chinese_visible_auto_accepted_and_returned(self) -> None:
        mutations = [
            (
                lambda record: record["grill_rounds"][0]["questions"][0].update(
                    question="English only"
                ),
                "relay_not_chinese",
            ),
            (
                lambda record: record["grill_rounds"][0].update(commentary_evidence=[]),
                "empty_list",
            ),
            (
                lambda record: record["grill_rounds"][0].update(
                    acceptance_source="user"
                ),
                "manual_confirmation_required",
            ),
            (
                lambda record: record["grill_rounds"][0].update(
                    planner_resume_evidence=[]
                ),
                "empty_list",
            ),
        ]
        for mutate, expected in mutations:
            with self.subTest(expected=expected):
                record = self.record()
                mutate(record)
                payload, code = self.handoff(record)
                self.assertEqual(1, code)
                self.assertIn(expected, self.codes(payload))

    def test_spec_approval_ticket_self_check_and_partition_are_enforced(self) -> None:
        mutations = [
            (
                lambda record: record["specs"][0]["auto_approval"].update(
                    approval_text="确认"
                ),
                "auto_approval_invalid",
            ),
            (
                lambda record: record["specs"][0]["ticket_self_check"].update(
                    granularity="fail"
                ),
                "ticket_self_check_failed",
            ),
            (
                lambda record: record["specs"][1].update(requirements=["R1"]),
                "requirement_partition_invalid",
            ),
            (
                lambda record: record["specs"][0]["tickets"][0].update(
                    blocked_by=["T-later"]
                ),
                "unknown_blocker",
            ),
        ]
        for mutate, expected in mutations:
            with self.subTest(expected=expected):
                record = self.record()
                mutate(record)
                payload, code = self.handoff(record)
                self.assertEqual(1, code)
                self.assertIn(expected, self.codes(payload))

    def test_planner_owns_every_planning_phase_and_changes_no_code(self) -> None:
        record = self.record()
        record["ownership"]["tickets"] = "controller"
        record["code_read_only"]["product_test_tree_after_sha256"] = "b" * 64
        record["code_read_only"]["changed_product_or_test_paths"] = ["src/app.py"]
        payload, code = self.handoff(record)
        self.assertEqual(1, code)
        self.assertIn("planning_ownership_drift", self.codes(payload))
        self.assertIn("planning_code_changed", self.codes(payload))

    def test_planning_route_floor_and_same_or_stronger_fallback_are_enforced(
        self,
    ) -> None:
        record = self.record()
        record["planning_task"]["route"] = self.route(
            self.pair("balanced-1", "high"), self.pair("fast-1", "medium")
        )
        payload, code = self.handoff(
            record, readback=self.readback(requested=self.pair("balanced-1", "high"))
        )
        self.assertEqual(1, code)
        self.assertIn("route_below_floor", self.codes(payload))
        self.assertIn("fallback_weaker", self.codes(payload))

    def test_exact_post_create_readback_is_allowed(self) -> None:
        payload, code = self.route_gate(self.record(), self.readback(), "planning")
        self.assertEqual(0, code)
        self.assertEqual("recommended", payload["selection"])
        self.assertEqual(self.pair("reliable-1", "xhigh"), payload["applied"])

    def test_recorded_fallback_requires_reason_and_is_allowed_without_drift(
        self,
    ) -> None:
        fallback = self.pair("strongest-1", "max")
        payload, code = self.route_gate(
            self.record(),
            self.readback(
                requested=fallback,
                selection="fallback",
                reason="Recommended pair became unavailable.",
            ),
            "planning",
        )
        self.assertEqual(0, code)
        self.assertEqual("fallback", payload["selection"])

    def test_silent_drift_and_unrecorded_fallback_fail_closed(self) -> None:
        drift = self.readback(applied=self.pair("balanced-1", "high"))
        payload, code = self.route_gate(self.record(), drift, "planning")
        self.assertEqual(1, code)
        self.assertIn("silent_route_drift", self.codes(payload))
        self.assertEqual("repair", payload["next_action"]["kind"])
        self.assert_persistable_next_action(payload["next_action"])

        unrecorded = self.readback(
            requested=self.pair("reliable-1", "max"),
            selection="fallback",
            reason="Unavailable",
        )
        payload, code = self.route_gate(self.record(), unrecorded, "planning")
        self.assertEqual(1, code)
        self.assertIn("fallback_not_preapproved", self.codes(payload))
        self.assertEqual("repair", payload["next_action"]["kind"])
        self.assert_persistable_next_action(payload["next_action"])

    def test_spec_route_readback_uses_locked_spec_route(self) -> None:
        requested = self.pair("fast-1", "medium")
        payload, code = self.route_gate(
            self.record(),
            self.readback(target="SPEC-1", task_id="spec-task-1", requested=requested),
            "SPEC-1",
        )
        self.assertEqual(0, code)
        self.assertEqual("spec-task-1", payload["task_id"])

    def test_spec_route_readback_is_bound_to_created_task(self) -> None:
        readback = self.readback(
            target="SPEC-1",
            task_id="stale-task",
            requested=self.pair("fast-1", "medium"),
        )
        self.write(self.record(), readback=readback)
        payload, code = validator.evaluate_route(
            self.record_path, self.readback_path, "run-001", "SPEC-1", "created-task"
        )
        self.assertEqual(1, code)
        self.assertIn("task_id_mismatch", self.codes(payload))

    def test_planning_handoff_rejection_uses_persistable_repair_action(self) -> None:
        record = self.record()
        record["frontier_empty"] = False
        payload, code = self.handoff(record)
        self.assertEqual(1, code)
        self.assertIn("grill_incomplete", self.codes(payload))
        self.assertEqual("repair", payload["next_action"]["kind"])
        self.assert_persistable_next_action(payload["next_action"])

    def test_receipt_failure_uses_persistable_repair_action(self) -> None:
        self.write(self.record())
        output = io.StringIO()
        with (
            mock.patch.object(
                validator, "_write_atomic", side_effect=OSError("denied")
            ),
            contextlib.redirect_stdout(output),
        ):
            exit_code = validator.main(
                [
                    "handoff",
                    "--record",
                    str(self.record_path),
                    "--controller-state",
                    str(self.state_path),
                    "--planning-readback",
                    str(self.readback_path),
                    "--expected-run-id",
                    "run-001",
                    "--receipt",
                    str(self.receipt_path),
                ]
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(1, exit_code)
        self.assertIn("receipt_unwritable", self.codes(payload))
        self.assertEqual("repair", payload["next_action"]["kind"])
        self.assert_persistable_next_action(payload["next_action"])

    def test_cli_writes_the_exact_handoff_receipt(self) -> None:
        self.write(self.record())
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "handoff",
                "--record",
                str(self.record_path),
                "--controller-state",
                str(self.state_path),
                "--planning-readback",
                str(self.readback_path),
                "--expected-run-id",
                "run-001",
                "--receipt",
                str(self.receipt_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(
            completed.stdout, self.receipt_path.read_text(encoding="utf-8")
        )
        self.assertEqual("allow", json.loads(completed.stdout)["decision"])


if __name__ == "__main__":
    unittest.main()
