import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from context_projection import ContextProjectionError, build_context, read_history
from control_db import ControlDB


class ContextProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "run.db"
        self.db = ControlDB(self.path)
        self.addCleanup(self.db.close)
        self.db.create_run("run", "initiative", "requirement")
        self.db.add_spec("run", "S1", "First", 1, acceptance=["acceptance://S1"], expected_version=self.db.business_version("run"))
        self.db.add_spec("run", "S2", "Second", 2, blocked_by=["S1"], acceptance=["acceptance://S2"], expected_version=self.db.business_version("run"))
        self.db.record_receipt("run", "spec", "S2", {"status": "verified"}, self.db.business_version("run"))

    def test_active_projection_is_small_versioned_and_dependency_aware(self):
        context = build_context(self.db, "run", "implementation", "spec", "S2")
        self.assertEqual(self.db.business_version("run"), context["read_business_version"])
        self.assertEqual({"run_id", "phase", "entity_type", "entity_id", "read_business_version", "projection"}, set(context))
        self.assertEqual("S1", context["projection"]["direct_dependencies"][0]["spec_id"])
        self.assertNotIn("events", context["projection"])
        self.assertNotIn("snapshot", context["projection"])

    def test_cancelled_projection_uses_pointer_and_history_is_explicit(self):
        self.db.update_spec("S1", "cancelled", self.db.business_version("run"))
        context = build_context(self.db, "run", "verification", "spec", "S1")
        self.assertIn("history_pointer", context["projection"])
        self.assertNotIn("acceptance", context["projection"])
        history = read_history(self.db, context["projection"]["history_pointer"])
        self.assertEqual("S1", history["record"]["spec_id"])

    def test_invalid_phase_and_unreachable_pointer_fail_closed(self):
        with self.assertRaises(ContextProjectionError) as raised:
            build_context(self.db, "run", "unknown", "run", "run")
        self.assertEqual("context_phase_unknown", raised.exception.code)
        with self.assertRaises(ContextProjectionError) as raised:
            read_history(self.db, "history://spec/missing")
        self.assertEqual("history_unreachable", raised.exception.code)

    def test_context_cli_returns_versioned_projection_and_structured_rejection(self):
        controller = Path(__file__).parents[1] / "scripts" / "controller.py"
        self.db.close()
        command = [sys.executable, str(controller), "--db", str(self.path), "context", "--run-id", "run", "--phase", "planning", "--entity-type", "spec", "--entity-id", "S2"]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(0, completed.returncode, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual("S2", payload["entity_id"])
        bad_command = [sys.executable, str(controller), "--db", str(self.path), "context", "--run-id", "run", "--phase", "unknown", "--entity-type", "spec", "--entity-id", "S2"]
        bad = subprocess.run(bad_command, capture_output=True, text=True, check=False)
        self.assertNotEqual(0, bad.returncode)
        self.assertEqual("context_phase_unknown", json.loads(bad.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
