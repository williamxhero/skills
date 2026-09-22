from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import sqlite3
import zipfile
from unittest.mock import patch
from pathlib import Path

from spec_runner.errors import RunnerError

from spec_runner.delivery import cleanup_managed_workspace, git_sha, merge_local, prepare_workspace, verify_candidate
from spec_runner.diagnostics import build_release_report, inspect_wheel, runtime_report, validate_fault_matrix, validate_release_report
from spec_runner.matt import resolve_grill
from spec_runner.plans import validate_spec_plan, validate_ticket_plan
from spec_runner.takeover import completion_action, inspect_takeover, plan_frontier, write_takeover_record
from spec_runner.legacy import legacy_takeover_inventory, read_legacy_database
from spec_runner.multi_spec import run_local_delivery


class ProductBoundaryTests(unittest.TestCase):
    def test_three_spec_local_delivery_uses_real_merge_chain_and_is_replayable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Spec Runner Test"], cwd=repo, check=True)
            (repo / "state.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            control = root / "control"
            plan = {
                "schema_version": "spec-runner-delivery-plan/v1",
                "specs": [],
            }
            for index, key in enumerate(("SR-01", "SR-02", "SR-03"), 1):
                review = control / f"reviews/{key}.json"
                review.parent.mkdir(parents=True, exist_ok=True)
                plan["specs"].append({
                    "key": key,
                    "blocked_by": [] if index == 1 else [f"SR-0{index - 1}"],
                    "acceptance_version": "a1",
                    "acceptance": ["A1"],
                    "implementation": [["python", "-c", f"from pathlib import Path; Path('state.txt').write_text(Path('state.txt').read_text() + '{key}\\n')"]],
                    "checks": [{"command": ["python", "-c", "from pathlib import Path; assert Path('state.txt').is_file()"], "acceptance": ["A1"]}],
                    "review_file": f"reviews/{key}.json",
                })
                review.write_text(json.dumps({"schema_version": "spec-runner-review-result/v1", "candidate_sha": "placeholder", "acceptance_version": "a1", "findings": []}), encoding="utf-8")

            # The trusted review fixture writes a receipt only after its
            # candidate commit exists, binding the actual SHA.
            for spec in plan["specs"]:
                review_path = str((control / spec["review_file"]).resolve())
                spec["implementation"] = [["python", "-c", f"from pathlib import Path; p=Path('state.txt'); p.write_text(p.read_text() + '{spec['key']}\\n'); import subprocess,json; subprocess.run(['git','add','state.txt'],check=True); subprocess.run(['git','commit','-qm','{spec['key']}'],check=True); sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(); Path(r'{review_path}').write_text(json.dumps({{'schema_version':'spec-runner-review-result/v1','candidate_sha':sha,'acceptance_version':'a1','findings':[]}}))"]]
            result = run_local_delivery(plan=plan, repository=repo, workspace_root=root / "workspaces", control_root=control, run_id="delivery-run", target_ref="refs/heads/main")
            self.assertEqual(result["state"], "completed")
            self.assertEqual(result["completed_specs"], ["SR-01", "SR-02", "SR-03"])
            self.assertIn("SR-03", result["specs"])
            target_contents = subprocess.check_output(["git", "-C", str(repo), "show", "refs/heads/main:state.txt"], text=True)
            self.assertEqual(target_contents, "base\nSR-01\nSR-02\nSR-03\n")
            self.assertTrue(all(item["cleanup"]["outcome"] == "cleaned" for item in result["specs"].values()))
            self.assertEqual(list((root / "workspaces").glob("*.manifest.json")), [])
            receipt_path = control / "delivery/delivery-run/delivery-receipt.json"
            interrupted = json.loads(receipt_path.read_text(encoding="utf-8"))
            interrupted["state"] = "running"
            interrupted["specs"]["SR-03"]["state"] = "verified_candidate"
            interrupted["specs"]["SR-03"].pop("merge", None)
            receipt_path.write_text(json.dumps(interrupted), encoding="utf-8")
            replay = run_local_delivery(plan=plan, repository=repo, workspace_root=root / "workspaces", control_root=control, run_id="delivery-run", target_ref="refs/heads/main")
            self.assertEqual(replay["completed_specs"], result["completed_specs"])
            self.assertEqual(replay["specs"]["SR-03"]["merge"]["outcome"], "reconciled")
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
            self.assertEqual(merged["cleanup"]["outcome"], "cleaned")

    def test_managed_workspace_cleanup_exposes_windows_lock_as_pending(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = root / "repo"
            repository.mkdir()
            workspace_root = root / "workspaces"
            workspace = workspace_root / "SR-01-run"
            workspace.mkdir(parents=True)
            manifest = workspace_root / "SR-01-run.manifest.json"
            manifest.write_text(json.dumps({"workspace": str(workspace), "repository": str(repository)}), encoding="utf-8")

            def locked_remove(**kwargs: object) -> None:
                raise RunnerError("workspace_cleanup_timeout", "simulated Windows file lock")

            with patch("spec_runner.delivery._remove_managed_worktree", side_effect=locked_remove):
                result = cleanup_managed_workspace(
                    repository=repository,
                    workspace_root=workspace_root,
                    workspace=workspace,
                    manifest=manifest,
                )
            self.assertEqual(result["outcome"], "pending")
            self.assertEqual(result["reason"], "worktree_remove_failed")
            self.assertTrue(workspace.exists())
            self.assertTrue(manifest.exists())

    def test_managed_workspace_cleanup_refuses_unowned_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = root / "repo"
            repository.mkdir()
            workspace_root = root / "workspaces"
            workspace = workspace_root / "SR-01-run"
            workspace.mkdir(parents=True)
            manifest = workspace_root / "SR-01-run.manifest.json"
            manifest.write_text(json.dumps({"workspace": str(workspace), "repository": str(root / "other")}), encoding="utf-8")
            result = cleanup_managed_workspace(
                repository=repository,
                workspace_root=workspace_root,
                workspace=workspace,
                manifest=manifest,
            )
            self.assertEqual(result["outcome"], "pending")
            self.assertEqual(result["reason"], "manifest_ownership_mismatch")
            self.assertTrue(workspace.exists())

    def test_delivery_retries_persisted_cleanup_pending_without_reimplementing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Spec Runner Test"], cwd=repo, check=True)
            (repo / "state.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
            control = root / "control"
            review_path = control / "review.json"
            review_path.parent.mkdir(parents=True)
            plan = {
                "schema_version": "spec-runner-delivery-plan/v1",
                "specs": [{
                    "key": "SR-01",
                    "blocked_by": [],
                    "acceptance_version": "a1",
                    "acceptance": ["A1"],
                    "implementation": [["python", "-c", f"from pathlib import Path; p=Path('state.txt'); p.write_text(p.read_text()+'done\\n'); import subprocess,json; subprocess.run(['git','add','state.txt'],check=True); subprocess.run(['git','commit','-qm','done'],check=True); sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(); Path(r'{review_path}').write_text(json.dumps({{'schema_version':'spec-runner-review-result/v1','candidate_sha':sha,'acceptance_version':'a1','findings':[]}}))"]],
                    "checks": [{"command": ["python", "-c", "from pathlib import Path; assert Path('state.txt').read_text().endswith('done\\n')"], "acceptance": ["A1"]}],
                    "review_file": "review.json",
                }],
            }
            real_cleanup = cleanup_managed_workspace
            calls = {"count": 0}

            def pending_once(**kwargs: object) -> dict[str, object]:
                calls["count"] += 1
                if calls["count"] == 1:
                    return {"outcome": "pending", "reason": "worktree_remove_failed"}
                return real_cleanup(**kwargs)  # type: ignore[arg-type]

            with patch("spec_runner.multi_spec.cleanup_managed_workspace", side_effect=pending_once):
                first = run_local_delivery(plan=plan, repository=repo, workspace_root=root / "workspaces", control_root=control, run_id="cleanup-run", target_ref="refs/heads/main")
            self.assertEqual(first["state"], "cleanup_pending")
            self.assertEqual(first["specs"]["SR-01"]["state"], "cleanup_pending")
            second = run_local_delivery(plan=plan, repository=repo, workspace_root=root / "workspaces", control_root=control, run_id="cleanup-run", target_ref="refs/heads/main")
            self.assertEqual(second["state"], "completed")
            self.assertEqual(second["specs"]["SR-01"]["cleanup"]["outcome"], "cleaned")
            self.assertEqual(calls["count"], 1)

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

    def test_release_builder_and_wheel_inspector_use_real_bodies(self):
        body = {"evidence_kind": "deterministic", "verified": True, "outcome": "passed", "cases": ["normal"]}
        report = build_release_report(
            runner_version="0.1.0",
            subject={"runner_version": "0.1.0", "build_digest": "build", "config_contract": "spec-runner-config/v1"},
            evidence_documents=[body, {"evidence_kind": "local_git", "verified": True, "outcome": "passed", "merge_sha": "abc"}],
        )
        self.assertTrue(report["report_digest"])
        with tempfile.TemporaryDirectory() as temp:
            wheel = Path(temp) / "spec_runner-0.1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("spec_runner/cli.py", "")
                archive.writestr("spec_runner/dependencies.lock.json", "{}")
                archive.writestr("spec_runner-0.1.0.dist-info/METADATA", "Version: 0.1.0\n")
            receipt = inspect_wheel(wheel, expected_runner_version="0.1.0")
            self.assertEqual(receipt["outcome"], "verified")

    def test_runtime_report_counts_states_without_treating_telemetry_as_progress(self):
        report = runtime_report(runner_version="0.1.0", store_status={"runs": [{"state": "completed"}, {"state": "paused"}, {"state": "paused"}]})
        self.assertEqual(report["states"], {"completed": 1, "paused": 2})
        self.assertEqual(report["progress_basis"], "verified_step_events_and_receipts")
        self.assertFalse(report["telemetry_is_business_progress"])

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
