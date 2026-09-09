from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SKILL_ROOT / "scripts" / "validate_controller_terminal.py"
SCHEMA_PATH = SKILL_ROOT / "references" / "controller-state.schema.json"
SPEC = importlib.util.spec_from_file_location(
    "validate_controller_terminal", SCRIPT_PATH
)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TerminalValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.delivery_map = self.root / "delivery-map.md"
        self.task_tree = self.root / "task-tree.json"
        self.state_path = self.root / "controller-state.json"
        self.receipt_path = self.root / "terminal-receipt.json"
        self.delivery_map.write_text(
            "# Delivery map\n\nAll work verified.\n", encoding="utf-8", newline="\n"
        )
        self.task_tree.write_text('{"tasks":[]}\n', encoding="utf-8", newline="\n")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def success_state(self, release_status: str = "deployed") -> dict:
        return {
            "schema_version": 1,
            "run_id": "run-001",
            "state_revision": 9,
            "controller_state": "terminal_success",
            "active_phase": "complete",
            "active_task_stack": [],
            "child_tasks": [
                {
                    "id": "plan",
                    "kind": "planning",
                    "spec_id": None,
                    "lifecycle": "archived",
                },
                {
                    "id": "spec-1",
                    "kind": "spec",
                    "spec_id": "SPEC-1",
                    "lifecycle": "archived",
                },
                {
                    "id": "repair-1",
                    "kind": "repair",
                    "spec_id": None,
                    "lifecycle": "archived",
                },
            ],
            "pending_specs": [],
            "unverified_handoffs": [],
            "unarchived_tasks": [],
            "test_state": {
                "status": "passed",
                "candidate_revision": "abc123",
                "evidence": ["tests/final.json"],
            },
            "release_state": {
                "status": release_status,
                "candidate_revision": "abc123",
                "evidence": ["release/evidence.json"],
            },
            "next_action": None,
            "resume_action": None,
            "freshness": {
                "delivery_map_sha256": sha256(self.delivery_map),
                "task_tree_sha256": sha256(self.task_tree),
            },
            "terminal": {
                "reason": "All delivery gates passed.",
                "evidence": ["tests/final.json", "release/evidence.json"],
                "stopping_rule_met": False,
                "user_stop_recorded": False,
            },
        }

    @staticmethod
    def action(kind: str = "resume") -> dict:
        return {
            "kind": kind,
            "target": "SPEC-2",
            "instruction": "Resume SPEC-2 after access is restored.",
        }

    def blocked_state(self) -> dict:
        state = self.success_state()
        state.update(
            {
                "controller_state": "terminal_blocked",
                "active_phase": "blocked",
                "active_task_stack": ["spec-2"],
                "child_tasks": [
                    {
                        "id": "plan",
                        "kind": "planning",
                        "spec_id": None,
                        "lifecycle": "archived",
                    },
                    {
                        "id": "spec-2",
                        "kind": "spec",
                        "spec_id": "SPEC-2",
                        "lifecycle": "paused",
                    },
                ],
                "pending_specs": ["SPEC-2"],
                "unarchived_tasks": ["spec-2"],
                "test_state": {
                    "status": "blocked",
                    "candidate_revision": None,
                    "evidence": ["blocker/test.txt"],
                },
                "release_state": {
                    "status": "pending",
                    "candidate_revision": None,
                    "evidence": [],
                },
                "resume_action": self.action(),
                "terminal": {
                    "reason": "Required external authority is unavailable.",
                    "evidence": ["blocker/access.json"],
                    "stopping_rule_met": True,
                    "user_stop_recorded": False,
                },
            }
        )
        return state

    def stopped_state(self) -> dict:
        state = self.blocked_state()
        state.update(
            {
                "controller_state": "user_stopped",
                "active_phase": "stopped",
                "terminal": {
                    "reason": "The user explicitly stopped the controller.",
                    "evidence": ["chat/message-42"],
                    "stopping_rule_met": False,
                    "user_stop_recorded": True,
                },
            }
        )
        return state

    def write_state(self, state: dict) -> None:
        if isinstance(state, dict):
            tasks = (
                state.get("child_tasks")
                if isinstance(state.get("child_tasks"), list)
                else []
            )
            task_tree = {
                "run_id": state.get("run_id", ""),
                "tasks": sorted(
                    copy.deepcopy(tasks),
                    key=lambda task: (
                        str(task.get("id", "")) if isinstance(task, dict) else ""
                    ),
                ),
            }
            self.task_tree.write_text(
                json.dumps(
                    task_tree, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            freshness = state.get("freshness")
            if isinstance(freshness, dict):
                if "delivery_map_sha256" in freshness:
                    freshness["delivery_map_sha256"] = sha256(self.delivery_map)
                if "task_tree_sha256" in freshness:
                    freshness["task_tree_sha256"] = sha256(self.task_tree)
        self.write_state_file_only(state)

    def write_state_file_only(self, state: dict) -> None:
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def evaluate(
        self, state: dict, proposed: str = "terminal_success", goal: str = "unchanged"
    ):
        self.write_state(state)
        return validator.evaluate(
            self.state_path,
            self.delivery_map,
            self.task_tree,
            "run-001",
            proposed,
            goal,
        )

    @staticmethod
    def codes(payload: dict) -> set[str]:
        return {reason["code"] for reason in payload.get("reasons", [])}

    def test_all_allowed_terminal_states_receive_a_receipt(self) -> None:
        cases = (
            (self.success_state(), "terminal_success", "complete"),
            (self.blocked_state(), "terminal_blocked", "blocked"),
            (self.stopped_state(), "user_stopped", "unchanged"),
        )
        for state, proposed, goal in cases:
            with self.subTest(proposed=proposed):
                payload, exit_code = self.evaluate(state, proposed, goal)
                self.assertEqual(0, exit_code)
                self.assertEqual("allow", payload["decision"])
                self.assertEqual(proposed, payload["terminal_state"])
                self.assertRegex(payload["receipt_sha256"], r"^[0-9a-f]{64}$")

    def test_success_allows_explicit_deployment_not_applicable(self) -> None:
        payload, exit_code = self.evaluate(
            self.success_state("not_applicable"), goal="complete"
        )
        self.assertEqual(0, exit_code)
        self.assertEqual("allow", payload["decision"])

    def test_receipt_is_deterministic_for_identical_inputs(self) -> None:
        state = self.success_state()
        first, first_code = self.evaluate(state, goal="complete")
        second, second_code = self.evaluate(state, goal="complete")
        self.assertEqual((first, first_code), (second, second_code))

    def test_cli_persists_the_exact_receipt_printed_to_stdout(self) -> None:
        self.write_state(self.success_state())
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--state",
                str(self.state_path),
                "--delivery-map",
                str(self.delivery_map),
                "--task-tree",
                str(self.task_tree),
                "--expected-run-id",
                "run-001",
                "--proposed-state",
                "terminal_success",
                "--goal-status",
                "complete",
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

    def test_active_state_rejects_final_and_returns_the_recorded_action(self) -> None:
        state = self.success_state()
        recorded_action = {
            "kind": "wait",
            "target": "spec-2",
            "instruction": "Wait for the SPEC-2 task.",
        }
        state.update(
            {
                "controller_state": "active",
                "active_phase": "implementation",
                "next_action": recorded_action,
                "terminal": None,
            }
        )
        payload, exit_code = self.evaluate(state)
        self.assertEqual(1, exit_code)
        self.assertEqual("reject", payload["decision"])
        self.assertEqual(recorded_action, payload["next_action"])
        self.assertIn("proposed_state_mismatch", self.codes(payload))

    def test_each_non_archived_child_lifecycle_rejects_success(self) -> None:
        for lifecycle in ("queued", "active", "paused", "handoff_received", "verified"):
            with self.subTest(lifecycle=lifecycle):
                state = self.success_state()
                state["child_tasks"] = [
                    {
                        "id": "spec-2",
                        "kind": "spec",
                        "spec_id": "SPEC-2",
                        "lifecycle": lifecycle,
                    }
                ]
                state["active_task_stack"] = ["spec-2"]
                state["unarchived_tasks"] = ["spec-2"]
                state["unverified_handoffs"] = (
                    ["spec-2"] if lifecycle == "handoff_received" else []
                )
                payload, exit_code = self.evaluate(state)
                self.assertEqual(1, exit_code)
                self.assertIn("terminal_child_not_archived", self.codes(payload))

    def test_success_requires_recorded_planning_and_spec_tasks(self) -> None:
        state = self.success_state()
        state["child_tasks"] = []
        payload, exit_code = self.evaluate(state)
        self.assertEqual(1, exit_code)
        self.assertIn("terminal_planning_task_missing", self.codes(payload))
        self.assertIn("terminal_spec_task_missing", self.codes(payload))

    def test_success_rejects_duplicate_archived_child_ownership(self) -> None:
        cases = (
            (
                "planning",
                lambda state: state["child_tasks"].append(
                    {
                        "id": "plan-2",
                        "kind": "planning",
                        "spec_id": None,
                        "lifecycle": "archived",
                    }
                ),
                "planning_task_count",
            ),
            (
                "spec",
                lambda state: state["child_tasks"].append(
                    {
                        "id": "spec-1-retry",
                        "kind": "spec",
                        "spec_id": "SPEC-1",
                        "lifecycle": "archived",
                    }
                ),
                "spec_task_owner_count",
            ),
        )
        for name, mutate, expected in cases:
            with self.subTest(name=name):
                state = self.success_state()
                mutate(state)
                payload, exit_code = self.evaluate(state, goal="complete")
                self.assertEqual(1, exit_code)
                self.assertIn(expected, self.codes(payload))
                self.assertEqual("repair_state", payload["next_action"]["kind"])

    def test_each_pending_gate_rejects_success(self) -> None:
        mutations = {
            "pending_spec": lambda state: state["pending_specs"].append("SPEC-2"),
            "nonempty_next_action": lambda state: state.update(
                next_action=self.action("advance")
            ),
            "unverified_handoff": lambda state: state.update(
                child_tasks=[
                    {
                        "id": "spec-2",
                        "kind": "spec",
                        "spec_id": "SPEC-2",
                        "lifecycle": "handoff_received",
                    }
                ],
                active_task_stack=["spec-2"],
                unverified_handoffs=["spec-2"],
                unarchived_tasks=["spec-2"],
            ),
            "unarchived_task": lambda state: state.update(
                child_tasks=[
                    {
                        "id": "spec-2",
                        "kind": "spec",
                        "spec_id": "SPEC-2",
                        "lifecycle": "verified",
                    }
                ],
                active_task_stack=["spec-2"],
                unarchived_tasks=["spec-2"],
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                state = self.success_state()
                mutate(state)
                payload, exit_code = self.evaluate(state)
                self.assertEqual(1, exit_code)
                self.assertEqual("reject", payload["decision"])

    def test_every_nonterminal_test_status_rejects_success(self) -> None:
        for status in ("pending", "running", "blocked"):
            with self.subTest(status=status):
                state = self.success_state()
                state["test_state"] = {
                    "status": status,
                    "candidate_revision": None,
                    "evidence": [],
                }
                payload, exit_code = self.evaluate(state)
                self.assertEqual(1, exit_code)
                self.assertIn("terminal_test_incomplete", self.codes(payload))

    def test_every_nonterminal_release_status_rejects_success(self) -> None:
        for status in ("pending", "building", "packaged", "blocked"):
            with self.subTest(status=status):
                state = self.success_state()
                state["release_state"] = {
                    "status": status,
                    "candidate_revision": None,
                    "evidence": [],
                }
                payload, exit_code = self.evaluate(state)
                self.assertEqual(1, exit_code)
                self.assertIn("terminal_release_incomplete", self.codes(payload))

    def test_missing_malformed_and_non_object_state_fail_closed(self) -> None:
        cases = (("{", "state_malformed"), ("[]", "invalid_type"))
        for raw, expected_code in cases:
            with self.subTest(raw=raw):
                self.state_path.write_text(raw, encoding="utf-8")
                payload, exit_code = validator.evaluate(
                    self.state_path,
                    self.delivery_map,
                    self.task_tree,
                    "run-001",
                    "terminal_success",
                )
                self.assertEqual(1, exit_code)
                self.assertIn(expected_code, self.codes(payload))
                self.assertEqual("repair_state", payload["next_action"]["kind"])

        self.state_path.unlink()
        payload, exit_code = validator.evaluate(
            self.state_path,
            self.delivery_map,
            self.task_tree,
            "run-001",
            "terminal_success",
        )
        self.assertEqual(1, exit_code)
        self.assertIn("state_unreadable", self.codes(payload))

    def test_every_top_level_field_is_required(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        for field in schema["required"]:
            with self.subTest(field=field):
                state = self.success_state()
                del state[field]
                payload, exit_code = self.evaluate(state)
                self.assertEqual(1, exit_code)
                self.assertIn("missing_field", self.codes(payload))

    def test_every_nested_contract_field_is_required(self) -> None:
        cases = []
        for field in ("id", "kind", "spec_id", "lifecycle"):
            cases.append(
                (
                    f"child_tasks.{field}",
                    self.success_state(),
                    lambda state, key=field: state["child_tasks"][0].pop(key),
                )
            )
        for object_name in ("test_state", "release_state"):
            for field in ("status", "candidate_revision", "evidence"):
                cases.append(
                    (
                        f"{object_name}.{field}",
                        self.success_state(),
                        lambda state, obj=object_name, key=field: state[obj].pop(key),
                    )
                )
        for field in ("delivery_map_sha256", "task_tree_sha256"):
            cases.append(
                (
                    f"freshness.{field}",
                    self.success_state(),
                    lambda state, key=field: state["freshness"].pop(key),
                )
            )
        for field in ("reason", "evidence", "stopping_rule_met", "user_stop_recorded"):
            cases.append(
                (
                    f"terminal.{field}",
                    self.success_state(),
                    lambda state, key=field: state["terminal"].pop(key),
                )
            )
        for field in ("kind", "target", "instruction"):
            active = self.success_state()
            active.update(
                controller_state="active",
                active_phase="implementation",
                next_action={
                    "kind": "wait",
                    "target": "spec-2",
                    "instruction": "Wait for SPEC-2.",
                },
                terminal=None,
            )
            cases.append(
                (
                    f"next_action.{field}",
                    active,
                    lambda state, key=field: state["next_action"].pop(key),
                )
            )

        for name, state, mutate in cases:
            with self.subTest(field=name):
                mutate(state)
                payload, exit_code = self.evaluate(state)
                self.assertEqual(1, exit_code)
                self.assertIn("missing_field", self.codes(payload))

    def test_unknown_fields_and_duplicate_ids_fail_closed(self) -> None:
        state = self.success_state()
        state["unexpected"] = True
        payload, exit_code = self.evaluate(state)
        self.assertEqual(1, exit_code)
        self.assertIn("unknown_field", self.codes(payload))

        state = self.success_state()
        state["child_tasks"].append(copy.deepcopy(state["child_tasks"][0]))
        payload, exit_code = self.evaluate(state)
        self.assertEqual(1, exit_code)
        self.assertIn("duplicate_child_id", self.codes(payload))

    def test_terminal_state_without_terminal_record_fails_closed(self) -> None:
        state = self.success_state()
        state["terminal"] = None
        payload, exit_code = self.evaluate(state)
        self.assertEqual(1, exit_code)
        self.assertIn("terminal_record_missing", self.codes(payload))

    def test_malformed_enum_types_fail_closed_without_an_exception(self) -> None:
        mutations = (
            lambda state: state.update(controller_state=[]),
            lambda state: state.update(active_phase={}),
            lambda state: state["child_tasks"][0].update(kind=[]),
            lambda state: state.update(
                next_action={"kind": [], "target": "x", "instruction": "x"}
            ),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                state = self.success_state()
                mutate(state)
                payload, exit_code = self.evaluate(state)
                self.assertEqual(1, exit_code)
                self.assertIn("invalid_enum", self.codes(payload))
                self.assertEqual("repair_state", payload["next_action"]["kind"])

    def test_stale_delivery_map_and_task_tree_return_refresh_action(self) -> None:
        for source in (self.delivery_map, self.task_tree):
            with self.subTest(source=source.name):
                self.delivery_map.write_text(
                    "# Delivery map\n", encoding="utf-8", newline="\n"
                )
                self.task_tree.write_text(
                    '{"tasks":[]}\n', encoding="utf-8", newline="\n"
                )
                self.write_state(self.success_state())
                suffix = "\n" if source == self.task_tree else "changed\n"
                source.write_text(
                    source.read_text(encoding="utf-8") + suffix,
                    encoding="utf-8",
                    newline="\n",
                )
                payload, exit_code = validator.evaluate(
                    self.state_path,
                    self.delivery_map,
                    self.task_tree,
                    "run-001",
                    "terminal_success",
                )
                self.assertEqual(1, exit_code)
                self.assertIn("stale_", " ".join(self.codes(payload)))
                self.assertEqual("refresh_state", payload["next_action"]["kind"])

    def test_missing_fingerprint_source_fails_closed(self) -> None:
        for source, expected_code in (
            (self.delivery_map, "delivery_map_unreadable"),
            (self.task_tree, "task_tree_unreadable"),
        ):
            with self.subTest(source=source.name):
                self.delivery_map.write_text(
                    "# Delivery map\n", encoding="utf-8", newline="\n"
                )
                self.task_tree.write_text(
                    '{"tasks":[]}\n', encoding="utf-8", newline="\n"
                )
                self.write_state(self.success_state())
                source.unlink()
                payload, exit_code = validator.evaluate(
                    self.state_path,
                    self.delivery_map,
                    self.task_tree,
                    "run-001",
                    "terminal_success",
                )
                self.assertEqual(1, exit_code)
                self.assertIn(expected_code, self.codes(payload))
                self.assertEqual("repair_state", payload["next_action"]["kind"])

    def test_malformed_task_tree_fails_closed(self) -> None:
        self.write_state(self.success_state())
        self.task_tree.write_text("{", encoding="utf-8")
        payload, exit_code = validator.evaluate(
            self.state_path,
            self.delivery_map,
            self.task_tree,
            "run-001",
            "terminal_success",
        )
        self.assertEqual(1, exit_code)
        self.assertIn("task_tree_malformed", self.codes(payload))
        self.assertEqual("repair_state", payload["next_action"]["kind"])

    def test_task_tree_and_child_ledger_must_match_exactly(self) -> None:
        state = self.success_state()
        self.write_state(state)
        task_tree = json.loads(self.task_tree.read_text(encoding="utf-8"))
        task_tree["tasks"][0]["lifecycle"] = "active"
        self.task_tree.write_text(
            json.dumps(task_tree, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        state["freshness"]["task_tree_sha256"] = sha256(self.task_tree)
        self.write_state_file_only(state)
        payload, exit_code = validator.evaluate(
            self.state_path,
            self.delivery_map,
            self.task_tree,
            "run-001",
            "terminal_success",
        )
        self.assertEqual(1, exit_code)
        self.assertIn("task_tree_task_mismatch", self.codes(payload))
        self.assertNotIn("stale_task_tree", self.codes(payload))

    def test_current_task_tree_cannot_omit_a_recorded_child(self) -> None:
        state = self.success_state()
        self.write_state(state)
        task_tree = json.loads(self.task_tree.read_text(encoding="utf-8"))
        task_tree["tasks"].pop()
        self.task_tree.write_text(
            json.dumps(task_tree, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        state["freshness"]["task_tree_sha256"] = sha256(self.task_tree)
        self.write_state_file_only(state)
        payload, exit_code = validator.evaluate(
            self.state_path,
            self.delivery_map,
            self.task_tree,
            "run-001",
            "terminal_success",
        )
        self.assertEqual(1, exit_code)
        self.assertIn("task_tree_task_set_mismatch", self.codes(payload))
        self.assertNotIn("stale_task_tree", self.codes(payload))

    def test_malformed_fingerprint_fails_closed(self) -> None:
        state = self.success_state()
        self.write_state(state)
        state["freshness"]["delivery_map_sha256"] = "ABC"
        self.write_state_file_only(state)
        payload, exit_code = validator.evaluate(
            self.state_path,
            self.delivery_map,
            self.task_tree,
            "run-001",
            "terminal_success",
        )
        self.assertEqual(1, exit_code)
        self.assertIn("invalid_sha256", self.codes(payload))
        self.assertEqual("repair_state", payload["next_action"]["kind"])

    def test_wrong_run_id_fails_as_stale(self) -> None:
        payload, exit_code = self.evaluate(
            {**self.success_state(), "run_id": "other-run"}
        )
        self.assertEqual(1, exit_code)
        self.assertIn("run_id_mismatch", self.codes(payload))
        self.assertEqual("refresh_state", payload["next_action"]["kind"])

    def test_inconsistent_aggregates_fail_closed(self) -> None:
        state = self.success_state()
        state["unarchived_tasks"] = ["plan"]
        state["unverified_handoffs"] = ["spec-1"]
        payload, exit_code = self.evaluate(state)
        self.assertEqual(1, exit_code)
        self.assertIn("unarchived_tasks_mismatch", self.codes(payload))
        self.assertIn("unverified_handoffs_mismatch", self.codes(payload))
        self.assertEqual("repair_state", payload["next_action"]["kind"])

    def test_success_rejects_candidate_mismatch_and_missing_evidence(self) -> None:
        state = self.success_state()
        state["release_state"]["candidate_revision"] = "different"
        state["test_state"]["evidence"] = []
        state["terminal"]["evidence"] = []
        payload, exit_code = self.evaluate(state)
        self.assertEqual(1, exit_code)
        codes = self.codes(payload)
        self.assertIn("candidate_revision_mismatch", codes)
        self.assertIn("terminal_test_incomplete", codes)
        self.assertIn("terminal_evidence_missing", codes)

    def test_blocked_and_stopped_reject_executable_child_work(self) -> None:
        for state, proposed, goal in (
            (self.blocked_state(), "terminal_blocked", "blocked"),
            (self.stopped_state(), "user_stopped", "unchanged"),
        ):
            with self.subTest(proposed=proposed):
                state["child_tasks"][1]["lifecycle"] = "active"
                payload, exit_code = self.evaluate(state, proposed, goal)
                self.assertEqual(1, exit_code)
                self.assertIn("terminal_executable_child_work", self.codes(payload))

    def test_user_stop_preserves_a_returned_unverified_handoff(self) -> None:
        state = self.stopped_state()
        state["child_tasks"][1]["lifecycle"] = "handoff_received"
        state["active_task_stack"] = []
        state["unverified_handoffs"] = ["spec-2"]
        payload, exit_code = self.evaluate(state, "user_stopped", "unchanged")
        self.assertEqual(0, exit_code)
        self.assertEqual("allow", payload["decision"])

    def test_goal_status_uses_the_same_terminal_gate(self) -> None:
        cases = (
            (self.blocked_state(), "terminal_blocked", "complete"),
            (self.success_state(), "terminal_success", "blocked"),
            (self.stopped_state(), "user_stopped", "complete"),
        )
        for state, proposed, goal in cases:
            with self.subTest(proposed=proposed, goal=goal):
                payload, exit_code = self.evaluate(state, proposed, goal)
                self.assertEqual(1, exit_code)
                self.assertIn("goal_status_mismatch", self.codes(payload))

    def test_programmatic_calls_fail_closed_on_unknown_terminal_arguments(self) -> None:
        self.write_state(self.success_state())
        payload, exit_code = validator.evaluate(
            self.state_path,
            self.delivery_map,
            self.task_tree,
            "run-001",
            "active",
            "finished",
        )
        self.assertEqual(1, exit_code)
        self.assertIn("invalid_proposed_state", self.codes(payload))
        self.assertIn("invalid_goal_status", self.codes(payload))

    def test_schema_required_fields_match_the_executable_contract(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(SCHEMA_PATH, validator.SCHEMA_PATH)
        self.assertEqual(
            {
                "schema_version",
                "run_id",
                "state_revision",
                "controller_state",
                "active_phase",
                "active_task_stack",
                "child_tasks",
                "pending_specs",
                "unverified_handoffs",
                "unarchived_tasks",
                "test_state",
                "release_state",
                "next_action",
                "resume_action",
                "freshness",
                "terminal",
            },
            set(schema["required"]),
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(1, schema["properties"]["schema_version"]["const"])


if __name__ == "__main__":
    unittest.main()
