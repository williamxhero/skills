import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB


class ActionCliRestartTests(unittest.TestCase):
    def test_cli_and_restart_preserve_unknown_recovery_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.db"
            db = ControlDB(path)
            db.create_run("run", "initiative", "requirement")
            version = db.business_version("run")
            db.close()
            controller = Path(__file__).parents[1] / "scripts" / "controller.py"
            prepared = subprocess.run([sys.executable, str(controller), "--db", str(path), "prepare-intent", "--run-id", "run", "--logical-action", "deploy", "--target", "service", "--target-state", "v1", "--expected-version", str(version)], capture_output=True, text=True, check=False)
            self.assertEqual(0, prepared.returncode, prepared.stderr)
            packet = json.loads(prepared.stdout)
            unknown = subprocess.run([sys.executable, str(controller), "--db", str(path), "intent-outcome", "--intent-id", str(packet["intent_id"]), "--status", "outcome_unknown", "--response", '{"status":"response_lost"}', "--expected-version", str(packet["business_version"])], capture_output=True, text=True, check=False)
            self.assertEqual(0, unknown.returncode, unknown.stderr)
            next_action = subprocess.run([sys.executable, str(controller), "--db", str(path), "next-action", "--run-id", "run"], capture_output=True, text=True, check=False)
            self.assertEqual(0, next_action.returncode, next_action.stderr)
            self.assertEqual("reconcile_intent", json.loads(next_action.stdout)["kind"])
            reopened = ControlDB.open_existing(path)
            snapshot = reopened.snapshot("run")
            self.assertEqual("outcome_unknown", snapshot["intents"][0]["status"])
            reopened.close()


if __name__ == "__main__":
    unittest.main()
