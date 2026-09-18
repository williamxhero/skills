import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from control_db import ControlDB


class StartupCliReferenceTests(unittest.TestCase):
    def test_root_skill_exposes_startup_reference_and_commands(self):
        skill = Path(__file__).parents[1] / "SKILL.md"
        reference = Path(__file__).parents[1] / "references" / "startup-and-scope.md"
        self.assertIn("references/startup-and-scope.md", skill.read_text(encoding="utf-8"))
        content = reference.read_text(encoding="utf-8")
        for command in ("startup-contract", "startup-check", "decide", "blocked_missing_dependency", "record-observation"):
            self.assertIn(command, content)

    def test_missing_contract_is_structured_and_unregistered_lookalike_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.db"
            db = ControlDB(path)
            db.create_run("run", "initiative", "requirement")
            db.close()
            controller = Path(__file__).parents[1] / "scripts" / "controller.py"
            checked = subprocess.run([sys.executable, str(controller), "--db", str(path), "startup-check", "--run-id", "run"], capture_output=True, text=True, check=False)
            self.assertNotEqual(0, checked.returncode)
            self.assertEqual("startup_contract_missing", json.loads(checked.stdout)["error"])

            db = ControlDB.open_existing(path)
            contract = {
                "schema_version": 1, "run_id": "run",
                "runtime": {"python": "3.13", "platform": "windows"},
                "skill_root": "C:/skills",
                "target_repository": {"id": "repo", "path": "C:/repo", "branch": "master"},
                "tracker": {"mode": "github"},
                "host": {"id": "local", "capabilities": ["python"]},
                "permissions": {"allowed_actions": ["read"]},
                "dependencies": [{"canonical_name": "implement-needs", "aliases": ["similar"]}],
            }
            with self.assertRaises(Exception) as raised:
                db.record_startup_contract("run", contract, [{"canonical_name": "implement-needs-copy", "aliases": ["other"], "path": "C:/copy", "digest": "sha256:copy", "adapter": "local"}], db.business_version("run"))
            db.close()
            self.assertEqual("blocked_missing_dependency", raised.exception.code)

    def test_restart_preserves_separate_streams(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.db"
            db = ControlDB(path)
            db.create_run("run", "initiative", "requirement")
            db.add_observation("run", "run", "run", {"operation": "probe", "status": "succeeded"})
            db.close()
            reopened = ControlDB.open_existing(path)
            snapshot = reopened.snapshot("run")
            self.assertIsNone(snapshot["startup_contract"])
            self.assertEqual(1, len(snapshot["observations"]))
            self.assertEqual([], snapshot["evidence_refs"])
            self.assertEqual([], snapshot["decisions"])
            reopened.close()


if __name__ == "__main__":
    unittest.main()
