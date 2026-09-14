import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB
from transitions import SPEC_TRANSITIONS, THREAD_TRANSITIONS, transition
from next_action import next_action


class SQLiteControllerTests(unittest.TestCase):
    def test_initializes_wal_and_persists_idempotent_action(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            db.create_run("run-1", "demo", "requirement")
            first = db.set_action("run-1", "dispatch_spec", "SPEC-01")
            second = db.set_action("run-1", "dispatch_spec", "SPEC-01")
            self.assertEqual(first, second)
            self.assertEqual("wal", db.conn.execute("PRAGMA journal_mode").fetchone()[0])
            self.assertEqual(1, db.conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0])
            self.assertEqual(1, db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            db.close()

    def test_decision_is_audited(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            db.create_run("run-1", "demo", "requirement")
            db.decide("run-1", "merge_strategy", "merge", "merge", ["repo-rule"], "repository default")
            row = db.conn.execute("SELECT event_type FROM events WHERE entity_type='decision'").fetchone()
            self.assertEqual("controller_approved", row[0])
            db.close()

    def test_illegal_transitions_are_rejected(self):
        transition(SPEC_TRANSITIONS, "planned", "ready")
        transition(THREAD_TRANSITIONS, "created", "route_verified")
        with self.assertRaises(ValueError):
            transition(SPEC_TRANSITIONS, "planned", "merged")
        with self.assertRaises(ValueError):
            transition(THREAD_TRANSITIONS, "created", "working")

    def test_foreign_keys_prevent_orphan_tickets(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            with self.assertRaises(sqlite3.IntegrityError):
                db.conn.execute("INSERT INTO tickets(ticket_id,spec_id,title,status) VALUES('T1','missing','ticket','planned')")
            db.close()

    def test_planner_selects_ready_spec_then_delayed_tickets(self):
        with tempfile.TemporaryDirectory() as d:
            db=ControlDB(Path(d)/"run.db"); db.create_run("r","i","req")
            db.add_spec("r","S1","first",1); db.add_spec("r","S2","second",2,["S1"])
            self.assertEqual({"kind":"advance_spec","target":"S1","next_status":"ready"},next_action(db,"r"))
            db.update_spec("S1","ready")
            self.assertEqual("ticket_current_spec",next_action(db,"r")["kind"])
            db.close()


if __name__ == "__main__":
    unittest.main()
