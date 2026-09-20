import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from control_db import ControlDB
from supervisor import detect_interruption, supervise


CONTROLLER = Path(__file__).parents[1] / "scripts" / "controller.py"


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "run.db"
        self.db = ControlDB(self.path)
        self.db.create_run("run", "initiative", "requirement")
        self.db.add_spec("run", "S1", "closed", 1)
        self.db.add_spec("run", "S2", "successor", 2, blocked_by=["S1"])
        self.db.conn.execute("UPDATE specs SET status='closed' WHERE spec_id='S1'")
        self.db.record_delivery_proof("spec", "S1", "delivery", "spec:S1:closed", ["proof://S1"])
        self.db.conn.execute("UPDATE runs SET run_phase='implementing' WHERE run_id='run'")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.db.close)

    def test_supervisor_detects_and_executes_next_frontier(self):
        result = supervise(self.db, "run", owner_id="sup-a")
        self.assertTrue(result["recovery_detected"])
        self.assertTrue(result["recovery_executed"])
        self.assertEqual("recovery_completed", result["outcome"])
        self.assertEqual("ready", self.db.conn.execute(
            "SELECT status FROM specs WHERE spec_id='S2'"
        ).fetchone()[0])
        self.assertEqual([], self.db.conn.execute(
            "SELECT action_id FROM actions WHERE status IN ('pending','running')"
        ).fetchall())

    def test_detection_only_never_claims_or_advances(self):
        result = supervise(self.db, "run", execute=False)
        self.assertEqual("detected_only", result["execution"]["status"])
        self.assertFalse(result["recovery_executed"])
        self.assertEqual("planned", self.db.conn.execute(
            "SELECT status FROM specs WHERE spec_id='S2'"
        ).fetchone()[0])

    def test_wait_and_explicit_stop_are_not_interruptions(self):
        self.db.record_external_wait("request-1", "run", self.db.event_cursor("run"), "remote completion", "2999-01-01T00:00:00+00:00")
        waiting = detect_interruption(self.db, "run")
        self.assertFalse(waiting["detected"])
        self.assertEqual("waiting_external", waiting["classification"])
        self.db.conn.execute("UPDATE runs SET terminal_result='user_stopped',status='user_stopped' WHERE run_id='run'")
        stopped = supervise(self.db, "run")
        self.assertEqual("explicit_stop", stopped["outcome"])
        self.assertFalse(stopped["recovery_detected"])

    def test_public_cli_survives_parent_process_boundary(self):
        self.db.close()
        completed = subprocess.run(
            [sys.executable, str(CONTROLLER), "--db", str(self.path), "supervisor", "--run-id", "run", "--owner-id", "cli-supervisor"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual("recovery_completed", payload["outcome"])
        reopened = ControlDB(self.path)
        try:
            self.assertEqual("ready", reopened.conn.execute("SELECT status FROM specs WHERE spec_id='S2'").fetchone()[0])
        finally:
            reopened.close()
        self.db = None


if __name__ == "__main__":
    unittest.main()
