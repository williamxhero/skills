from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import sqlite3
from pathlib import Path

from spec_runner.errors import RunnerError

from spec_runner.delivery import git_sha, merge_local, prepare_workspace, verify_candidate
from spec_runner.diagnostics import validate_fault_matrix, validate_release_report
from spec_runner.matt import resolve_grill
from spec_runner.plans import validate_spec_plan, validate_ticket_plan
from spec_runner.takeover import completion_action, inspect_takeover, plan_frontier, write_takeover_record
from spec_runner.legacy import legacy_takeover_inventory, read_legacy_database


class ProductBoundaryTests(unittest.TestCase):
    def test_plans_are_independent_and_validate_coverage_and_base(self):
        spec = validate_spec_plan({
            "schema_version": "spec-runner-spec-plan/v1",
            "requirements": ["R1", "R2"],
            "specs": [
                {"key": "SR-01", "title": "One", "body": "body", "covers": ["R1", "R2"], "blocked_by": [], "route": {"model": "m", "effort": "high", "reason": "fit"}}
            ],
        })
        self.assertTrue(spec["digest"])
        ticket = validate_ticket_plan({
            "schema_version": "spec-runner-ticket-plan/v1", "spec_key": "SR-01", "base_sha": "abcdef0",
            "tickets": [{"key": "SR-01.1", "body": "implement", "acceptance": ["A1"], "blocked_by": []}],
        }, expected_spec_key="SR-01", expected_base_sha="abcdef0")
        self.assertTrue(ticket["digest"])

    def test_grill_never_invents_external_facts(self):
        result = resolve_grill(requirement_digest="r", questions=[
            {"id": "Q1", "question": "production permission?", "requires_external_fact": True, "recommended": "yes"},
            {"id": "Q2", "question": "format", "requires_external_fact": False, "recommended": "json"},
        ], decisions={}, authorization={"Q2"})
        self.assertEqual(result["state"], "needs_input")
        self.assertEqual(result["decisions"][0]["source"], "standing_authorization")

    def test_candidate_receipt_is_bound_to_real_git_sha(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Spec Runner Test"], cwd=repo, check=True)
            (repo / "test.py").write_text("print('ok')\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "test"], cwd=repo, check=True)
            sha = git_sha(repo)
            receipt = verify_candidate(workspace=repo, candidate_sha=sha, acceptance_version="a1", checks=[{"command": ["python", "test.py"], "acceptance": ["A1"]}], acceptance=["A1"])
            self.assertEqual(receipt["candidate_sha"], sha)

    def test_workspace_and_guarded_local_merge_preserve_main_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Spec Runner Test"], cwd=repo, check=True)
            (repo / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            workspace_info = prepare_workspace(repository=repo, workspace_root=Path(temp) / "workspaces", run_id="12345678-1234-1234-1234-123456789012", spec_key="SR-01", base_ref="refs/heads/main")
            workspace = Path(workspace_info["workspace"])
            (workspace / "README.md").write_text("candidate\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True)
            subprocess.run(["git", "commit", "-qm", "candidate"], cwd=workspace, check=True)
            candidate_sha = git_sha(workspace)
            merged = merge_local(repository=repo, candidate_branch=str(workspace_info["branch"]), target_ref="refs/heads/main", expected_target_sha=workspace_info["base_sha"], workspace_root=Path(temp) / "workspaces", run_id="12345678-1234-1234-1234-123456789012")
            self.assertEqual(merged["tested_head"], candidate_sha)
            self.assertEqual((repo / "README.md").read_text(encoding="utf-8"), "base\n")
            self.assertEqual(git_sha(repo, "refs/heads/main"), merged["merge_sha"])

    def test_takeover_distinguishes_cleanup_from_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Spec Runner Test"], cwd=repo, check=True)
            (repo / "x").write_text("x", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
            report = inspect_takeover({"schema_version": "spec-runner-takeover-input/v1", "repository_path": str(repo), "source_threads": [], "artifacts": [], "facts": {"merged": True, "verification_receipt": {"candidate_sha": "abc"}}})
            self.assertEqual(completion_action(report)["state"], "cleanup_pending")
            frontier = plan_frontier(report)
            self.assertEqual(frontier["state"], "planned")
            record = write_takeover_record(control_root=Path(temp) / "control", takeover_key="takeover-1", report=report, frontier=frontier)
            self.assertTrue(record["created"])
            self.assertTrue((Path(temp) / "control" / "spec-runner.sqlite3").is_file())
            self.assertFalse(write_takeover_record(control_root=Path(temp) / "control", takeover_key="takeover-1", report=report, frontier=frontier)["created"])

    def test_takeover_frontier_keeps_missing_scope_as_needs_input(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            report = inspect_takeover({"schema_version": "spec-runner-takeover-input/v1", "repository_path": str(repo), "source_threads": [], "artifacts": [], "facts": {"partial_code": True}})
            frontier = plan_frontier(report)
            self.assertEqual(frontier["state"], "needs_input")
            self.assertTrue(any(step["target"] == "requirement_scope" for step in frontier["steps"]))

    def test_takeover_wait_policy_does_not_start_a_competing_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            inventory = {
                "schema_version": "spec-runner-takeover-input/v1",
                "repository_path": str(repo),
                "handover_policy": "wait_then_takeover",
                "source_threads": [{"id": "source-1", "ownership": "confirmed", "active": True, "stop_confirmed": False}],
                "artifacts": [],
                "facts": {"requirements": ["R1"]},
            }
            report = inspect_takeover(inventory)
            self.assertEqual(report["next_state"], "waiting_handover")
            self.assertEqual(completion_action(report)["state"], "waiting_handover")
            frontier = plan_frontier(report)
            self.assertEqual(frontier["state"], "waiting_handover")

            inventory["handover_policy"] = "interrupt_then_takeover"
            blocked = inspect_takeover(inventory)
            self.assertEqual(blocked["next_state"], "blocked")
            self.assertEqual(completion_action(blocked)["state"], "blocked")

    def test_takeover_frontier_preserves_mixed_spec_progress(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            report = inspect_takeover({
                "schema_version": "spec-runner-takeover-input/v1",
                "repository_path": str(repo),
                "source_threads": [],
                "artifacts": [],
                "facts": {
                    "requirements": ["R1"],
                    "tracker": True,
                    "specs": [
                        {"key": "SR-01", "state": "completed", "verified": True},
                        {"key": "SR-02", "state": "partial", "verified": False},
                        {"key": "SR-03", "state": "not_started"},
                    ],
                },
            })
            frontier = plan_frontier(report)
            self.assertEqual(frontier["categories"]["adopted"], ["SR-01", "SR-02"])
            self.assertEqual(frontier["categories"]["reverified"], ["SR-02"])
            self.assertEqual(frontier["categories"]["new_work"], ["SR-03"])
            self.assertEqual([step["target"] for step in frontier["steps"]], ["SR-01", "SR-02", "SR-03"])

    def test_release_and_fault_reports_reject_unverified_shape(self):
        fault = validate_fault_matrix({"schema_version": "spec-runner-fault-matrix/v1", "scenarios": [{"id": "s1", "entrypoint": "public_cli", "expected": {"state": "blocked"}, "evidence_kind": "deterministic"}]})
        self.assertEqual(fault["outcome"], "validated")
        body = {"evidence_kind": "deterministic", "verified": True, "report": "fixture"}
        from spec_runner.plans import digest

        release = validate_release_report({
            "schema_version": "spec-runner-release-report/v1",
            "runner_version": "0.1.0",
            "subject": {"runner_version": "0.1.0", "build_digest": "build", "config_contract": "spec-runner-config/v1"},
            "required_kinds": ["deterministic"],
            "evidence": [{"kind": "deterministic", "body": body, "body_digest": digest(body), "outcome": "passed"}],
        }, expected_runner_version="0.1.0")
        self.assertTrue(release["eligible"])

        with self.assertRaisesRegex(RunnerError, "digest"):
            validate_release_report({
                "schema_version": "spec-runner-release-report/v1",
                "runner_version": "0.1.0",
                "subject": {"runner_version": "0.1.0", "build_digest": "build", "config_contract": "spec-runner-config/v1"},
                "required_kinds": ["deterministic"],
                "evidence": [{"kind": "deterministic", "body": body, "body_digest": "forged", "outcome": "passed"}],
            }, expected_runner_version="0.1.0")

    def test_legacy_database_is_observed_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE old_runs (id TEXT)")
            connection.execute("INSERT INTO old_runs VALUES ('historic')")
            connection.commit()
            before = path.stat().st_mtime_ns
            connection.close()
            report = read_legacy_database(path)
            self.assertTrue(report["read_only"])
            self.assertEqual(report["tables"]["old_runs"][0]["id"], "historic")
            self.assertEqual(path.stat().st_mtime_ns, before)

    def test_legacy_observation_maps_to_common_takeover_input_without_mutating_db(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = root / "repo"
            repository.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
            path = root / "legacy.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE old_runs (id TEXT)")
            connection.execute("INSERT INTO old_runs VALUES ('historic')")
            connection.commit()
            connection.close()
            before = path.read_bytes()
            inventory = legacy_takeover_inventory(database=path, repository=repository)
            self.assertEqual(inventory["schema_version"], "spec-runner-takeover-input/v1")
            self.assertTrue(inventory["facts"]["legacy_database"]["historical_only"])
            self.assertEqual(inventory["source_threads"], [])
            self.assertEqual(path.read_bytes(), before)
