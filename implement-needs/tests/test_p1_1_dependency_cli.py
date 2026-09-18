import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from control_db import ControlDB
from reconcile import audit, dependency_audit


class DependencyCliTests(unittest.TestCase):
    def test_cli_exposes_structure_and_readiness_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.db"
            db = ControlDB(path)
            db.create_run("run", "initiative", "requirement")
            db.add_spec("run", "S1", "first", 1)
            db.add_spec("run", "S2", "second", 2, ["S1"])
            version = db.business_version("run")
            db.close()
            controller = Path(__file__).parents[1] / "scripts" / "controller.py"
            check = subprocess.run([sys.executable, str(controller), "--db", str(path), "dependency-check", "--run-id", "run"], capture_output=True, text=True, check=False)
            self.assertEqual(0, check.returncode, check.stderr)
            self.assertEqual("valid", json.loads(check.stdout)["status"])
            readiness = subprocess.run([sys.executable, str(controller), "--db", str(path), "dependency-readiness", "--run-id", "run", "--spec-id", "S2"], capture_output=True, text=True, check=False)
            self.assertEqual(0, readiness.returncode, readiness.stderr)
            self.assertEqual("waiting", json.loads(readiness.stdout)["status"])
            reopened = ControlDB.read_only(path)
            self.assertEqual(version, reopened.business_version("run"))
            reopened.close()

    def test_reconcile_keeps_waiting_out_of_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run", "initiative", "requirement")
            db.add_spec("run", "S1", "first", 1)
            db.add_spec("run", "S2", "second", 2, ["S1"])
            self.assertEqual([], audit(db, "run"))
            result = dependency_audit(db, "run", "S2")
            self.assertEqual("allow", result["decision"])
            self.assertEqual("waiting", result["status"])
            self.assertEqual([], result["errors"])
            db.close()

    def test_reconcile_reports_structural_error_not_waiting(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run", "initiative", "requirement")
            db.add_spec("run", "S1", "first", 1, ["MISSING"])
            result = dependency_audit(db, "run", "S1")
            self.assertEqual("repair", result["decision"])
            self.assertEqual("invalid", result["status"])
            self.assertEqual("unknown_dependency", result["errors"][0]["code"])
            self.assertIn("dependency_unknown_dependency", audit(db, "run"))
            db.close()


if __name__ == "__main__":
    unittest.main()
