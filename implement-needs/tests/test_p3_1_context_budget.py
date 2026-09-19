import unittest
import tempfile
from pathlib import Path
import sys
import subprocess
import json

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from context_budget import BENCHMARK_CASES, benchmark_context
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


if __name__ == "__main__":
    unittest.main()
