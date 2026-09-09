from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SKILL_ROOT / "scripts" / "validate_controller_lifecycle.py"
SPEC = importlib.util.spec_from_file_location("validate_controller_lifecycle", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class LifecycleValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.log_path = self.root / "lifecycle-log.jsonl"
        self.receipt_path = self.root / "lifecycle-receipt.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def action(kind: str, target: str, instruction: str = "Execute the recorded action.") -> dict:
        return {"kind": kind, "target": target, "instruction": instruction}

    @staticmethod
    def add(events: list[dict], event_type: str, data: dict, actor: str = "controller") -> None:
        sequence = len(events) + 1
        events.append(
            {
                "schema_version": 1,
                "run_id": "run-001",
                "sequence": sequence,
                "type": event_type,
                "actor": actor,
                "data": data,
                "evidence": [f"evidence://{sequence}/{event_type}"],
            }
        )

    def planning(self, events: list[dict], *, two_specs: bool = False) -> None:
        specs = [{"id": "SPEC-1", "checkpoint_required": True}]
        if two_specs:
            specs.append({"id": "SPEC-2", "checkpoint_required": False})
        self.add(
            events,
            "planning_archived",
            {
                "task_id": "plan-1",
                "specs": specs,
                "default_branch": "main",
                "default_revision": "base-0",
            },
        )

    def dispatch(self, events: list[dict], number: int, base: str) -> None:
        self.add(
            events,
            "spec_dispatched",
            {
                "task_id": f"task-{number}",
                "spec_id": f"SPEC-{number}",
                "base_revision": base,
            },
        )

    def finish_spec(self, events: list[dict], number: int, revision: str, *, checkpoint: bool) -> None:
        task_id = f"task-{number}"
        spec_id = f"SPEC-{number}"
        self.add(
            events,
            "child_handoff",
            {"task_id": task_id, "boundary": "merged_evidence", "revision": revision},
            actor="spec_child",
        )
        self.add(
            events,
            "handoff_verified",
            {"task_id": task_id, "result": "pass", "revision": revision},
        )
        self.add(events, "child_archived", {"task_id": task_id})
        if checkpoint:
            self.add(events, "checkpoint_passed", {"spec_id": spec_id, "revision": revision})
        self.add(
            events,
            "default_branch_verified",
            {"spec_id": spec_id, "revision": revision},
        )

    def release(self, events: list[dict], revision: str = "merge-2", *, deploy: bool = True) -> None:
        self.add(events, "release_candidate_frozen", {"revision": revision})
        self.add(events, "artifact_built", {"revision": revision, "artifact_id": "artifact-1"})
        self.add(
            events,
            "final_tests_passed",
            {"revision": revision, "artifact_id": "artifact-1"},
        )
        self.add(
            events,
            "package_completed",
            {"revision": revision, "artifact_id": "artifact-1", "package_id": "package-1"},
        )
        if deploy:
            self.add(
                events,
                "deployment_completed",
                {
                    "revision": revision,
                    "artifact_id": "artifact-1",
                    "package_id": "package-1",
                    "target": "production",
                },
            )
            self.add(
                events,
                "smoke_passed",
                {"revision": revision, "artifact_id": "artifact-1", "target": "production"},
            )
        else:
            self.add(
                events,
                "deployment_not_applicable",
                {
                    "revision": revision,
                    "artifact_id": "artifact-1",
                    "package_id": "package-1",
                    "reason": "The repository intentionally has no deployment target.",
                },
            )
        self.add(
            events,
            "terminal_ready",
            {
                "controller_state": "terminal_success",
                "candidate_revision": revision,
                "next_action": None,
            },
        )

    def complete_events(self, *, deploy: bool = True) -> list[dict]:
        events: list[dict] = []
        self.planning(events, two_specs=True)
        self.dispatch(events, 1, "base-0")
        wait = self.action("wait", "task-1")
        self.add(
            events,
            "commentary",
            {"category": "heartbeat", "text": "实现线程仍在运行，我会继续等待。", "next_action": wait},
        )
        self.add(events, "waited", {"task_id": "task-1"})
        self.finish_spec(events, 1, "merge-1", checkpoint=True)
        self.dispatch(events, 2, "merge-1")
        self.finish_spec(events, 2, "merge-2", checkpoint=False)
        self.release(events, deploy=deploy)
        return events

    def write(self, events: list[dict]) -> None:
        serialized = "".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for event in events
        )
        self.log_path.write_text(serialized, encoding="utf-8", newline="\n")

    def evaluate(self, events: list[dict], state: str = "active"):
        self.write(events)
        return validator.evaluate(self.log_path, "run-001", state)

    @staticmethod
    def codes(payload: dict) -> set[str]:
        return {reason["code"] for reason in payload.get("reasons", [])}

    def test_complete_sequential_release_is_allowed_and_deterministic(self) -> None:
        events = self.complete_events()
        first = self.evaluate(events, "terminal_success")
        second = self.evaluate(events, "terminal_success")
        self.assertEqual(first, second)
        payload, exit_code = first
        self.assertEqual(0, exit_code)
        self.assertEqual("terminal_success", payload["derived_controller_state"])
        self.assertEqual("merge-2", payload["candidate_revision"])
        self.assertEqual("deployed", payload["release_status"])
        self.assertIsNone(payload["next_action"])
        self.assertEqual(
            ["plan-1", "task-1", "task-2"],
            [task["id"] for task in payload["child_tasks"]],
        )
        self.assertTrue(all(task["lifecycle"] == "archived" for task in payload["child_tasks"]))

    def test_running_child_heartbeats_continue_waiting_without_terminal(self) -> None:
        events: list[dict] = []
        self.planning(events)
        self.dispatch(events, 1, "base-0")
        for number in (1, 2):
            self.add(
                events,
                "commentary",
                {
                    "category": "heartbeat",
                    "text": f"第{number}次检查：子线程仍在运行，继续等待。",
                    "next_action": self.action("wait", "task-1"),
                },
            )
            self.add(events, "waited", {"task_id": "task-1"})

        payload, exit_code = self.evaluate(events, "active")
        self.assertEqual(0, exit_code)
        self.assertEqual(self.action("wait", "task-1", "Wait for the current SPEC task."), payload["next_action"])
        self.assertFalse(any(event["actor"] == "user" for event in events))

        rejected, rejected_code = self.evaluate(events, "terminal_success")
        self.assertEqual(1, rejected_code)
        self.assertIn("controller_state_mismatch", self.codes(rejected))
        self.assertEqual("wait", rejected["next_action"]["kind"])

        incomplete = events[:-1]
        rejected, rejected_code = self.evaluate(incomplete, "active")
        self.assertEqual(1, rejected_code)
        self.assertIn("continuation_missing", self.codes(rejected))

    def test_side_question_answer_resumes_the_same_stored_action(self) -> None:
        events: list[dict] = []
        self.planning(events)
        self.dispatch(events, 1, "base-0")
        self.add(
            events,
            "commentary",
            {
                "category": "side_question_answer",
                "text": "答复：当前没有失败；我现在恢复原来的等待动作。",
                "next_action": self.action("wait", "task-1"),
            },
        )
        self.add(events, "waited", {"task_id": "task-1"})
        payload, exit_code = self.evaluate(events, "active")
        self.assertEqual(0, exit_code)
        self.assertEqual("wait", payload["next_action"]["kind"])

        wrong_target = copy.deepcopy(events)
        wrong_target[-1]["data"]["task_id"] = "plan-1"
        payload, exit_code = self.evaluate(wrong_target, "active")
        self.assertEqual(1, exit_code)
        self.assertIn("continuation_not_immediate", self.codes(payload))

        english = copy.deepcopy(events)
        english[-2]["data"]["text"] = "Still running."
        payload, exit_code = self.evaluate(english, "active")
        self.assertEqual(1, exit_code)
        self.assertIn("commentary_not_chinese", self.codes(payload))

    def test_child_final_is_only_a_handoff_until_verified_and_archived(self) -> None:
        events: list[dict] = []
        self.planning(events)
        self.dispatch(events, 1, "base-0")
        self.add(
            events,
            "child_handoff",
            {"task_id": "task-1", "boundary": "merged_evidence", "revision": "merge-1"},
            actor="spec_child",
        )
        payload, exit_code = self.evaluate(events, "active")
        self.assertEqual(0, exit_code)
        self.assertEqual("verify", payload["next_action"]["kind"])
        self.assertEqual("handoff_received", payload["child_tasks"][1]["lifecycle"])

        skipped = copy.deepcopy(events)
        self.add(skipped, "child_archived", {"task_id": "task-1"})
        payload, exit_code = self.evaluate(skipped, "active")
        self.assertEqual(1, exit_code)
        self.assertIn("archive_before_verification", self.codes(payload))

    def test_only_one_spec_runs_and_next_uses_latest_verified_default(self) -> None:
        events: list[dict] = []
        self.planning(events, two_specs=True)
        self.dispatch(events, 1, "base-0")
        self.add(
            events,
            "spec_dispatched",
            {"task_id": "task-2", "spec_id": "SPEC-2", "base_revision": "base-0"},
        )
        payload, exit_code = self.evaluate(events, "active")
        self.assertEqual(1, exit_code)
        self.assertIn("overlapping_spec", self.codes(payload))

        sequential: list[dict] = []
        self.planning(sequential, two_specs=True)
        self.dispatch(sequential, 1, "base-0")
        self.finish_spec(sequential, 1, "merge-1", checkpoint=True)
        self.dispatch(sequential, 2, "base-0")
        payload, exit_code = self.evaluate(sequential, "active")
        self.assertEqual(1, exit_code)
        self.assertIn("stale_spec_base", self.codes(payload))

    def test_blocker_repair_archives_then_returns_to_exact_breakpoint(self) -> None:
        events: list[dict] = []
        self.planning(events)
        self.dispatch(events, 1, "base-0")
        blocked_action = self.action("wait", "task-1", "Wait for implementation evidence.")
        self.add(
            events,
            "blocker_opened",
            {
                "repair_task_id": "repair-1",
                "parent_task_id": "task-1",
                "fingerprint": "network:ci",
                "blocked_action": blocked_action,
            },
        )
        self.add(
            events,
            "commentary",
            {
                "category": "heartbeat",
                "text": "修复线程仍在处理阻塞，我会继续等待。",
                "next_action": self.action("wait", "repair-1"),
            },
        )
        self.add(events, "waited", {"task_id": "repair-1"})
        self.add(
            events,
            "child_handoff",
            {"task_id": "repair-1", "boundary": "repair_evidence", "revision": None},
            actor="repair_child",
        )
        self.add(
            events,
            "handoff_verified",
            {"task_id": "repair-1", "result": "pass", "revision": None},
        )
        self.add(events, "child_archived", {"task_id": "repair-1"})
        self.add(
            events,
            "blocked_action_resumed",
            {"repair_task_id": "repair-1", "parent_task_id": "task-1", "action": blocked_action},
        )
        payload, exit_code = self.evaluate(events, "active")
        self.assertEqual(0, exit_code)
        tasks = {task["id"]: task for task in payload["child_tasks"]}
        self.assertEqual("archived", tasks["repair-1"]["lifecycle"])
        self.assertEqual("active", tasks["task-1"]["lifecycle"])

        drift = copy.deepcopy(events)
        drift[-1]["data"]["action"] = self.action("wait", "another-step")
        payload, exit_code = self.evaluate(drift, "active")
        self.assertEqual(1, exit_code)
        self.assertIn("resume_action_mismatch", self.codes(payload))

    def test_restart_reconnects_recorded_child_without_duplicate_creation(self) -> None:
        events: list[dict] = []
        self.planning(events)
        self.dispatch(events, 1, "base-0")
        stored = self.action("wait", "task-1", "Wait for the existing SPEC task.")
        self.add(
            events,
            "controller_resumed",
            {"persisted_next_action": stored, "observed_task_ids": ["plan-1", "task-1"]},
        )
        self.add(events, "child_reconnected", {"task_id": "task-1"})
        self.add(events, "stored_action_resumed", {"action": stored})
        self.add(events, "waited", {"task_id": "task-1"})
        payload, exit_code = self.evaluate(events, "active")
        self.assertEqual(0, exit_code)
        self.assertEqual(2, len(payload["child_tasks"]))

        duplicate = events[:3]
        self.add(
            duplicate,
            "spec_dispatched",
            {"task_id": "task-duplicate", "spec_id": "SPEC-1", "base_revision": "base-0"},
        )
        payload, exit_code = self.evaluate(duplicate, "active")
        self.assertEqual(1, exit_code)
        self.assertIn("recovery_interrupted", self.codes(payload))

    def test_release_is_controller_only_and_ordered_after_all_specs(self) -> None:
        events: list[dict] = []
        self.planning(events)
        self.dispatch(events, 1, "base-0")
        self.add(events, "release_candidate_frozen", {"revision": "base-0"})
        payload, exit_code = self.evaluate(events, "active")
        self.assertEqual(1, exit_code)
        self.assertIn("release_before_specs_complete", self.codes(payload))

        child_release = self.complete_events()
        artifact = next(event for event in child_release if event["type"] == "artifact_built")
        artifact["actor"] = "spec_child"
        payload, exit_code = self.evaluate(child_release, "terminal_success")
        self.assertEqual(1, exit_code)
        self.assertIn("actor_mismatch", self.codes(payload))

        bad_order = self.complete_events()
        build_index = next(index for index, event in enumerate(bad_order) if event["type"] == "artifact_built")
        test_index = next(index for index, event in enumerate(bad_order) if event["type"] == "final_tests_passed")
        bad_order[build_index], bad_order[test_index] = bad_order[test_index], bad_order[build_index]
        for sequence, event in enumerate(bad_order, start=1):
            event["sequence"] = sequence
        payload, exit_code = self.evaluate(bad_order, "terminal_success")
        self.assertEqual(1, exit_code)
        self.assertIn("tests_before_build", self.codes(payload))

    def test_terminal_blocker_requires_objective_repeated_stop_rule(self) -> None:
        events: list[dict] = []
        self.planning(events)
        self.dispatch(events, 1, "base-0")
        blocked_action = self.action("wait", "task-1")
        self.add(
            events,
            "blocker_opened",
            {
                "repair_task_id": "repair-1",
                "parent_task_id": "task-1",
                "fingerprint": "credentials:deploy",
                "blocked_action": blocked_action,
            },
        )
        self.add(
            events,
            "blocker_stopped",
            {
                "repair_task_id": "repair-1",
                "parent_task_id": "task-1",
                "fingerprint": "credentials:deploy",
                "resume_action": blocked_action,
                "attempts": 3,
                "same_fingerprint_count": 3,
                "external_authority_required": True,
                "no_safe_action": True,
            },
        )
        payload, exit_code = self.evaluate(events, "terminal_blocked")
        self.assertEqual(0, exit_code)
        self.assertEqual(blocked_action, payload["resume_action"])

        premature = copy.deepcopy(events)
        premature[-1]["data"]["same_fingerprint_count"] = 2
        payload, exit_code = self.evaluate(premature, "terminal_blocked")
        self.assertEqual(1, exit_code)
        self.assertIn("stopping_rule_not_met", self.codes(payload))

    def test_explicit_user_stop_requires_paused_children_and_resume_action(self) -> None:
        events: list[dict] = []
        self.planning(events)
        self.dispatch(events, 1, "base-0")
        self.add(events, "child_paused", {"task_id": "task-1"})
        resume = self.action("resume", "task-1")
        self.add(events, "user_stop_recorded", {"resume_action": resume})
        payload, exit_code = self.evaluate(events, "user_stopped")
        self.assertEqual(0, exit_code)
        self.assertEqual(resume, payload["resume_action"])

        not_paused = events[:2] + [copy.deepcopy(events[-1])]
        for sequence, event in enumerate(not_paused, start=1):
            event["sequence"] = sequence
        payload, exit_code = self.evaluate(not_paused, "user_stopped")
        self.assertEqual(1, exit_code)
        self.assertIn("stop_with_executable_children", self.codes(payload))

    def test_deployment_not_applicable_and_cli_receipt_are_supported(self) -> None:
        events = self.complete_events(deploy=False)
        self.write(events)
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--log",
                str(self.log_path),
                "--expected-run-id",
                "run-001",
                "--expected-state",
                "terminal_success",
                "--receipt",
                str(self.receipt_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(completed.stdout, self.receipt_path.read_text(encoding="utf-8"))
        payload = json.loads(completed.stdout)
        self.assertEqual("not_applicable", payload["release_status"])


if __name__ == "__main__":
    unittest.main()
