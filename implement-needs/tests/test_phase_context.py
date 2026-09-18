import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB
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
        self.assertEqual(cursor, context["event_cursor"])
        self.assertEqual(["spec_state_changed"], [event["event_type"] for event in context["events"]])
        self.assertEqual(["merged", "tested"], context["acceptance"])

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
