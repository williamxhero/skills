from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from validation.qualification import EXPECTED_COUNTS, QUALIFIED, REJECTED, digest_tree, verify_report

CLEANUP_MODULE_SPEC = importlib.util.spec_from_file_location(
    "cleanup_qualification", ROOT / "validation/scripts/cleanup_qualification.py"
)
assert CLEANUP_MODULE_SPEC and CLEANUP_MODULE_SPEC.loader
cleanup_qualification = importlib.util.module_from_spec(CLEANUP_MODULE_SPEC)
CLEANUP_MODULE_SPEC.loader.exec_module(cleanup_qualification)

REGISTER_MODULE_SPEC = importlib.util.spec_from_file_location(
    "register_qualification", ROOT / "validation/scripts/register_qualification.py"
)
assert REGISTER_MODULE_SPEC and REGISTER_MODULE_SPEC.loader
register_qualification = importlib.util.module_from_spec(REGISTER_MODULE_SPEC)
REGISTER_MODULE_SPEC.loader.exec_module(register_qualification)


class QualificationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = json.loads((ROOT / "validation/scenarios/whole-spec-v1.json").read_text(encoding="utf-8"))

    def valid_report(self) -> dict:
        return {
            "run_id": "qualification-test", "scenario_version": "whole-spec-v1",
            "skill_digest": "a" * 64, "harness_digest": "b" * 64,
            "backend_kind": "thread", "backend_contract_version": 1,
            "project_identity_receipt": {"project_id": "project-1", "canonical_path": "C:/skills"},
            "logical_id_to_external_id_map": {f"item-{index}": f"external-{index}" for index in range(20)},
            "artifact_counts": EXPECTED_COUNTS,
            "recovery_results": {key: {"detected": True, "executed": True} for key in ("planning_restart", "ticket_restart", "assignment_restart", "merge_before_archive_restart", "release_restart", "lost_response", "idempotency_retry", "controller_interrupted", "capacity_fallback", "stream_disconnect", "uncertain_side_effect")},
            "recovery_evidence": {"detection": ["recovery://detected"], "execution": ["recovery://executed"]},
            "final_frontier": {"successor_reached": True, "spec_id": "SPEC-3"},
            "release_train_receipts": {level: "passed" for level in ("L0", "L1", "L2", "L3", "L4", "L5")},
            "backend_capability_receipt": {
                "capabilities": ["create_thread", "list_tasks", "read_thread", "read_applied_route", "send_message_to_thread", "set_thread_archived", "read_archive_state"],
                "probe_target": {"formal_thread_id": "probe-thread", "host_id": "local"},
                "identity_readback": {"formal_thread_id": "probe-thread", "host_id": "local", "task_id": "probe-task", "run_id": "qualification-test", "attempt_id": "01", "owner_id": "owner", "project_id": "project-1", "cwd": "C:/skills"},
            },
            "task_census": {
                "all_archived": True, "no_orphans": True,
                "tasks": [
                    {"kind": kind, "task_id": f"task-{index}", "run_id": "qualification-test", "attempt_id": "01", "owner_id": "owner", "formal_thread_id": f"thread-{index}", "host_id": "local", "project_id": "project-1", "cwd": "C:/skills"}
                    for index, kind in enumerate(("grill", "planning", "spec", "spec", "spec"), 1)
                ],
            },
            "repository_sync_receipt": {"local_remote_head_equal": True},
            "cleanup_receipt": {"complete": True, "receipts": [{"archive_readback": {"archived": True}, "registry_transition": "archived"}]},
            "route_visibility_receipt": {"planned": {"model": "gpt-5.6-sol", "effort": "high"}, "applied": {"model": "gpt-5.6-sol", "effort": "high"}, "executed": {"turn_id": "turn-1"}, "identity_consistent": True},
            "controller_lifecycle_receipt": {"continuation_persisted": True, "watchdog_tested": True, "cleanup_readback_verified": True},
        }

    def test_complete_external_evidence_qualifies(self) -> None:
        result = verify_report(self.valid_report(), self.scenario)
        self.assertEqual(QUALIFIED, result["decision"])

    def test_missing_project_id_rejects_before_any_claim_of_success(self) -> None:
        report = self.valid_report()
        del report["project_identity_receipt"]["project_id"]
        result = verify_report(report, self.scenario)
        self.assertEqual(REJECTED, result["decision"])
        self.assertIn("identity_project_id_missing", result["reasons"])

    def test_request_id_cannot_replace_task_identity(self) -> None:
        report = self.valid_report()
        report["project_identity_receipt"]["request_id"] = "request-123"
        result = verify_report(report, self.scenario)
        self.assertEqual(REJECTED, result["decision"])
        self.assertIn("identity_request_id_forbidden", result["reasons"])

    def test_ticket_worker_or_unarchived_task_rejects(self) -> None:
        report = self.valid_report()
        report["artifact_counts"] = {**EXPECTED_COUNTS, "ticket_implementation_artifacts": 1}
        report["task_census"]["all_archived"] = False
        result = verify_report(report, self.scenario)
        self.assertEqual(REJECTED, result["decision"])
        self.assertIn("artifact_counts_mismatch", result["reasons"])
        self.assertIn("task_census_incomplete", result["reasons"])

    def test_rejected_backend_receipt_cannot_qualify(self) -> None:
        report = self.valid_report()
        report["backend_capability_receipt"]["decision"] = "reject"
        report["backend_capability_receipt"]["archive_error"] = "no rollout found"
        result = verify_report(report, self.scenario)
        self.assertEqual(REJECTED, result["decision"])
        self.assertIn("backend_probe_rejected", result["reasons"])
        self.assertIn("backend_archive_failed", result["reasons"])

    def test_route_mismatch_and_required_archive_reject_preflight_receipt(self) -> None:
        report = self.valid_report()
        backend = report["backend_capability_receipt"]
        backend["route_readback"] = {"model": "gpt-5.6-sol", "effort": "medium"}
        backend["expected_route"] = {"model": "gpt-5.6-sol", "effort": "high"}
        backend["archive_readback"] = {"required": True, "archived": False}
        result = verify_report(report, self.scenario)
        self.assertEqual(REJECTED, result["decision"])
        self.assertIn("backend_route_mismatch", result["reasons"])
        self.assertIn("backend_archive_incomplete", result["reasons"])

    def test_missing_turn_execution_evidence_is_not_reported_as_available(self) -> None:
        report = self.valid_report()
        report["route_visibility_receipt"]["executed"] = {}
        result = verify_report(report, self.scenario)
        self.assertIn("route_executed_evidence_unavailable", result["reasons"])

    def test_recovery_detection_without_execution_cannot_qualify(self) -> None:
        report = self.valid_report()
        report["recovery_results"]["controller_interrupted"] = {"detected": True, "executed": False}
        result = verify_report(report, self.scenario)
        self.assertEqual(REJECTED, result["decision"])
        self.assertIn("recovery_controller_interrupted_execution_missing", result["reasons"])

    def test_legacy_passed_string_cannot_replace_recovery_execution_evidence(self) -> None:
        report = self.valid_report()
        report["recovery_results"]["controller_interrupted"] = "passed"
        result = verify_report(report, self.scenario)
        self.assertEqual(REJECTED, result["decision"])
        self.assertIn("recovery_controller_interrupted_evidence_missing", result["reasons"])

    def test_duplicate_formal_task_identity_is_rejected(self) -> None:
        report = self.valid_report()
        report["task_census"]["tasks"][1]["formal_thread_id"] = report["task_census"]["tasks"][0]["formal_thread_id"]
        result = verify_report(report, self.scenario)
        self.assertEqual(REJECTED, result["decision"])
        self.assertIn("duplicate_task_identity", result["reasons"])

    def test_digest_excludes_report_output_but_not_subject_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "subject.txt").write_text("one", encoding="utf-8")
            reports = root / "reports"; reports.mkdir()
            before = digest_tree(root, exclude=(reports,))
            (reports / "run.json").write_text("volatile", encoding="utf-8")
            self.assertEqual(before, digest_tree(root, exclude=(reports,)))
            (root / "subject.txt").write_text("two", encoding="utf-8")
            self.assertNotEqual(before, digest_tree(root, exclude=(reports,)))

    def test_cli_verifier_returns_nonzero_for_incomplete_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report_path = Path(temporary) / "report.json"
            report = self.valid_report(); report["cleanup_receipt"] = {"complete": False}
            report_path.write_text(json.dumps(report), encoding="utf-8")
            completed = subprocess.run([
                sys.executable, str(ROOT / "validation/scripts/verify_qualification.py"),
                "--scenario", str(ROOT / "validation/scenarios/whole-spec-v1.json"), "--report", str(report_path),
            ], text=True, capture_output=True, check=False)
            self.assertEqual(2, completed.returncode, completed.stdout + completed.stderr)

    def test_cli_verifier_returns_zero_for_complete_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report_path = Path(temporary) / "report.json"
            report_path.write_text(json.dumps(self.valid_report()), encoding="utf-8")
            completed = subprocess.run([
                sys.executable, str(ROOT / "validation/scripts/verify_qualification.py"),
                "--scenario", str(ROOT / "validation/scenarios/whole-spec-v1.json"), "--report", str(report_path),
            ], text=True, capture_output=True, check=False)
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)

    def test_qualification_gate_blocks_without_matching_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            index = Path(temporary) / "index.json"
            index.write_text(json.dumps({"schema_version": 1, "qualifications": []}), encoding="utf-8")
            completed = subprocess.run([
                sys.executable, str(ROOT / "validation/scripts/qualification_gate.py"),
                "--index", str(index),
            ], text=True, capture_output=True, check=False)
            self.assertEqual(3, completed.returncode, completed.stdout + completed.stderr)

    def test_registration_is_atomic_and_rejects_incomplete_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = root / "index.json"
            index.write_text(json.dumps({"schema_version": 1, "qualifications": []}), encoding="utf-8")
            report = self.valid_report(); report["cleanup_receipt"] = {"complete": False}
            report_path = root / "report.json"; report_path.write_text(json.dumps(report), encoding="utf-8")
            result = register_qualification.register(index_path=index, scenario_path=ROOT / "validation/scenarios/whole-spec-v1.json", report_path=report_path)
            self.assertEqual("blocked", result["decision"])
            self.assertEqual([], json.loads(index.read_text(encoding="utf-8"))["qualifications"])

    def test_registration_writes_only_a_verified_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = root / "index.json"
            report = self.valid_report(); report_path = root / "run" / "qualification.json"
            report_path.parent.mkdir(); report_path.write_text(json.dumps(report), encoding="utf-8")
            result = register_qualification.register(index_path=index, scenario_path=ROOT / "validation/scenarios/whole-spec-v1.json", report_path=report_path)
            self.assertEqual("registered", result["decision"])
            self.assertEqual("QUALIFIED", json.loads(index.read_text(encoding="utf-8"))["qualifications"][0]["decision"])

    def test_cleanup_removes_branch_before_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "qualification-cleanup-test"
            repo = root / "repo"
            repo.mkdir(parents=True)
            subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "qualification-test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "qualification@test.invalid"], cwd=repo, check=True)
            (repo / "README.md").write_text("test\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=repo, check=True)
            branch = "in-validation/qualification-cleanup-test/branch"
            subprocess.run(["git", "branch", branch], cwd=repo, check=True)

            cleanup_qualification._execute_cleanup([root], repo, [branch])

            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
