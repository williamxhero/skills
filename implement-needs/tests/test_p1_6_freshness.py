import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from context_projection import build_context
from control_db import ControlDB, StaleState


class ContextFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "run.db"
        self.db = ControlDB(self.path)
        self.addCleanup(self.db.close)
        self.db.create_run("run", "initiative", "requirement")
        self.db.add_spec("run", "S1", "First", 1, expected_version=self.db.business_version("run"))

    def test_stale_context_rejects_without_business_write_then_fresh_context_succeeds(self):
        context = build_context(self.db, "run", "implementation", "spec", "S1")
        before_version = self.db.business_version("run")
        before_events = self.db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.db.event("run", "observation", "freshness", "state_changed", {"status": "new"}, before_version)
        with self.assertRaises(StaleState) as raised:
            self.db.update_spec_from_context("S1", "ready", context)
        self.assertEqual("stale_state", raised.exception.code)
        self.assertEqual("planned", self.db.conn.execute("SELECT status FROM specs WHERE spec_id='S1'").fetchone()[0])
        self.assertEqual(before_events + 1, self.db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        fresh = build_context(self.db, "run", "implementation", "spec", "S1")
        self.db.update_spec_from_context("S1", "ready", fresh)
        self.assertEqual("ready", self.db.conn.execute("SELECT status FROM specs WHERE spec_id='S1'").fetchone()[0])

    def test_cli_context_write_returns_structured_stale_state(self):
        context = build_context(self.db, "run", "implementation", "spec", "S1")
        self.db.event("run", "observation", "freshness", "state_changed", {"status": "new"}, self.db.business_version("run"))
        self.db.close()
        controller = Path(__file__).parents[1] / "scripts" / "controller.py"
        command = [sys.executable, str(controller), "--db", str(self.path), "spec-state", "--spec-id", "S1", "--status", "ready", "--context", json.dumps(context), "--expected-version", str(context["read_business_version"])]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertNotEqual(0, completed.returncode)
        packet = json.loads(completed.stdout)
        self.assertEqual({"decision", "error", "run_id", "expected_version", "actual_version"}, set(packet))
        self.assertEqual("stale_state", packet["error"])


if __name__ == "__main__":
    unittest.main()
