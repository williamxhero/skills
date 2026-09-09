from __future__ import annotations

import contextlib
import copy
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
SCRIPT_PATH = SKILL_ROOT / "scripts" / "validate_controller_lifecycle.py"
SPEC = importlib.util.spec_from_file_location(
    "validate_controller_lifecycle", SCRIPT_PATH
)
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
    def action(
        kind: str, target: str, instruction: str = "Execute the recorded action."
    ) -> dict:
        return {"kind": kind, "target": target, "instruction": instruction}

    @staticmethod
    def add(
        events: list[dict], event_type: str, data: dict, actor: str = "controller"
    ) -> None:
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

    @staticmethod
    def checkpoint_plan(count: int) -> list[dict]:
        spec_ids = [f"SPEC-{number}" for number in range(1, count + 1)]
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

    def planning(self, events: list[dict], *, spec_count: int = 1) -> None:
        specs = [
            {
                "id": f"SPEC-{number}",
                "tickets": [{"id": f"T{number}", "blocked_by": []}],
            }
            for number in range(1, spec_count + 1)
        ]
        self.add(
            events,
            "planning_archived",
            {
                "task_id": "plan-1",
                "specs": specs,
                "checkpoint_size": 10,
                "checkpoints": self.checkpoint_plan(spec_count),
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
                "route_selection": "recommended",
                "route_evidence": [f"receipt://SPEC-{number}/route"],
            },
        )

    def finish_spec(self, events: list[dict], number: int, revision: str) -> None:
        task_id = f"task-{number}"
        spec_id = f"SPEC-{number}"
        self.add(
            events,
            "ticket_evidence",
            {
                "spec_id": spec_id,
                "ticket_id": f"T{number}",
                "owner_task_id": task_id,
                "blocked_by": [],
                "commits": [f"git://{revision}/ticket-{number}"],
                "test_evidence": [f"test://ticket-{number}"],
                "tracker_state": "closed",
            },
            actor="spec_child",
        )
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
        self.add(
            events,
            "default_branch_verified",
            {"spec_id": spec_id, "revision": revision},
        )

    def pass_checkpoint(self, events: list[dict], position: int, revision: str) -> None:
        self.add(
            events,
            "checkpoint_passed",
            {
                "checkpoint_id": f"checkpoint-{position}",
                "revision": revision,
                "candidate_revisions": [revision],
                "affected_owners": ["owner-a"],
                "affected_repositories": ["repo-a"],
            },
        )

    def release(
        self, events: list[dict], revision: str = "merge-2", *, deploy: bool = True
    ) -> None:
        self.add(events, "release_candidate_frozen", {"revision": revision})
        self.add(
            events,
            "artifact_built",
            {"revision": revision, "artifact_id": "artifact-1"},
        )
        self.add(
            events,
            "final_tests_passed",
            {
                "revision": revision,
                "artifact_id": "artifact-1",
                "candidate_revisions": [revision],
                "l4_reused_checkpoint": f"checkpoint-{revision.removeprefix('merge-')}",
            },
        )
        self.add(
            events,
            "package_completed",
            {
                "revision": revision,
                "artifact_id": "artifact-1",
                "package_id": "package-1",
            },
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
                {
                    "revision": revision,
                    "artifact_id": "artifact-1",
                    "target": "production",
                },
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
        self.planning(events, spec_count=2)
        self.dispatch(events, 1, "base-0")
        wait = self.action("wait", "task-1")
        self.add(
            events,
            "commentary",
            {
                "category": "heartbeat",
                "text": "实现线程仍在运行，我会继续等待。",
                "next_action": wait,
            },
        )
        self.add(events, "waited", {"task_id": "task-1"})
        self.finish_spec(events, 1, "merge-1")
        self.dispatch(events, 2, "merge-1")
        self.finish_spec(events, 2, "merge-2")
        self.pass_checkpoint(events, 2, "merge-2")
        self.release(events, deploy=deploy)
        return events

    def write(self, events: list[dict]) -> None:
        serialized = "".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
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
        self.assertTrue(
            all(task["lifecycle"] == "archived" for task in payload["child_tasks"])
        )

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
                checkpoints = self.checkpoint_plan(count)
                self.assertEqual(
                    expected_positions,
                    [checkpoint["end_spec_index"] for checkpoint in checkpoints],
                )

    def test_due_checkpoint_blocks_next_segment_dispatch(self) -> None:
        events: list[dict] = []
        self.planning(events, spec_count=11)
        base = "base-0"
        for number in range(1, 11):
            revision = f"merge-{number}"
            self.dispatch(events, number, base)
            self.finish_spec(events, number, revision)
            base = revision
        self.dispatch(events, 11, "merge-10")

        payload, exit_code = self.evaluate(events, "active")
        self.assertEqual(1, exit_code)
        self.assertIn("checkpoint_blocks_next_segment", self.codes(payload))

        recovered = events[:-1]
        self.pass_checkpoint(recovered, 10, "merge-10")
        self.dispatch(recovered, 11, "merge-10")
        payload, exit_code = self.evaluate(recovered, "active")
        self.assertEqual(0, exit_code)
        self.assertEqual("wait", payload["next_action"]["kind"])

    def test_failed_checkpoint_blocks_next_segment_until_green(self) -> None:
        events: list[dict] = []
        self.planning(events, spec_count=11)
        base = "base-0"
        for number in range(1, 11):
            revision = f"merge-{number}"
            self.dispatch(events, number, base)
            self.finish_spec(events, number, revision)
            base = revision
        self.add(
            events,
            "checkpoint_failed",
            {
                "checkpoint_id": "checkpoint-10",
                "revision": "merge-10",
                "candidate_revisions": ["merge-10"],
                "affected_owners": ["owner-a"],
                "affected_repositories": ["repo-a"],
                "reason": "Owner regression failed.",
            },
        )
        self.dispatch(events, 11, "merge-10")

        payload, exit_code = self.evaluate(events, "active")
        self.assertEqual(1, exit_code)
        self.assertIn("checkpoint_blocks_next_segment", self.codes(payload))

    def test_final_l4_reuse_requires_exact_candidate_revisions(self) -> None:
        events: list[dict] = []
        self.planning(events)
        self.dispatch(events, 1, "base-0")
        self.finish_spec(events, 1, "merge-1")
        self.pass_checkpoint(events, 1, "merge-1")
        self.add(events, "release_candidate_frozen", {"revision": "merge-1"})
        self.add(
            events,
            "artifact_built",
            {"revision": "merge-1", "artifact_id": "artifact-1"},
        )
        self.add(
            events,
            "final_tests_passed",
            {
                "revision": "merge-1",
                "artifact_id": "artifact-1",
                "candidate_revisions": ["merge-1", "repo-b@later"],
                "l4_reused_checkpoint": "checkpoint-1",
            },
        )

        payload, exit_code = self.evaluate(events, "active")
        self.assertEqual(1, exit_code)
        self.assertIn("stale_final_checkpoint_revisions", self.codes(payload))

    def test_final_l4_rerun_is_required_only_after_candidate_changes(self) -> None:
        events: list[dict] = []
        self.planning(events)
        self.dispatch(events, 1, "base-0")
        self.finish_spec(events, 1, "merge-1")
        self.pass_checkpoint(events, 1, "merge-1")
        self.add(events, "release_candidate_frozen", {"revision": "merge-1"})
        self.add(
            events,
            "artifact_built",
            {"revision": "merge-1", "artifact_id": "artifact-1"},
        )
        self.add(
            events,
            "final_tests_passed",
            {
                "revision": "merge-1",
                "artifact_id": "artifact-1",
                "candidate_revisions": ["merge-1", "repo-b@later"],
                "l4_reused_checkpoint": None,
            },
        )

        payload, exit_code = self.evaluate(events, "active")
        self.assertEqual(0, exit_code)
        self.assertEqual("package", payload["next_action"]["target"])

        duplicate = copy.deepcopy(events)
        duplicate[-1]["data"]["candidate_revisions"] = ["merge-1"]
        payload, exit_code = self.evaluate(duplicate, "active")
        self.assertEqual(1, exit_code)
        self.assertIn("final_l4_duplicate", self.codes(payload))

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
        self.assertEqual(
            self.action("wait", "task-1", "Wait for the current SPEC task."),
            payload["next_action"],
        )
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
            "ticket_evidence",
            {
                "spec_id": "SPEC-1",
                "ticket_id": "T1",
                "owner_task_id": "task-1",
                "blocked_by": [],
                "commits": ["git://merge-1/ticket-1"],
                "test_evidence": ["test://ticket-1"],
                "tracker_state": "closed",
            },
            actor="spec_child",
        )
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

    def test_spec_handoff_requires_all_ticket_evidence(self) -> None:
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
        self.assertEqual(1, exit_code)
        self.assertIn("ticket_evidence_missing", self.codes(payload))

    def test_only_one_spec_runs_and_next_uses_latest_verified_default(self) -> None:
        events: list[dict] = []
        self.planning(events, spec_count=2)
        self.dispatch(events, 1, "base-0")
        self.add(
            events,
            "spec_dispatched",
            {
                "task_id": "task-2",
                "spec_id": "SPEC-2",
                "base_revision": "base-0",
                "route_selection": "recommended",
                "route_evidence": ["receipt://SPEC-2/route"],
            },
        )
        payload, exit_code = self.evaluate(events, "active")
        self.assertEqual(1, exit_code)
        self.assertIn("overlapping_spec", self.codes(payload))

        sequential: list[dict] = []
        self.planning(sequential, spec_count=2)
        self.dispatch(sequential, 1, "base-0")
        self.finish_spec(sequential, 1, "merge-1")
        self.dispatch(sequential, 2, "base-0")
        payload, exit_code = self.evaluate(sequential, "active")
        self.assertEqual(1, exit_code)
        self.assertIn("stale_spec_base", self.codes(payload))

    def test_blocker_repair_archives_then_returns_to_exact_breakpoint(self) -> None:
        events: list[dict] = []
        self.planning(events)
        self.dispatch(events, 1, "base-0")
        blocked_action = self.action(
            "wait", "task-1", "Wait for implementation evidence."
        )
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
            {
                "repair_task_id": "repair-1",
                "parent_task_id": "task-1",
                "action": blocked_action,
            },
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

    def test_ticket_owner_mismatch_and_ticket_artifacts_reject(self) -> None:
        events: list[dict] = []
        self.planning(events)
        self.dispatch(events, 1, "base-0")
        self.add(
            events,
            "ticket_evidence",
            {
                "spec_id": "SPEC-1",
                "ticket_id": "T1",
                "owner_task_id": "ticket-task-1",
                "blocked_by": [],
                "commits": ["git://ticket-1"],
                "test_evidence": ["test://ticket-1"],
                "tracker_state": "closed",
            },
            actor="spec_child",
        )
        payload, exit_code = self.evaluate(events, "active")
        self.assertEqual(1, exit_code)
        self.assertIn("ticket_owner_mismatch", self.codes(payload))

        artifact = events[:2]
        self.add(
            artifact,
            "ticket_implementation_artifact",
            {
                "artifact_type": "thread",
                "id": "ticket-thread-1",
                "spec_id": "SPEC-1",
                "ticket_id": "T1",
            },
        )
        payload, exit_code = self.evaluate(artifact, "active")
        self.assertEqual(1, exit_code)
        self.assertIn("ticket_implementation_artifact_present", self.codes(payload))

    def test_read_only_review_is_role_limited_and_cannot_merge(self) -> None:
        events: list[dict] = []
        self.planning(events)
        self.dispatch(events, 1, "base-0")
        self.add(
            events,
            "role_limited_task",
            {
                "task_id": "review-1",
                "spec_id": "SPEC-1",
                "parent_task_id": "task-1",
                "role": "read_only_review",
                "writes_product_code": False,
                "merge_commits": [],
            },
        )
        payload, exit_code = self.evaluate(events, "active")
        self.assertEqual(0, exit_code)
        self.assertEqual(
            "review-1",
            payload["implementation_ownership"]["role_limited_tasks"][0]["task_id"],
        )

        mutated = copy.deepcopy(events)
        mutated[-1]["data"]["writes_product_code"] = True
        mutated[-1]["data"]["merge_commits"] = ["merge-review"]
        payload, exit_code = self.evaluate(mutated, "active")
        self.assertEqual(1, exit_code)
        self.assertIn("role_limited_task_mutated_product", self.codes(payload))

    def test_restart_reconnects_recorded_child_without_duplicate_creation(self) -> None:
        events: list[dict] = []
        self.planning(events)
        self.dispatch(events, 1, "base-0")
        stored = self.action("wait", "task-1", "Wait for the existing SPEC task.")
        self.add(
            events,
            "controller_resumed",
            {
                "persisted_next_action": stored,
                "observed_task_ids": ["plan-1", "task-1"],
            },
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
            {
                "task_id": "task-duplicate",
                "spec_id": "SPEC-1",
                "base_revision": "base-0",
                "route_selection": "recommended",
                "route_evidence": ["receipt://SPEC-1/route"],
            },
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
        artifact = next(
            event for event in child_release if event["type"] == "artifact_built"
        )
        artifact["actor"] = "spec_child"
        payload, exit_code = self.evaluate(child_release, "terminal_success")
        self.assertEqual(1, exit_code)
        self.assertIn("actor_mismatch", self.codes(payload))

        bad_order = self.complete_events()
        build_index = next(
            index
            for index, event in enumerate(bad_order)
            if event["type"] == "artifact_built"
        )
        test_index = next(
            index
            for index, event in enumerate(bad_order)
            if event["type"] == "final_tests_passed"
        )
        bad_order[build_index], bad_order[test_index] = (
            bad_order[test_index],
            bad_order[build_index],
        )
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

    def test_explicit_user_stop_requires_paused_children_and_resume_action(
        self,
    ) -> None:
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
        self.assertEqual(
            completed.stdout, self.receipt_path.read_text(encoding="utf-8")
        )
        payload = json.loads(completed.stdout)
        self.assertEqual("not_applicable", payload["release_status"])

    def test_receipt_write_failure_preserves_derived_lifecycle_state(self) -> None:
        self.write(self.complete_events())
        output = io.StringIO()
        with (
            mock.patch.object(
                validator, "_write_atomic", side_effect=OSError("denied")
            ),
            contextlib.redirect_stdout(output),
        ):
            exit_code = validator.main(
                [
                    "--log",
                    str(self.log_path),
                    "--expected-run-id",
                    "run-001",
                    "--expected-state",
                    "terminal_success",
                    "--receipt",
                    str(self.receipt_path),
                ]
            )
        payload = json.loads(output.getvalue())
        self.assertEqual(1, exit_code)
        self.assertEqual("reject", payload["decision"])
        self.assertEqual("terminal_success", payload["derived_controller_state"])
        self.assertIn("receipt_unwritable", self.codes(payload))
        self.assertIn("lifecycle receipt", payload["reasons"][0]["message"])


if __name__ == "__main__":
    unittest.main()
