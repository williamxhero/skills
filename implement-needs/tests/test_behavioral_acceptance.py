"""End-to-end controller protocol acceptance using the installed validators."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
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
        self.planning_record_sha256 = "a" * 64
        self.route_receipts: dict[str, dict[str, Any]] = {}

    @staticmethod
    def pair(model: str, thinking: str) -> dict[str, str]:
        return {"model": model, "thinking": thinking}

    @staticmethod
    def spec_id(number: int) -> str:
        return f"SPEC-{number}"

    @staticmethod
    def task_id(number: int) -> str:
        return f"task-{number}"

    @staticmethod
    def revision(number: int) -> str:
        return f"merge-{number}"

    @staticmethod
    def checkpoint_surfaces(end_spec_index: int) -> dict[str, list[str]]:
        segment = ((end_spec_index - 1) // 10) + 1
        return {
            "affected_owners": [f"owner-{segment}"],
            "affected_repositories": [f"repo-{segment}"],
        }

    @classmethod
    def checkpoint_plan(cls, spec_ids: list[str]) -> list[dict[str, Any]]:
        checkpoints: list[dict[str, Any]] = []
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
                    **cls.checkpoint_surfaces(end),
                }
            )
        return checkpoints

    @staticmethod
    def checkpoint_for_spec(number: int, spec_count: int) -> str:
        return f"checkpoint-{min(((number + 9) // 10) * 10, spec_count)}"

    @classmethod
    def ticket_plan(cls, number: int) -> list[dict[str, Any]]:
        if number == 7:
            return [
                {"id": "T7-foundation", "blocked_by": []},
                {"id": "T7-api", "blocked_by": ["T7-foundation"]},
                {"id": "T7-smoke", "blocked_by": ["T7-foundation"]},
            ]
        return [{"id": f"T{number}", "blocked_by": []}]

    def route(
        self, recommended: dict[str, str], fallback: dict[str, str]
    ) -> dict[str, Any]:
        return {
            "recommended": recommended,
            "fallbacks": [fallback],
            "rationale": "Risk and coupling justify this route.",
        }

    def route_receipt(
        self,
        target: str,
        task_id: str,
        *,
        selection: str = "recommended",
        model: str = "gpt-5.6-terra",
        thinking: str = "xhigh",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": 1,
            "decision": "allow",
            "gate": "task_route",
            "run_id": RUN_ID,
            "target": target,
            "task_id": task_id,
            "selection": selection,
            "applied": self.pair(model, thinking),
            "locked_route": {
                "recommended": self.pair("gpt-5.6-terra", "xhigh"),
                "fallbacks": [self.pair("gpt-5.6-sol", "xhigh")],
            },
            "planning_record_sha256": self.planning_record_sha256,
            "route_readback_sha256": "b" * 64,
        }
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        payload["receipt_sha256"] = hashlib.sha256(canonical).hexdigest()
        return payload

    def planning_record(self, spec_count: int = 1) -> dict[str, Any]:
        approval = {
            "confirmation_mode": "auto_approve",
            "approval_source": "implement-needs",
            "approval_text": "\u540c\u610f",
            "spec_state": "auto_approved",
            "approval_provenance": "controller_decision",
        }
        ticket_check = {
            "granularity": "pass",
            "blocking_edges": "pass",
            "acyclic": "pass",
            "evidence": ["tracker://ticket-self-check"],
        }
        spec_ids = [self.spec_id(number) for number in range(1, spec_count + 1)]
        checkpoints = self.checkpoint_plan(spec_ids)
        owners = []
        repositories = []
        for checkpoint in checkpoints:
            owners.extend(checkpoint["affected_owners"])
            repositories.extend(checkpoint["affected_repositories"])

        return {
            "schema_version": 1,
            "run_id": RUN_ID,
            "scope": "bounded",
            "capability_evidence": ["tool://create-thread-schema"],
            "supported_routes": [
                {
                    "model": "gpt-5.6-terra",
                    "thinking": ["medium", "high", "xhigh"],
                },
                {
                    "model": "gpt-5.6-sol",
                    "thinking": ["medium", "high", "xhigh"],
                },
            ],
            "planning_task": {
                "id": "plan-1",
                "generation": 1,
                "route": self.route(
                    self.pair("gpt-5.6-terra", "xhigh"),
                    self.pair("gpt-5.6-sol", "xhigh"),
                ),
            },
            "ownership": {
                "grill": "plan-1",
                "specs": "plan-1",
                "tickets": "plan-1",
                "routing": "plan-1",
            },
            "requirements": [f"R{number}" for number in range(1, spec_count + 1)],
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
                    "acceptance_command": "全部采用推荐选项/答案",
                    "acceptance_evidence": ["controller://accepted-round-1"],
                    "planner_resume_evidence": ["thread://plan-1/round-1"],
                }
            ],
            "frontier_empty": True,
            "umbrella_spec": {
                "id": "SPEC-ROOT",
                "artifact": "https://github.com/example/repo/issues/100",
            },
            "specs": [
                {
                    "id": self.spec_id(number),
                    "artifact": f"tracker://spec-{number}",
                    "parent_issue": {
                        "parent_id": "SPEC-ROOT",
                        "evidence": [f"github://spec-{number}/parent-readback"],
                    },
                    "requirements": [f"R{number}"],
                    "blocked_by": [] if number == 1 else [self.spec_id(number - 1)],
                    "auto_approval": copy.deepcopy(approval),
                    "tickets": [
                        {
                            "id": ticket["id"],
                            "artifact": f"tracker://ticket-{ticket['id']}",
                            "parent_issue": {
                                "parent_id": self.spec_id(number),
                                "evidence": [f"github://ticket-{ticket['id']}/parent-readback"],
                            },
                            "blocked_by": list(ticket["blocked_by"]),
                            "vertical_slice": (
                                f"Delivers {self.spec_id(number)} ticket "
                                f"{ticket['id']} through commit and test evidence."
                            ),
                        }
                        for ticket in self.ticket_plan(number)
                    ],
                    "ticket_self_check": copy.deepcopy(ticket_check),
                    "difficulty": "hard",
                    "owners": [f"owner-{((number - 1) // 10) + 1}"],
                    "repositories": [f"repo-{((number - 1) // 10) + 1}"],
                    "route": self.route(
                        self.pair("gpt-5.6-terra", "xhigh"),
                        self.pair("gpt-5.6-sol", "xhigh"),
                    ),
                    "checkpoint": self.checkpoint_for_spec(number, spec_count),
                }
                for number in range(1, spec_count + 1)
            ],
            "release_train": {
                "owners": list(dict.fromkeys(owners)),
                "repositories": list(dict.fromkeys(repositories)),
                "acceptance_scopes": ["scope-a"],
                "public_contract_specs": [self.spec_id(spec_count)],
                "environment_specs": [],
                "baselines": ["main@base-0"],
                "checkpoint_size": 10,
                "checkpoints": checkpoints,
            },
            "code_read_only": {
                "product_test_tree_before_sha256": "a" * 64,
                "product_test_tree_after_sha256": "a" * 64,
                "changed_product_or_test_paths": [],
                "evidence": ["git://planning-read-only-check"],
            },
            "handoff_evidence": ["thread://plan-1/handoff"],
        }

    def readback(
        self,
        target: str,
        task_id: str,
        *,
        requested: dict[str, str] | None = None,
        selection: str = "recommended",
        reason: str | None = None,
    ) -> dict[str, Any]:
        requested = requested or self.pair("gpt-5.6-terra", "xhigh")
        return {
            "schema_version": 1,
            "run_id": RUN_ID,
            "task_id": task_id,
            "target": target,
            "requested": requested,
            "applied": copy.deepcopy(requested),
            "selection": selection,
            "substitution_reason": reason,
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

    def write_route_artifacts(
        self,
        target: str,
        task_id: str,
        *,
        spec_count: int = 1,
        readback: dict[str, Any] | None = None,
    ) -> None:
        self.write_json(self.record_path, self.planning_record(spec_count))
        self.planning_record_sha256 = _sha256(self.record_path)
        self.write_json(self.readback_path, readback or self.readback(target, task_id))

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

    @staticmethod
    def action(
        kind: str, target: str, instruction: str = "Execute the recorded action."
    ) -> dict[str, str]:
        return {"kind": kind, "target": target, "instruction": instruction}

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

    def emit_planning_event(
        self, events: list[dict[str, Any]], spec_count: int
    ) -> None:
        specs = [
            {
                "id": self.spec_id(number),
                "tickets": [
                    {"id": ticket["id"], "blocked_by": list(ticket["blocked_by"])}
                    for ticket in self.ticket_plan(number)
                ],
                "owners": [f"owner-{((number - 1) // 10) + 1}"],
                "repositories": [f"repo-{((number - 1) // 10) + 1}"],
            }
            for number in range(1, spec_count + 1)
        ]
        self.event(
            events,
            "planning_archived",
            {
                "task_id": "plan-1",
                "specs": specs,
                "checkpoint_size": 10,
                "checkpoints": self.checkpoint_plan(
                    [self.spec_id(number) for number in range(1, spec_count + 1)]
                ),
                "default_branch": "main",
                "default_revision": "base-0",
                "planning_record_sha256": self.planning_record_sha256,
            },
        )

    def emit_dispatch(
        self,
        events: list[dict[str, Any]],
        number: int,
        base_revision: str,
        *,
        fallback: bool = False,
    ) -> None:
        selection = "fallback" if fallback else "recommended"
        target = self.spec_id(number)
        task_id = self.task_id(number)
        receipt = self.route_receipts.get(target) or self.route_receipt(
            target,
            task_id,
            selection=selection,
            model="gpt-5.6-sol" if fallback else "gpt-5.6-terra",
        )
        self.event(
            events,
            "spec_dispatched",
            {
                "task_id": task_id,
                "spec_id": target,
                "base_revision": base_revision,
                "route_selection": selection,
                "model": "gpt-5.6-sol" if fallback else "gpt-5.6-terra",
                "thinking": "xhigh",
                "route_receipt": receipt,
            },
        )

    def emit_recovery(self, events: list[dict[str, Any]], number: int) -> None:
        task_id = self.task_id(number)
        wait = self.action("wait", task_id, "Wait for the existing SPEC task.")
        observed = sorted(
            ["plan-1"] + [self.task_id(index) for index in range(1, number + 1)]
        )
        self.event(
            events,
            "controller_resumed",
            {"persisted_next_action": wait, "observed_task_ids": observed},
        )
        self.event(
            events,
            "child_reconnected",
            {
                "task_id": task_id,
                "route_receipt": self.route_receipts.get(self.spec_id(number))
                or self.route_receipt(self.spec_id(number), task_id),
            },
        )
        self.event(events, "stored_action_resumed", {"action": wait})
        self.event(events, "waited", {"task_id": task_id})

    def emit_blocker_round_trip(
        self, events: list[dict[str, Any]], number: int
    ) -> None:
        task_id = self.task_id(number)
        repair_id = f"repair-{number}"
        blocked_action = self.action(
            "wait", task_id, "Wait for implementation evidence."
        )
        wait_repair = self.action(
            "wait", repair_id, "Wait for the focused repair task."
        )
        self.event(
            events,
            "blocker_opened",
            {
                "repair_task_id": repair_id,
                "parent_task_id": task_id,
                "fingerprint": "ci:network",
                "blocked_action": blocked_action,
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
        self.event(events, "waited", {"task_id": repair_id})
        self.event(
            events,
            "child_handoff",
            {"task_id": repair_id, "boundary": "repair_evidence", "revision": None},
            actor="repair_child",
        )
        self.event(
            events,
            "handoff_verified",
            {"task_id": repair_id, "result": "pass", "revision": None},
        )
        self.event(events, "child_archived", {"task_id": repair_id})
        self.event(
            events,
            "blocked_action_resumed",
            {
                "repair_task_id": repair_id,
                "parent_task_id": task_id,
                "action": blocked_action,
            },
        )

    def emit_read_only_review(self, events: list[dict[str, Any]], number: int) -> None:
        self.event(
            events,
            "role_limited_task",
            {
                "task_id": f"review-{number}",
                "spec_id": self.spec_id(number),
                "parent_task_id": self.task_id(number),
                "role": "read_only_review",
                "writes_product_code": False,
                "merge_commits": [],
            },
        )

    def emit_ticket_evidence(
        self, events: list[dict[str, Any]], number: int, revision: str
    ) -> None:
        for ticket in self.ticket_plan(number):
            self.event(
                events,
                "ticket_evidence",
                {
                    "spec_id": self.spec_id(number),
                    "ticket_id": ticket["id"],
                    "owner_task_id": self.task_id(number),
                    "blocked_by": list(ticket["blocked_by"]),
                    "commits": [f"git://{revision}/{ticket['id']}"],
                    "test_evidence": [f"test://{ticket['id']}"],
                    "tracker_state": "closed",
                },
                actor="spec_child",
            )

    def finish_spec(
        self, events: list[dict[str, Any]], number: int, revision: str
    ) -> None:
        task_id = self.task_id(number)
        self.emit_ticket_evidence(events, number, revision)
        self.event(
            events,
            "child_handoff",
            {"task_id": task_id, "boundary": "merged_evidence", "revision": revision},
            actor="spec_child",
        )
        self.event(
            events,
            "handoff_verified",
            {"task_id": task_id, "result": "pass", "revision": revision},
        )
        self.event(events, "child_archived", {"task_id": task_id})
        self.event(
            events,
            "default_branch_verified",
            {"spec_id": self.spec_id(number), "revision": revision},
        )

    def pass_checkpoint(
        self, events: list[dict[str, Any]], position: int, revision: str
    ) -> None:
        self.event(
            events,
            "checkpoint_passed",
            {
                "checkpoint_id": f"checkpoint-{position}",
                "revision": revision,
                "candidate_revisions": [revision],
                **self.checkpoint_surfaces(position),
            },
        )

    def emit_release(
        self, events: list[dict[str, Any]], revision: str, *, deploy: bool = True
    ) -> None:
        artifact_id = "artifact-1"
        package_id = "package-1"
        self.event(events, "release_candidate_frozen", {"revision": revision})
        self.event(
            events,
            "artifact_built",
            {"revision": revision, "artifact_id": artifact_id},
        )
        self.event(
            events,
            "final_tests_passed",
            {
                "revision": revision,
                "artifact_id": artifact_id,
                "candidate_revisions": [revision],
                "l4_reused_checkpoint": f"checkpoint-{revision.removeprefix('merge-')}",
            },
        )
        self.event(
            events,
            "package_completed",
            {
                "revision": revision,
                "artifact_id": artifact_id,
                "package_id": package_id,
            },
        )
        if deploy:
            self.event(
                events,
                "deployment_completed",
                {
                    "revision": revision,
                    "artifact_id": artifact_id,
                    "package_id": package_id,
                    "target": "production",
                },
            )
            self.event(
                events,
                "smoke_passed",
                {
                    "revision": revision,
                    "artifact_id": artifact_id,
                    "target": "production",
                },
            )
        else:
            self.event(
                events,
                "deployment_not_applicable",
                {
                    "revision": revision,
                    "artifact_id": artifact_id,
                    "package_id": package_id,
                    "reason": "The repository intentionally has no deployment target.",
                },
            )
        self.event(
            events,
            "terminal_ready",
            {
                "controller_state": "terminal_success",
                "candidate_revision": revision,
                "next_action": None,
            },
        )

    def success_events(
        self,
        spec_count: int = 1,
        *,
        include_recovery: bool = False,
        include_blocker: bool = False,
        include_review: bool = False,
        fallback_spec: int | None = None,
        deploy: bool = True,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        self.emit_planning_event(events, spec_count)
        base_revision = "base-0"
        for number in range(1, spec_count + 1):
            self.emit_dispatch(
                events,
                number,
                base_revision,
                fallback=fallback_spec == number,
            )
            if include_recovery and number == 5:
                self.emit_recovery(events, number)
            if include_blocker and number == 12:
                self.emit_blocker_round_trip(events, number)
            if include_review and number == 16:
                self.emit_read_only_review(events, number)
            revision = self.revision(number)
            self.finish_spec(events, number, revision)
            base_revision = revision
            if number % 10 == 0 or number == spec_count:
                self.pass_checkpoint(events, number, revision)
        self.emit_release(events, self.revision(spec_count), deploy=deploy)
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
        implementation_ownership = lifecycle_receipt["implementation_ownership"]
        self.delivery_map_path.write_text(
            "# Delivery map\n\n"
            "Planning, implementation, repair, release, and smoke evidence verified.\n",
            encoding="utf-8",
            newline="\n",
        )
        self.write_json(
            self.task_tree_path,
            {
                "run_id": RUN_ID,
                "tasks": sorted(child_tasks, key=lambda task: task["id"]),
                "implementation_ownership": implementation_ownership,
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
            "implementation_ownership": implementation_ownership,
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
            "repository_sync": {
                "status": "synchronized",
                "repositories": [{"path": "repo-1", "branch": "main", "upstream": "origin/main", "local_head": lifecycle_receipt["candidate_revision"], "remote_head": lifecycle_receipt["candidate_revision"], "evidence": ["receipt://git/sync"]}],
                "evidence": ["receipt://git/sync"],
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
        self.installed_skill_root = self.root / "installed" / "implement-needs"
        shutil.copytree(
            SKILL_ROOT,
            self.installed_skill_root,
            ignore=shutil.ignore_patterns("tests", "__pycache__", "*.pyc"),
        )
        self.planning = self.installed_skill_root / "scripts" / "validate_planning.py"
        self.lifecycle = (
            self.installed_skill_root / "scripts" / "validate_controller_lifecycle.py"
        )
        self.terminal = (
            self.installed_skill_root / "scripts" / "validate_controller_terminal.py"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_validator(self, args: list[str]) -> tuple[dict[str, Any], int]:
        completed = subprocess.run(
            [sys.executable, "-I", *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            cwd=self.root,
        )
        self.assertTrue(
            completed.stdout.strip(),
            f"validator produced no JSON\nstderr:\n{completed.stderr}",
        )
        return json.loads(completed.stdout), completed.returncode

    @staticmethod
    def codes(payload: dict[str, Any]) -> set[str]:
        return {reason["code"] for reason in payload.get("reasons", [])}

    def validate_planning(
        self, spec_count: int, route_specs: list[tuple[int, bool]] | None = None
    ) -> dict[str, Any]:
        self.fixture.write_route_artifacts("planning", "plan-1", spec_count=spec_count)
        route_payload, route_code = self.run_validator(
            [
                str(self.planning),
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
                str(self.planning),
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
        self.assertEqual(
            [self.fixture.spec_id(number) for number in range(1, spec_count + 1)],
            handoff_payload["spec_ids"],
        )

        fallback_specs = {number for number, fallback in route_specs or [] if fallback}
        for number in range(1, spec_count + 1):
            fallback = number in fallback_specs
            target = self.fixture.spec_id(number)
            task_id = self.fixture.task_id(number)
            readback = None
            if fallback:
                readback = self.fixture.readback(
                    target,
                    task_id,
                    requested=self.fixture.pair("gpt-5.6-sol", "xhigh"),
                    selection="fallback",
                    reason="Recommended route became unavailable.",
                )
            self.fixture.write_route_artifacts(
                target,
                task_id,
                spec_count=spec_count,
                readback=readback,
            )
            payload, code = self.run_validator(
                [
                    str(self.planning),
                    "route",
                    "--record",
                    str(self.fixture.record_path),
                    "--readback",
                    str(self.fixture.readback_path),
                    "--expected-run-id",
                    RUN_ID,
                    "--target",
                    target,
                    "--expected-task-id",
                    task_id,
                ]
            )
            self.assertEqual(0, code)
            self.assertEqual(task_id, payload["task_id"])
            self.fixture.route_receipts[target] = payload
        return handoff_payload

    def evaluate_lifecycle(
        self, events: list[dict[str, Any]], expected_state: str
    ) -> tuple[dict[str, Any], int]:
        self.fixture.write_lifecycle_log(events)
        return self.run_validator(
            [
                str(self.lifecycle),
                "--log",
                str(self.fixture.lifecycle_log_path),
                "--expected-run-id",
                RUN_ID,
                "--expected-state",
                expected_state,
                "--receipt",
                str(self.root / "lifecycle-receipt.json"),
            ]
        )

    def evaluate_terminal(
        self, lifecycle_payload: dict[str, Any]
    ) -> tuple[dict[str, Any], int]:
        self.fixture.write_terminal_state(lifecycle_payload)
        return self.run_validator(
            [
                str(self.terminal),
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

    def validate_success_flow(
        self,
        spec_count: int,
        *,
        route_specs: list[tuple[int, bool]] | None = None,
        include_recovery: bool = False,
        include_blocker: bool = False,
        include_review: bool = False,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
        self.validate_planning(spec_count, route_specs=route_specs)
        events = self.fixture.success_events(
            spec_count,
            include_recovery=include_recovery,
            include_blocker=include_blocker,
            include_review=include_review,
            fallback_spec=next(
                (number for number, fallback in route_specs or [] if fallback), None
            ),
        )
        lifecycle_payload, lifecycle_code = self.evaluate_lifecycle(
            events, "terminal_success"
        )
        self.assertEqual(0, lifecycle_code)
        self.assertEqual(
            "terminal_success", lifecycle_payload["derived_controller_state"]
        )
        terminal_payload, terminal_code = self.evaluate_terminal(lifecycle_payload)
        self.assertEqual(0, terminal_code)
        self.assertEqual("allow", terminal_payload["decision"])
        self.assertEqual("terminal_success", terminal_payload["terminal_state"])
        return events, lifecycle_payload, terminal_payload

    def planning_handoff_for_record(
        self, record: dict[str, Any]
    ) -> tuple[dict[str, Any], int]:
        self.fixture.write_json(self.fixture.record_path, record)
        self.fixture.write_json(
            self.fixture.readback_path, self.fixture.readback("planning", "plan-1")
        )
        self.fixture.write_planning_handoff_state()
        return self.run_validator(
            [
                str(self.planning),
                "handoff",
                "--record",
                str(self.fixture.record_path),
                "--controller-state",
                str(self.fixture.planning_state_path),
                "--planning-readback",
                str(self.fixture.readback_path),
                "--expected-run-id",
                RUN_ID,
            ]
        )

    def test_installed_artifact_accepts_locked_route_and_rejects_route_drift(
        self,
    ) -> None:
        self.assertFalse((self.installed_skill_root / "tests").exists())
        self.assertNotEqual(SKILL_ROOT.resolve(), self.installed_skill_root.resolve())
        self.validate_planning(1)

        valid: list[dict[str, Any]] = []
        self.fixture.emit_planning_event(valid, 1)
        self.fixture.emit_dispatch(valid, 1, "base-0")
        payload, code = self.evaluate_lifecycle(valid, "active")
        self.assertEqual(0, code)
        self.assertEqual("allow", payload["decision"])

        outside_policy = copy.deepcopy(valid)
        outside_policy[-1]["data"]["model"] = "gpt-5.6-unknown"
        payload, code = self.evaluate_lifecycle(outside_policy, "active")
        self.assertEqual(1, code)
        self.assertIn("invalid_spec_model_policy", self.codes(payload))
        self.assertEqual("repair_state", payload["next_action"]["kind"])

        unlocked_allowed = copy.deepcopy(valid)
        unlocked_allowed[-1]["data"]["thinking"] = "high"
        unlocked_allowed[-1]["data"]["route_receipt"] = self.fixture.route_receipt(
            "SPEC-1", "task-1", thinking="high"
        )
        payload, code = self.evaluate_lifecycle(unlocked_allowed, "active")
        self.assertEqual(1, code)
        self.assertIn("route_not_locked_recommendation", self.codes(payload))
        self.assertEqual("repair_state", payload["next_action"]["kind"])

    def test_installed_route_gate_rejects_non_string_pair_fields_deterministically(
        self,
    ) -> None:
        cases = (
            ("model", []),
            ("model", {}),
            ("model", 1),
            ("thinking", []),
            ("thinking", {}),
            ("thinking", 1),
        )
        action_kinds = set(
            json.loads(
                (
                    self.installed_skill_root
                    / "references"
                    / "controller-state.schema.json"
                ).read_text(encoding="utf-8")
            )["$defs"]["action"]["properties"]["kind"]["enum"]
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                readback = self.fixture.readback("planning", "plan-1")
                readback["requested"][field] = value
                readback["applied"][field] = copy.deepcopy(value)
                self.fixture.write_route_artifacts(
                    "planning", "plan-1", readback=readback
                )
                receipt_path = self.root / f"{field}-route-receipt.json"
                command = [
                    str(self.planning),
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
                    str(receipt_path),
                ]

                first = self.run_validator(command)
                second = self.run_validator(command)

                self.assertEqual(first, second)
                payload, code = first
                self.assertEqual(1, code)
                self.assertEqual("reject", payload["decision"])
                self.assertEqual("repair", payload["next_action"]["kind"])
                self.assertIn(payload["next_action"]["kind"], action_kinds)
                self.assertEqual(
                    payload,
                    json.loads(receipt_path.read_text(encoding="utf-8")),
                )
                self.assertIn(
                    ("invalid_text", f"$readback.requested.{field}"),
                    {
                        (issue["code"], issue["path"])
                        for issue in payload["reasons"]
                    },
                )
                self.assertIn(
                    ("invalid_text", f"$readback.applied.{field}"),
                    {
                        (issue["code"], issue["path"])
                        for issue in payload["reasons"]
                    },
                )

    def test_malformed_policy_ranks_fail_closed_without_type_error(self) -> None:
        self.validate_planning(1)
        events = self.fixture.success_events(1)
        lifecycle_payload, lifecycle_code = self.evaluate_lifecycle(
            events, "terminal_success"
        )
        self.assertEqual(0, lifecycle_code)
        self.fixture.write_terminal_state(lifecycle_payload)

        policy_path = self.installed_skill_root / "references" / "model-policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["model_rank"]["gpt-5.6-sol"] = "maximum"
        self.fixture.write_json(policy_path, policy)

        commands = (
            (
                [
                    str(self.planning),
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
                ],
                "repair",
            ),
            (
                [
                    str(self.lifecycle),
                    "--log",
                    str(self.fixture.lifecycle_log_path),
                    "--expected-run-id",
                    RUN_ID,
                    "--expected-state",
                    "terminal_success",
                ],
                "repair_state",
            ),
            (
                [
                    str(self.terminal),
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
                ],
                "repair_state",
            ),
        )
        for command, repair_kind in commands:
            with self.subTest(validator=Path(command[0]).name):
                first = self.run_validator(command)
                second = self.run_validator(command)
                self.assertEqual(first, second)
                payload, code = first
                self.assertEqual(1, code)
                self.assertIn("model_policy_invalid", self.codes(payload))
                self.assertEqual(repair_kind, payload["next_action"]["kind"])

    def test_full_32_spec_protocol_trace_covers_release_train_and_ownership(
        self,
    ) -> None:
        events, lifecycle_payload, terminal_payload = self.validate_success_flow(
            32,
            route_specs=[(1, False), (14, True), (32, False)],
            include_recovery=True,
            include_blocker=True,
            include_review=True,
        )
        checkpoints = lifecycle_payload["l4_checkpoints"]["checkpoints"]
        self.assertEqual(
            [10, 20, 30, 32],
            [checkpoint["end_spec_index"] for checkpoint in checkpoints],
        )
        self.assertEqual(4, terminal_payload["l4_checkpoint_count"])
        final_tests = next(
            event for event in events if event["type"] == "final_tests_passed"
        )
        self.assertEqual(
            {
                "mode": "reused_checkpoint",
                "checkpoint_id": "checkpoint-32",
                "candidate_revisions": ["merge-32"],
                "evidence": final_tests["evidence"],
            },
            lifecycle_payload["l4_checkpoints"]["release_l4"],
        )
        self.assertEqual(
            4, sum(event["type"] == "checkpoint_passed" for event in events)
        )

        ownership = lifecycle_payload["implementation_ownership"]
        self.assertTrue(
            all(
                not values
                for values in ownership["ticket_implementation_artifacts"].values()
            )
        )
        spec_7 = next(
            spec for spec in ownership["specs"] if spec["spec_id"] == "SPEC-7"
        )
        self.assertEqual("task-7", spec_7["implementation_task_id"])
        self.assertEqual(
            {"task-7"}, {ticket["owner_task_id"] for ticket in spec_7["tickets"]}
        )
        self.assertEqual(
            {
                "T7-foundation": [],
                "T7-api": ["T7-foundation"],
                "T7-smoke": ["T7-foundation"],
            },
            {ticket["id"]: ticket["blocked_by"] for ticket in spec_7["tickets"]},
        )
        self.assertTrue(
            all(
                ticket["commits"] and ticket["test_evidence"]
                for ticket in spec_7["tickets"]
            )
        )

        spec_14 = next(
            spec for spec in ownership["specs"] if spec["spec_id"] == "SPEC-14"
        )
        self.assertEqual("fallback", spec_14["route"]["selection"])
        self.assertEqual("task-14", spec_14["route"]["task_id"])

        helpers = {
            helper["task_id"]: helper for helper in ownership["role_limited_tasks"]
        }
        self.assertEqual("blocker_repair", helpers["repair-12"]["role"])
        self.assertEqual("task-12", helpers["repair-12"]["parent_task_id"])
        self.assertEqual("read_only_review", helpers["review-16"]["role"])
        self.assertFalse(helpers["review-16"]["writes_product_code"])
        self.assertEqual([], helpers["review-16"]["merge_commits"])

    def test_short_and_exact_ten_trains_reuse_final_checkpoint_without_duplicate_l4(
        self,
    ) -> None:
        for spec_count in (7, 10):
            with self.subTest(spec_count=spec_count):
                events, lifecycle_payload, terminal_payload = (
                    self.validate_success_flow(
                        spec_count,
                        route_specs=[(1, False), (spec_count, False)],
                    )
                )
                checkpoints = lifecycle_payload["l4_checkpoints"]["checkpoints"]
                self.assertEqual(1, len(checkpoints))
                self.assertEqual(spec_count, checkpoints[0]["end_spec_index"])
                self.assertEqual(
                    f"checkpoint-{spec_count}",
                    lifecycle_payload["l4_checkpoints"]["release_l4"]["checkpoint_id"],
                )
                self.assertEqual(
                    "reused_checkpoint",
                    lifecycle_payload["l4_checkpoints"]["release_l4"]["mode"],
                )
                self.assertEqual(1, terminal_payload["l4_checkpoint_count"])
                self.assertEqual(
                    1, sum(event["type"] == "checkpoint_passed" for event in events)
                )

    def test_audit_failure_trace_is_rejected_by_real_lifecycle_gate(self) -> None:
        events: list[dict[str, Any]] = []
        self.fixture.emit_planning_event(events, 1)
        self.fixture.emit_dispatch(events, 1, "base-0")
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
                str(self.lifecycle),
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

    def test_l4_planning_rejects_old_six_schedule_and_tail_omission(self) -> None:
        old_schedule = self.fixture.planning_record(32)
        old_schedule["release_train"]["checkpoint_size"] = 6
        old_schedule["release_train"]["checkpoints"] = [
            {
                "id": f"checkpoint-{position}",
                "start_spec_index": position - 5,
                "end_spec_index": position,
                "specs": [
                    self.fixture.spec_id(number)
                    for number in range(position - 5, position + 1)
                ],
                "final_tail": False,
                **self.fixture.checkpoint_surfaces(position),
            }
            for position in (6, 12, 18, 24, 30)
        ]
        payload, code = self.planning_handoff_for_record(old_schedule)
        self.assertEqual(1, code)
        self.assertIn("checkpoint_size_mismatch", self.codes(payload))
        self.assertIn("checkpoint_mismatch", self.codes(payload))

        omitted_tail = self.fixture.planning_record(32)
        omitted_tail["release_train"]["checkpoints"].pop()
        payload, code = self.planning_handoff_for_record(omitted_tail)
        self.assertEqual(1, code)
        self.assertIn("checkpoint_mismatch", self.codes(payload))

    def test_lifecycle_rejects_due_failed_stale_and_overbroad_l4(self) -> None:
        due_events: list[dict[str, Any]] = []
        self.fixture.emit_planning_event(due_events, 11)
        base = "base-0"
        for number in range(1, 11):
            self.fixture.emit_dispatch(due_events, number, base)
            revision = self.fixture.revision(number)
            self.fixture.finish_spec(due_events, number, revision)
            base = revision
        self.fixture.emit_dispatch(due_events, 11, "merge-10")
        payload, code = self.evaluate_lifecycle(due_events, "active")
        self.assertEqual(1, code)
        self.assertIn("checkpoint_blocks_next_segment", self.codes(payload))

        failed_events = copy.deepcopy(due_events[:-1])
        self.fixture.event(
            failed_events,
            "checkpoint_failed",
            {
                "checkpoint_id": "checkpoint-10",
                "revision": "merge-10",
                "candidate_revisions": ["merge-10"],
                **self.fixture.checkpoint_surfaces(10),
                "reason": "Owner regression failed.",
            },
        )
        self.fixture.emit_dispatch(failed_events, 11, "merge-10")
        payload, code = self.evaluate_lifecycle(failed_events, "active")
        self.assertEqual(1, code)
        self.assertIn("checkpoint_blocks_next_segment", self.codes(payload))

        stale_reuse = self.fixture.success_events(7)
        final_tests = next(
            event for event in stale_reuse if event["type"] == "final_tests_passed"
        )
        final_tests["data"]["candidate_revisions"] = ["merge-7", "repo-2@later"]
        payload, code = self.evaluate_lifecycle(stale_reuse, "terminal_success")
        self.assertEqual(1, code)
        self.assertIn("stale_final_checkpoint_revisions", self.codes(payload))

        overbroad = self.fixture.success_events(7)
        checkpoint = next(
            event for event in overbroad if event["type"] == "checkpoint_passed"
        )
        checkpoint["data"]["affected_repositories"] = ["repo-1", "repo-unrelated"]
        payload, code = self.evaluate_lifecycle(overbroad, "terminal_success")
        self.assertEqual(1, code)
        self.assertIn("checkpoint_repository_scope_mismatch", self.codes(payload))

    def test_lifecycle_rejects_ticket_thread_and_owner_topology_regressions(
        self,
    ) -> None:
        ticket_thread = self.fixture.success_events(1)
        events = ticket_thread[:2]
        self.fixture.event(
            events,
            "ticket_implementation_artifact",
            {
                "artifact_type": "thread",
                "id": "ticket-thread-1",
                "spec_id": "SPEC-1",
                "ticket_id": "T1",
            },
        )
        payload, code = self.evaluate_lifecycle(events, "active")
        self.assertEqual(1, code)
        self.assertIn("ticket_implementation_artifact_present", self.codes(payload))

        replacement_owner: list[dict[str, Any]] = []
        self.fixture.emit_planning_event(replacement_owner, 1)
        self.fixture.emit_dispatch(replacement_owner, 1, "base-0")
        self.fixture.event(
            replacement_owner,
            "spec_dispatched",
            {
                "task_id": "task-1-escalated",
                "spec_id": "SPEC-1",
                "base_revision": "base-0",
                "route_selection": "fallback",
                "model": "gpt-5.6-sol",
                "thinking": "xhigh",
                "route_receipt": self.fixture.route_receipt(
                    "SPEC-1",
                    "task-1-escalated",
                    selection="fallback",
                    model="gpt-5.6-sol",
                ),
            },
        )
        payload, code = self.evaluate_lifecycle(replacement_owner, "active")
        self.assertEqual(1, code)
        self.assertIn("overlapping_spec", self.codes(payload))
        self.assertIn("duplicate_spec_task", self.codes(payload))

        owner_mismatch = self.fixture.success_events(1)
        ticket = next(
            event for event in owner_mismatch if event["type"] == "ticket_evidence"
        )
        ticket["data"]["owner_task_id"] = "ticket-task-1"
        payload, code = self.evaluate_lifecycle(owner_mismatch, "terminal_success")
        self.assertEqual(1, code)
        self.assertIn("ticket_owner_mismatch", self.codes(payload))

    def test_lifecycle_rejects_repair_or_reviewer_ownership_breach(self) -> None:
        repair_takeover = self.fixture.success_events(12, include_blocker=True)
        takeover_ticket = next(
            event
            for event in repair_takeover
            if event["type"] == "ticket_evidence"
            and event["data"]["spec_id"] == "SPEC-12"
        )
        takeover_ticket["data"]["owner_task_id"] = "repair-12"
        takeover_ticket["actor"] = "repair_child"
        payload, code = self.evaluate_lifecycle(repair_takeover, "terminal_success")
        self.assertEqual(1, code)
        self.assertIn("actor_mismatch", self.codes(payload))
        self.assertIn("ticket_owner_mismatch", self.codes(payload))

        reviewer_commit = self.fixture.success_events(16, include_review=True)
        review = next(
            event for event in reviewer_commit if event["type"] == "role_limited_task"
        )
        review["data"]["writes_product_code"] = True
        review["data"]["merge_commits"] = ["merge-review"]
        payload, code = self.evaluate_lifecycle(reviewer_commit, "terminal_success")
        self.assertEqual(1, code)
        self.assertIn("role_limited_task_mutated_product", self.codes(payload))

    def test_terminal_counterexamples_reject_machine_state_regressions(self) -> None:
        _, lifecycle_payload, _ = self.validate_success_flow(10)
        self.fixture.write_terminal_state(lifecycle_payload)
        state = json.loads(self.fixture.terminal_state_path.read_text(encoding="utf-8"))
        state["test_state"]["l4_checkpoints"]["release_l4"]["candidate_revisions"] = [
            "merge-10",
            "repo-2@later",
        ]
        self.fixture.write_json(self.fixture.terminal_state_path, state)
        payload, code = self.run_validator(
            [
                str(self.terminal),
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
            ]
        )
        self.assertEqual(1, code)
        self.assertIn("stale_final_checkpoint_revisions", self.codes(payload))

        self.fixture.write_terminal_state(lifecycle_payload)
        state = json.loads(self.fixture.terminal_state_path.read_text(encoding="utf-8"))
        state["implementation_ownership"]["ticket_implementation_artifacts"][
            "tasks"
        ].append("ticket-task-1")
        self.fixture.write_json(self.fixture.terminal_state_path, state)
        payload, code = self.run_validator(
            [
                str(self.terminal),
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
            ]
        )
        self.assertEqual(1, code)
        self.assertIn("ticket_implementation_artifact_present", self.codes(payload))

    def test_second_terminal_event_is_rejected(self) -> None:
        events = self.fixture.success_events()
        duplicate = copy.deepcopy(events[-1])
        duplicate["sequence"] = len(events) + 1
        duplicate["evidence"] = ["evidence://duplicate-terminal"]
        events.append(duplicate)
        self.fixture.write_lifecycle_log(events)
        payload, code = self.run_validator(
            [
                str(self.lifecycle),
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
