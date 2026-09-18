import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB


class BootstrapCheckpointCliTests(unittest.TestCase):
    def test_cli_routes_bootstrap_and_persists_train_across_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.db"
            db = ControlDB(path)
            db.create_run("run", "initiative", "requirement")
            db.add_spec("run", "S1", "spec", 1, expected_version=db.business_version("run"))
            db.add_thread("run", "thread", "spec", "S1", expected_version=db.business_version("run"))
            version = db.business_version("run")
            db.close()
            controller = Path(__file__).parents[1] / "scripts" / "controller.py"
            next_action = subprocess.run([sys.executable, str(controller), "--db", str(path), "next-action", "--run-id", "run"], capture_output=True, text=True, check=False)
            self.assertEqual(0, next_action.returncode, next_action.stderr)
            self.assertEqual("verify_thread_route", json.loads(next_action.stdout)["kind"])
            route = subprocess.run([sys.executable, str(controller), "--db", str(path), "bootstrap-state", "--run-id", "run", "--thread-id", "thread", "--state", "route_verifying", "--receipt", '{"status":"verified","model":"m","effort":"low"}', "--expected-version", str(version)], capture_output=True, text=True, check=False)
            self.assertEqual(0, route.returncode, route.stderr)
            routed = json.loads(route.stdout)
            train = subprocess.run([sys.executable, str(controller), "--db", str(path), "initialize-test-train", "--run-id", "run", "--spec-ids", '["S1"]', "--expected-version", str(routed["business_version"])], capture_output=True, text=True, check=False)
            self.assertEqual(0, train.returncode, train.stderr)
            reopened = ControlDB.open_existing(path)
            self.assertEqual("route_verifying", reopened.bootstrap("run", "thread")["state"])
            self.assertTrue(reopened.test_train_status("run")["initialized"])
            reopened.close()


if __name__ == "__main__":
    unittest.main()
