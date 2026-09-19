import unittest
import tempfile
from pathlib import Path
import sys
import subprocess
import json

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from context_budget import BENCHMARK_CASES, benchmark_context, benchmark_context_suite
from phase_context import assemble_context, build_snapshot
from control_db import ControlDB


class ContextBudgetTests(unittest.TestCase):
    def test_fixed_benchmark_requires_existing_snapshot_and_reports_unknown_provider_data(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run", "initiative", "requirement")
            db.add_spec("run", "S1", "First", 1)
            missing = benchmark_context(db, "run")
            self.assertEqual("inconclusive", missing["decision"])
            self.assertEqual("snapshot_missing", missing["reason"])
            assemble_context(db, "run", "planning")
            before = db.business_version("run")
            report = benchmark_context(db, "run")
            self.assertEqual(list(BENCHMARK_CASES), report["benchmark"]["cases"])
            self.assertEqual("unknown", report["coverage"]["provider_tokens"])
            self.assertEqual("unknown", report["coverage"]["fees"])
            self.assertEqual(before, db.business_version("run"))
            self.assertEqual("allow", report["decision"])
            self.assertGreater(report["savings"]["payload_bytes"], 0)
            self.assertTrue(report["safety"]["completion_predicates_equivalent"])
            self.assertTrue(report["safety"]["evidence_coverage_equivalent"])
            self.assertTrue(report["safety"]["stale_write_rejection_covered"])
            controller = Path(__file__).parents[1] / "scripts" / "controller.py"
            db.close()
            cli = subprocess.run([sys.executable, str(controller), "--db", str(Path(directory) / "run.db"), "context-budget", "--run-id", "run"], capture_output=True, text=True, check=False)
            self.assertEqual(0, cli.returncode, cli.stderr)
            self.assertEqual("allow", json.loads(cli.stdout)["decision"])

    def test_benchmark_preserves_required_context_and_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run", "initiative", "requirement")
            db.add_spec("run", "S1", "First", 1, acceptance=["acceptance://S1"])
            assemble_context(db, "run", "planning")
            report = benchmark_context(db, "run", "implementation")
            self.assertTrue(report["safety"]["required_fields_preserved"])
            self.assertTrue(report["safety"]["history_pointer_reachable"])
            self.assertTrue(report["snapshot_pointer"].startswith("snapshot://run/"))
            db.close()

    def test_fixed_suite_measures_each_declared_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            fixture_runs = {}
            for index, case in enumerate(BENCHMARK_CASES):
                run_id = f"fixture-{index}"
                fixture_runs[case] = run_id
                db.create_run(run_id, "initiative", case)
                if case != "empty-run":
                    db.add_spec(run_id, f"S{index}", case, 1, acceptance=[f"acceptance://{case}"])
                if case == "active-dependency":
                    db.add_spec(run_id, "S-dependency", "dependency", 2, blocked_by=[f"S{index}"])
                if case == "closed-delivery":
                    db.add_ticket(f"S{index}", "T-closed", "closed delivery")
                    db.update_ticket("T-closed", "ready")
                    db.update_ticket("T-closed", "implementing")
                    db.update_ticket("T-closed", "verified")
                    db.update_ticket("T-closed", "merged")
                    db.update_ticket("T-closed", "closed", commits=["commit://closed"], tests=["test://closed"])
                if case == "recovery-exception":
                    db.record_exception(run_id, "fp-recovery", "network", "unavailable", "log://recovery")
                assemble_context(db, run_id, "planning")
            report = benchmark_context_suite(db, fixture_runs)
            self.assertEqual("allow", report["decision"])
            self.assertEqual(list(BENCHMARK_CASES), list(report["cases"]))
            self.assertTrue(all(item["run_id"] == fixture_runs[case] for case, item in report["cases"].items()))
            self.assertTrue(all(item["decision"] == "allow" for item in report["cases"].values()))
            db.close()

    def test_suite_without_all_fixed_cases_is_inconclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run", "initiative", "requirement")
            assemble_context(db, "run", "planning")
            report = benchmark_context_suite(db, {"empty-run": "run"})
            self.assertEqual("inconclusive", report["decision"])
            self.assertEqual([case for case in BENCHMARK_CASES if case != "empty-run"], report["missing_cases"])
            db.close()


if __name__ == "__main__":
    unittest.main()
