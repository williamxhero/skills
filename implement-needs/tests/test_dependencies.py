import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB
from dependencies import dependency_status, validation_errors
from next_action import next_action


class DependencyTests(unittest.TestCase):
    def _db(self):
        directory = tempfile.TemporaryDirectory()
        db = ControlDB(Path(directory.name) / "run.db")
        db.create_run("run-1", "demo", "req")
        return directory, db

    def test_cancelled_blocker_is_not_delivery_but_explicit_waiver_is(self):
        directory, db = self._db()
        try:
            db.add_spec("run-1", "S1", "first", 1)
            db.add_spec("run-1", "S2", "second", 2, ["S1"])
            db.conn.execute("UPDATE specs SET status='cancelled' WHERE spec_id='S1'")
            status = dependency_status(db, "spec", "S2", "spec", "S1")
            self.assertFalse(status["satisfied"])
            self.assertEqual("blocker_not_delivered", status["code"])
            waiver = db.waive_dependency("spec", "S2", "spec", "S1", "delivery replaced", "controller-approved", "SPEC S2 only", ["decision://s2"])
            self.assertTrue(waiver["created"])
            self.assertTrue(dependency_status(db, "spec", "S2", "spec", "S1")["satisfied"])
        finally:
            db.close(); directory.cleanup()

    def test_closed_blocker_needs_delivery_proof_and_next_action_waits(self):
        directory, db = self._db()
        try:
            db.add_spec("run-1", "S1", "first", 1)
            db.add_spec("run-1", "S2", "second", 2, ["S1"])
            db.conn.execute("UPDATE specs SET status='closed' WHERE spec_id='S1'")
            self.assertEqual("delivery_proof_missing", dependency_status(db, "spec", "S2", "spec", "S1")["code"])
            self.assertEqual("wait_spec_dependency", next_action(db, "run-1")["kind"])
            db.record_delivery_proof("spec", "S1", "delivery", "merge:abc", ["commit://abc", "test://abc"])
            self.assertEqual("advance_spec", next_action(db, "run-1")["kind"])
        finally:
            db.close(); directory.cleanup()

    def test_unknown_and_forward_dependencies_fail_deterministically(self):
        directory, db = self._db()
        try:
            db.add_spec("run-1", "S1", "first", 1, ["missing"])
            self.assertEqual("repair_dependency", next_action(db, "run-1")["kind"])
            self.assertIn("unknown_blocker", {item["code"] for item in validation_errors(db, "run-1")})
            db.conn.execute("UPDATE specs SET blocked_by='[\"S2\"]' WHERE spec_id='S1'")
            db.add_spec("run-1", "S2", "later", 2)
            self.assertEqual("forward_dependency", next_action(db, "run-1")["reason"])
        finally:
            db.close(); directory.cleanup()

    def test_closed_ticket_creates_delivery_proof_and_rejects_evidence_free_close(self):
        directory, db = self._db()
        try:
            db.add_spec("run-1", "S1", "spec", 1)
            db.add_ticket("S1", "T1", "ticket")
            db.conn.execute("UPDATE tickets SET status='merged' WHERE ticket_id='T1'")
            with self.assertRaisesRegex(ValueError, "delivery evidence"):
                db.update_ticket("T1", "closed")
            self.assertEqual("merged", db.conn.execute("SELECT status FROM tickets WHERE ticket_id='T1'").fetchone()[0])
            db.update_ticket("T1", "closed", commits=["commit://abc"], tests=["test://abc"])
            self.assertTrue(dependency_status(db, "ticket", "anything", "ticket", "T1")["satisfied"])
        finally:
            db.close(); directory.cleanup()


if __name__ == "__main__":
    unittest.main()
