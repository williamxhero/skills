import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB
from context_projection import ContextProjectionError, read_history
from phase_context import assemble_context, build_snapshot


class PhaseContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = ControlDB(Path(self.temp.name) / "run.db")
        self.db.create_run("run-1", "demo", "req")
        self.db.add_spec("run-1", "S1", "one", 1, acceptance=["merged", "tested"])

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_snapshot_has_version_cursor_and_incremental_events(self):
        first = self.db.save_snapshot("run-1", build_snapshot(self.db, "run-1"), self.db.event_cursor("run-1"))
        self.assertEqual(first["state_version"], first["event_cursor"])
        cursor = first["event_cursor"]
        self.db.update_spec("S1", "ready")
        context = assemble_context(self.db, "run-1", "planning")
        self.assertEqual(cursor, context["business_version"])
        self.assertEqual(cursor, context["event_cursor"])
        self.assertEqual(["spec_state_changed"], [event["event_type"] for event in context["events"]])
        self.assertEqual(["merged", "tested"], context["acceptance"])
        self.assertNotIn("snapshot", context)
        self.assertEqual("snapshot://run/run-1", context["snapshot_pointer"])
        history = read_history(self.db, context["snapshot_pointer"])
        self.assertEqual("snapshot", history["entity_type"])
        self.assertEqual("run-1", history["entity_id"])
        self.assertIn("specs", history["record"])

    def test_snapshot_pointer_is_fail_closed_when_unreachable(self):
        with self.assertRaises(ContextProjectionError) as raised:
            read_history(self.db, "snapshot://run/missing")
        self.assertEqual("history_unreachable", raised.exception.code)
        with self.assertRaises(ContextProjectionError) as malformed:
            read_history(self.db, "snapshot://invalid")
        self.assertEqual("history_pointer_invalid", malformed.exception.code)

    def test_snapshot_history_cli_is_public_and_fail_closed(self):
        assemble_context(self.db, "run-1", "planning")
        controller = Path(__file__).parents[1] / "scripts" / "controller.py"
        db_path = self.db.path
        self.db.close()
        good = subprocess.run(
            [sys.executable, str(controller), "--db", str(db_path), "context-history", "--pointer", "snapshot://run/run-1"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, good.returncode, good.stderr)
        self.assertEqual("snapshot", json.loads(good.stdout)["entity_type"])
        bad = subprocess.run(
            [sys.executable, str(controller), "--db", str(db_path), "context-history", "--pointer", "snapshot://run/missing"],
            capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(0, bad.returncode)
        self.assertEqual("history_unreachable", json.loads(bad.stdout)["error"])
        self.db = ControlDB(db_path)

    def test_stale_snapshot_write_is_rejected(self):
        first = self.db.save_snapshot("run-1", build_snapshot(self.db, "run-1"), self.db.event_cursor("run-1"))
        self.db.update_spec("S1", "ready")
        with self.assertRaisesRegex(ValueError, "stale snapshot cursor"):
            self.db.save_snapshot("run-1", {"version": {"revision": "old"}}, first["event_cursor"])
        with self.assertRaisesRegex(ValueError, "stale snapshot version"):
            self.db.save_snapshot("run-1", {"version": {"revision": "old"}}, expected_state_version=first["state_version"])

    def test_context_keeps_unresolved_exception_until_evidence_resolves_it(self):
        self.db.record_exception("run-1", "fp-1", "network", "backend unavailable", "log://1", {"retry": 1})
        context = assemble_context(self.db, "run-1", "recovery")
        self.assertEqual("fp-1", context["unresolved_exceptions"][0]["fingerprint"])
        self.db.resolve_exception("run-1", "fp-1", ["readback://1"])
        self.assertEqual([], assemble_context(self.db, "run-1", "recovery")["unresolved_exceptions"])


if __name__ == "__main__":
    unittest.main()
