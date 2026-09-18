import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB
from tool_envelopes import failure, host_operation, success, waiting_external


class ToolEnvelopeTests(unittest.TestCase):
    def test_success_and_failure_are_compact_but_refer_to_full_evidence(self):
        result = success({"status": "closed"}, {"ticket_id": "T1"}, 12, "evidence://T1/12")
        self.assertEqual("succeeded", result["status"])
        self.assertEqual("evidence://T1/12", result["evidence_uri"])
        error = failure("timeout", "backend did not answer", "log://T1/attempt-2", 12)
        self.assertFalse(error["ok"])
        self.assertEqual("timeout", error["error"]["category"])

    def test_wait_envelope_and_polling_do_not_start_semantic_round_when_unchanged(self):
        wait = waiting_external("request-1", 7, "task archived", "2030-01-01T00:00:00+00:00")
        self.assertEqual("waiting_external", wait["status"])
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run-1", "demo", "req")
            db.record_external_wait("request-1", "run-1", 7, "task archived", "2030-01-01T00:00:00+00:00")
            unchanged = db.poll_external_wait("request-1", {"archived": False}, False)
            self.assertFalse(unchanged["changed"])
            self.assertFalse(unchanged["semantic_round"])
            # The initial run event is the only event; an unchanged poll adds no semantic event.
            self.assertEqual(0, db.conn.execute("SELECT COUNT(*) FROM events WHERE event_type='external_wait_changed'").fetchone()[0])
            changed = db.poll_external_wait("request-1", {"archived": True}, True, ["readback://1"])
            self.assertTrue(changed["semantic_round"])
            db.close()

    def test_host_operation_requires_explicit_capability(self):
        denied = host_operation("deploy", {"revision": "r1"}, "deploy", [])
        self.assertEqual("capability_required", denied["status"])
        allowed = host_operation("deploy", {"revision": "r1"}, "deploy", ["deploy"])
        self.assertEqual("authorized", allowed["status"])


if __name__ == "__main__":
    unittest.main()
