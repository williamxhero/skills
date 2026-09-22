from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import sqlite3
from pathlib import Path

from spec_runner.delivery import git_sha, prepare_workspace, verify_candidate
from spec_runner.diagnostics import validate_fault_matrix, validate_release_report
from spec_runner.matt import resolve_grill
from spec_runner.plans import validate_spec_plan, validate_ticket_plan
from spec_runner.takeover import completion_action, inspect_takeover
from spec_runner.legacy import read_legacy_database


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

    def test_release_and_fault_reports_reject_unverified_shape(self):
        fault = validate_fault_matrix({"schema_version": "spec-runner-fault-matrix/v1", "scenarios": [{"id": "s1", "entrypoint": "public_cli", "expected": {"state": "blocked"}, "evidence_kind": "deterministic"}]})
        self.assertEqual(fault["outcome"], "validated")
        release = validate_release_report({"schema_version": "spec-runner-release-report/v1", "runner_version": "0.1.0", "required_kinds": ["deterministic"], "evidence": [{"kind": "deterministic", "body_digest": "d", "outcome": "passed"}]}, expected_runner_version="0.1.0")
        self.assertTrue(release["eligible"])

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
