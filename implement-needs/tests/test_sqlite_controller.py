import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ActionConflict, ControlDB
from next_action import next_action
from transitions import SPEC_TRANSITIONS, THREAD_TRANSITIONS, transition


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
            self.assertEqual(2, db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            db.close()

    def test_decision_is_audited(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            db.create_run("run-1", "demo", "requirement")
            db.decide("run-1", "merge_strategy", "merge", "merge", ["repo-rule"], "repository default")
            row = db.conn.execute("SELECT event_type FROM events WHERE entity_type='decision'").fetchone()
            self.assertEqual("controller_approved", row[0])
            db.close()

    def test_different_pending_action_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            db.create_run("run-1", "demo", "requirement")
            db.set_action("run-1", "dispatch_spec", "SPEC-01")
            with self.assertRaises(ActionConflict):
                db.set_action("run-1", "wait_spec", "SPEC-01")
            db.close()

    def test_single_ticket_line_is_global_and_uses_controller_task(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            db.create_run("run-line", "initiative", "requirement", "single-ticket-line", "01a09a58-dfcd-72b0-a5d7-c359eefce9a2")
            db.add_spec("run-line", "SPEC-01", "first", 1)
            db.add_spec("run-line", "SPEC-02", "second", 2)
            db.add_ticket("SPEC-02", "T-02", "second ticket", queue_position=2)
            db.add_ticket("SPEC-01", "T-01", "first ticket", queue_position=1)
            self.assertEqual("T-01", next_action(db, "run-line")["target"])
            db.update_ticket("T-01", "ready")
            self.assertEqual("dispatch_ticket", next_action(db, "run-line")["kind"])
            db.close()

    def test_single_ticket_line_does_not_skip_an_earlier_blocked_ticket(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            db.create_run("run-line", "initiative", "requirement", "single-ticket-line", "controller")
            db.add_spec("run-line", "SPEC-01", "first", 1)
            db.add_spec("run-line", "SPEC-02", "second", 2)
            db.add_ticket("SPEC-01", "T-01", "first ticket", blocked_by=["MISSING"], queue_position=1)
            db.add_ticket("SPEC-02", "T-02", "second ticket", queue_position=2)
            self.assertEqual("T-01", next_action(db, "run-line")["target"])
            self.assertEqual("wait_ticket_blocker", next_action(db, "run-line")["kind"])
            db.close()

    def test_single_ticket_line_ignores_known_forward_references(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            queue = ["T-01", "T-02", "T-03"]
            db.create_run(
                "run-line", "initiative", "requirement", "single-ticket-line",
                "controller", queue,
            )
            db.add_spec("run-line", "SPEC", "tickets", 1)
            db.add_ticket("SPEC", "T-01", "first", queue_position=1)
            db.add_ticket(
                "SPEC", "T-02", "second", blocked_by=["T-01", "T-03"],
                queue_position=2,
            )
            db.add_ticket("SPEC", "T-03", "third", queue_position=3)
            for status in ("ready", "implementing", "verified", "merged"):
                db.update_ticket("T-01", status)
            db.update_ticket(
                "T-01", "closed", ["commit:abc"], ["test:green"],
                ["acceptance:T-01"],
            )
            action = next_action(db, "run-line")
            self.assertEqual("advance_ticket", action["kind"])
            self.assertEqual("T-02", action["target"])
            db.close()

    def test_migrate_existing_run_to_single_ticket_line_keeps_controller_unbound(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            db.create_run("run-legacy", "initiative", "requirement")
            queue = ["#433", "#434", "#437"]
            result = db.migrate_run_to_single_ticket_line("run-legacy", queue)
            self.assertTrue(result["changed"])
            row = db.conn.execute(
                "SELECT execution_mode,controller_task_id,queue_definition,current_action "
                "FROM runs WHERE run_id='run-legacy'"
            ).fetchone()
            self.assertEqual("single-ticket-line", row[0])
            self.assertIsNone(row[1])
            self.assertEqual(queue, json.loads(row[2]))
            self.assertEqual("repair_queue:run-legacy", row[3])
            self.assertEqual("repair_queue", next_action(db, "run-legacy")["kind"])
            event = db.conn.execute(
                "SELECT payload FROM events WHERE run_id='run-legacy' "
                "AND event_type='run_mode_migrated'"
            ).fetchone()
            self.assertIn("single-ticket-line", event[0])
            db.close()

    def test_migrate_single_ticket_line_is_idempotent_and_rejects_races(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            db.create_run("run-legacy", "initiative", "requirement")
            queue = ["#433", "#434"]
            first = db.migrate_run_to_single_ticket_line("run-legacy", queue)
            second = db.migrate_run_to_single_ticket_line("run-legacy", queue)
            self.assertTrue(first["changed"])
            self.assertFalse(second["changed"])
            with self.assertRaises(ValueError):
                db.migrate_run_to_single_ticket_line("run-legacy", ["#434", "#433"])
            db.close()

    def test_migrate_run_refuses_pending_action_without_mutating_mode(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            db.create_run("run-legacy", "initiative", "requirement")
            db.set_action("run-legacy", "repair_spec", "q395")
            with self.assertRaises(ActionConflict):
                db.migrate_run_to_single_ticket_line("run-legacy", ["#433"])
            row = db.conn.execute(
                "SELECT execution_mode,controller_task_id,queue_definition FROM runs "
                "WHERE run_id='run-legacy'"
            ).fetchone()
            self.assertEqual("whole-spec", row[0])
            self.assertIsNone(row[1])
            self.assertEqual([], json.loads(row[2]))
            db.close()

    def test_single_ticket_line_fails_closed_when_declared_queue_has_no_ticket_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            queue = ["#433", "#434"]
            db.create_run("run-line", "initiative", "requirement", "single-ticket-line", "controller", queue)
            db.add_spec("run-line", "#433", "first", 1)
            db.add_spec("run-line", "#434", "second", 2, blocked_by=["#433"])
            action = next_action(db, "run-line")
            self.assertEqual("repair_queue", action["kind"])
            self.assertEqual("ticket_ledger_incomplete", action["reason"])
            db.close()

    def test_single_ticket_line_fails_closed_when_ticket_ledger_does_not_match_queue(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            queue = ["#433", "#434"]
            db.create_run("run-line", "initiative", "requirement", "single-ticket-line", "controller", queue)
            db.add_spec("run-line", "#433", "first", 1)
            db.add_spec("run-line", "#434", "second", 2, blocked_by=["#433"])
            db.add_ticket("#433", "#433", "first ticket", queue_position=1)
            db.add_ticket("#434", "#435", "wrong ticket", queue_position=2)
            action = next_action(db, "run-line")
            self.assertEqual("repair_queue", action["kind"])
            self.assertEqual("ticket_ledger_incomplete", action["reason"])
            db.close()

    def test_import_ticket_ledger_requires_exact_queue_and_closed_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            queue = ["#433", "#434"]
            db.create_run("run-line", "initiative", "requirement", "single-ticket-line", "controller", queue)
            db.add_spec("run-line", "#433", "first", 1)
            db.add_spec("run-line", "#434", "second", 2, blocked_by=["#433"])
            ledger = {"expected_ticket_count":2, "queue":["#433", "#434"], "source":{"kind":"github", "evidence":["github://issue/444"]}, "tickets":[
                {"ticket_id":"#433", "spec_id":"#433", "title":"first", "status":"closed",
                 "blocked_by":[], "queue_position":1, "commits":["commit:abc"], "tests":["test:green"], "acceptance":["acceptance:#433"], "readback_evidence":["github://issue/433"]},
                {"ticket_id":"#434", "spec_id":"#434", "title":"second", "status":"planned",
                 "blocked_by":["#433"], "queue_position":2, "readback_evidence":["github://issue/434"]},
            ]}
            result = db.import_ticket_ledger("run-line", ledger)
            self.assertTrue(result["changed"])
            self.assertEqual(2, result["ticket_count"])
            self.assertFalse(db.import_ticket_ledger("run-line", ledger)["changed"])
            self.assertEqual("#434", next_action(db, "run-line")["target"])
            db.close()

    def test_import_ticket_ledger_rejects_missing_closed_evidence_without_writing(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            queue = ["#433"]
            db.create_run("run-line", "initiative", "requirement", "single-ticket-line", "controller", queue)
            db.add_spec("run-line", "#433", "first", 1)
            with self.assertRaisesRegex(ValueError, "commit evidence"):
                db.import_ticket_ledger("run-line", {"expected_ticket_count":1, "queue":["#433"], "source":{"kind":"github", "evidence":["github://issue/444"]}, "tickets":[{"ticket_id":"#433", "spec_id":"#433", "title":"first", "status":"closed", "readback_evidence":["github://issue/433"]}]})
            self.assertEqual(0, db.conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0])
            db.close()

    def test_closing_ticket_requires_commit_and_test_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            db.create_run("r", "i", "req")
            db.add_spec("r", "S", "spec", 1)
            db.add_ticket("S", "T", "ticket")
            for status in ("ready", "implementing", "verified", "merged"):
                db.update_ticket("T", status)
            with self.assertRaises(ValueError):
                db.update_ticket("T", "closed")
            db.update_ticket("T", "closed", ["commit:abc"], ["test:green"], ["acceptance:#T"])
            self.assertEqual("closed", db.conn.execute("SELECT status FROM tickets WHERE ticket_id='T'").fetchone()[0])
            db.close()

    def test_closing_ticket_rejects_unstructured_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            db.create_run("r", "i", "req")
            db.add_spec("r", "S", "spec", 1)
            db.add_ticket("S", "T", "ticket")
            for status in ("ready", "implementing", "verified", "merged"):
                db.update_ticket("T", status)
            with self.assertRaisesRegex(ValueError, "commit evidence"):
                db.update_ticket("T", "closed", ["a commit"], ["test:green"])
            with self.assertRaisesRegex(ValueError, "test evidence"):
                db.update_ticket("T", "closed", ["commit:abc"], ["green"])
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
