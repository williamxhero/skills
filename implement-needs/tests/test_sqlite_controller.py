import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ActionConflict, ControlDB
from next_action import next_action
from task_identity import TaskIdentity
from transitions import SPEC_TRANSITIONS, THREAD_TRANSITIONS, transition


def verified_gate(db, run_id, target_id, candidate="abc", environment="test", scope="controller"):
    return {
        "schema_version": 1,
        "expected": {"run_id": run_id, "target_id": target_id, "candidate_sha": candidate, "environment": environment, "business_version": db.business_version(run_id)},
        "actor": {"id": "owner", "authorized": True},
        "source": {"kind": "independent-readback", "trust": "verified"},
        "readback": {"status": "verified", "run_id": run_id, "target_id": target_id, "candidate_sha": candidate, "environment": environment},
        "test_scope": scope,
    }


def enter_implementation(db, run_id):
    for from_phase, to_phase in (
        ("initialized", "preflight_passed"),
        ("preflight_passed", "grilling"),
        ("grilling", "planning"),
        ("planning", "implementing"),
    ):
        receipt = {
            "run_id": run_id, "from_phase": from_phase, "to_phase": to_phase,
            "status": "verified", "business_version": db.business_version(run_id),
            "evidence": [f"phase://{to_phase}/verified"],
        }
        db.advance_run_phase(run_id, to_phase, receipt, db.business_version(run_id))


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

    def test_whole_spec_controller_recovery_does_not_require_ticket_line_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            db.create_run("run-whole", "initiative", "requirement")
            identity = TaskIdentity(
                task_id="controller-task", run_id="run-whole",
                attempt_id="01", nonce="0123456789abcdef",
            )
            db.add_thread(
                "run-whole", "thread-1", "controller", identity=identity,
                host_id="local", cwd="C:/work", project_id="project-1",
                formal_thread_id="thread-1",
            )
            readback = {
                "formal_thread_id": "thread-1", "host_id": "local",
                "task_id": "controller-task", "run_id": "run-whole",
                "attempt_id": "01", "owner_id": "owner", "cwd": "C:/work",
                "project_id": "project-1", "lifecycle": "completed",
            }
            db.persist_controller_recovery(
                "run-whole", "thread-1", readback,
                {"model": "gpt-5.6-sol", "effort": "high"},
                {"kind": "advance_spec", "target": "S1"},
                db.business_version("run-whole"),
            )
            payload = db.conn.execute(
                "SELECT payload FROM events WHERE run_id='run-whole' "
                "AND event_type='controller_recovery_committed'"
            ).fetchone()[0]
            self.assertNotIn("ticket_ledger", json.loads(payload)["gates"])
            db.close()

    def test_recover_unregistered_bootstrap_records_distinct_tombstone(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            db.create_run("run-orphan", "initiative", "requirement")
            identity = TaskIdentity(
                task_id="spec-task", run_id="run-orphan",
                attempt_id="01", nonce="0123456789abcdef",
            )
            result = db.recover_unregistered_bootstrap(
                "run-orphan", "missing-thread", "repair", identity,
                host_id="local", owner_id="controller", cwd="C:/work",
                project_id="project-1", title_token="[INN test]",
                creation_evidence=["backend:create_thread:accepted"],
                absence_evidence=[
                    "backend:read_thread:not_found",
                    "backend:list_tasks:absent",
                ],
                no_execution_evidence=[
                    "controller:assignment_absent",
                    "controller:managed_turn_absent",
                ],
                expected_version=db.business_version("run-orphan"),
            )
            self.assertEqual("tombstoned", result["lifecycle"])
            row = db.conn.execute(
                "SELECT lifecycle,outcome FROM threads WHERE thread_id='missing-thread'"
            ).fetchone()
            self.assertEqual(("tombstoned", "backend_absent_after_create"), tuple(row))
            self.assertEqual(
                "02", db.next_attempt_id("run-orphan", "spec-task", "01")
            )
            db.close()

    def test_recover_unregistered_bootstrap_requires_all_evidence_classes(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            db.create_run("run-orphan", "initiative", "requirement")
            identity = TaskIdentity(
                task_id="spec-task", run_id="run-orphan",
                attempt_id="01", nonce="0123456789abcdef",
            )
            with self.assertRaisesRegex(ValueError, "no-execution evidence"):
                db.recover_unregistered_bootstrap(
                    "run-orphan", "missing-thread", "repair", identity,
                    host_id="local", owner_id="controller", cwd="C:/work",
                    project_id="project-1", title_token="[INN test]",
                    creation_evidence=["backend:create_thread:accepted"],
                    absence_evidence=[
                        "backend:read_thread:not_found",
                        "backend:list_tasks:absent",
                    ],
                    no_execution_evidence=[],
                    expected_version=db.business_version("run-orphan"),
                )
            self.assertEqual(
                0, db.conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
            )
            db.close()

    def test_decision_is_audited(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            db.create_run("run-1", "demo", "requirement")
            authorization = {
                "schema_version": 1,
                "repository": {"id": "repo://skills", "branch": "master"},
                "target_ref": "master",
                "allowed_paths": ["implement-needs/*"],
                "allowed_tasks": ["run"],
                "allowed_actions": ["decide"],
                "deployment_target": "staging",
                "full_project_submission": False,
            }
            db.configure_authorization("run-1", authorization, db.business_version("run-1"))
            approved = db.authorize("run-1", action="decide")
            result = db.decide("run-1", "merge_strategy", "merge", "merge", ["repo-rule"], "repository default", "controller", {"action": "decide"}, "repository-default", approved, db.business_version("run-1"))
            self.assertEqual(1, result["decision_id"])
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
            enter_implementation(db, "run-line")
            self.assertEqual("T-01", next_action(db, "run-line")["target"])
            db.update_ticket("T-01", "ready")
            self.assertEqual("dispatch_ticket", next_action(db, "run-line")["kind"])
            db.close()

    def test_reconcile_ticket_blockers_maps_legacy_identity_to_run_owned_ticket(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            try:
                db.create_run("run-line", "initiative", "requirement", "single-ticket-line", "controller")
                db.add_spec("run-line", "SPEC", "tickets", 1)
                db.add_ticket("SPEC", "T-01", "first ticket", queue_position=1)
                db.add_ticket("SPEC", "T-02", "second ticket", blocked_by=["SPEC/01"], queue_position=2)
                result = db.reconcile_ticket_blockers("T-02", ["SPEC/01"], db.business_version("run-line"), ["github://issue/220"])
                self.assertTrue(result["changed"])
                self.assertEqual('["T-01"]', db.conn.execute("SELECT blocked_by FROM tickets WHERE ticket_id='T-02'").fetchone()[0])
                self.assertEqual("ticket_dependencies_reconciled", db.conn.execute("SELECT event_type FROM events ORDER BY event_id DESC LIMIT 1").fetchone()[0])
            finally:
                db.close()

    def test_reconcile_ticket_blockers_rejects_bad_aliases(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            try:
                db.create_run("run-line", "initiative", "requirement", "single-ticket-line", "controller")
                db.add_spec("run-line", "SPEC", "tickets", 1)
                db.add_spec("run-line", "OTHER", "other tickets", 2)
                db.add_ticket("SPEC", "T-01", "first ticket", queue_position=1)
                db.add_ticket("SPEC", "T-02", "second ticket", queue_position=2)
                db.add_ticket("OTHER", "T-03", "other ticket", queue_position=3)
                for alias in ("SPEC/1", "SPEC/99", "OTHER/01", "SPEC/nope"):
                    with self.subTest(alias=alias):
                        with self.assertRaises(ValueError):
                            db.reconcile_ticket_blockers("T-02", [alias], db.business_version("run-line"))
            finally:
                db.close()

    def test_single_ticket_line_does_not_skip_an_earlier_blocked_ticket(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            db.create_run("run-line", "initiative", "requirement", "single-ticket-line", "controller")
            db.add_spec("run-line", "SPEC-01", "first", 1)
            db.add_spec("run-line", "SPEC-02", "second", 2)
            db.add_ticket("SPEC-01", "T-01", "first ticket", blocked_by=["MISSING"], queue_position=1)
            db.add_ticket("SPEC-02", "T-02", "second ticket", queue_position=2)
            enter_implementation(db, "run-line")
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
            enter_implementation(db, "run-line")
            db.add_ticket("SPEC", "T-03", "third", queue_position=3)
            for status in ("ready", "implementing", "verified", "merged"):
                db.update_ticket("T-01", status)
            db.update_ticket(
                "T-01", "closed", ["commit:abc"], ["test://abc/controller"],
                ["acceptance:T-01"], gate=verified_gate(db, "run-line", "T-01"),
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
            enter_implementation(db, "run-line")
            self.assertEqual("#434", next_action(db, "run-line")["target"])
            db.close()

    def test_add_ticket_resolves_verified_spec_local_alias(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            try:
                db.create_run("run", "initiative", "requirement")
                db.add_spec("run", "#95", "CoordinatorSpec", 1)
                db.add_ticket("#95", "#220", "first ticket", queue_position=1)

                db.add_ticket("#95", "#221", "second ticket", blocked_by=["#95/01"], queue_position=2)

                row = db.conn.execute("SELECT blocked_by FROM tickets WHERE ticket_id='#221'").fetchone()
                self.assertEqual(["#220"], json.loads(row[0]))
            finally:
                db.close()

    def test_import_ticket_ledger_resolves_verified_spec_local_alias(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            try:
                queue = ["#220", "#221"]
                db.create_run("run-line", "initiative", "requirement", "single-ticket-line", "controller", queue)
                db.add_spec("run-line", "#95", "CoordinatorSpec", 1)
                ledger = {
                    "expected_ticket_count": 2,
                    "queue": queue,
                    "source": {"kind": "github", "evidence": ["github://issue/95"]},
                    "tickets": [
                        {"ticket_id": "#220", "spec_id": "#95", "title": "first", "status": "planned",
                         "blocked_by": [], "queue_position": 1, "readback_evidence": ["github://issue/220"]},
                        {"ticket_id": "#221", "spec_id": "#95", "title": "second", "status": "planned",
                         "blocked_by": ["#95/01"], "queue_position": 2, "readback_evidence": ["github://issue/221"]},
                    ],
                }

                db.import_ticket_ledger("run-line", ledger)

                row = db.conn.execute("SELECT blocked_by FROM tickets WHERE ticket_id='#221'").fetchone()
                self.assertEqual(["#220"], json.loads(row[0]))
            finally:
                db.close()

    def test_spec_local_alias_rejects_malformed_unknown_and_cross_spec_references(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            try:
                db.create_run("run", "initiative", "requirement")
                db.add_spec("run", "#95", "CoordinatorSpec", 1)
                db.add_spec("run", "#96", "AnalysisSkillSpec", 2)
                db.add_ticket("#95", "#220", "first ticket", queue_position=1)

                with self.assertRaisesRegex(ValueError, "malformed SPEC-local ticket alias"):
                    db.add_ticket("#95", "#221", "bad format", blocked_by=["#95/1"], queue_position=2)
                with self.assertRaisesRegex(ValueError, "unknown SPEC-local ticket alias"):
                    db.add_ticket("#95", "#221", "unknown position", blocked_by=["#95/02"], queue_position=2)
                with self.assertRaisesRegex(ValueError, "cross-SPEC ticket alias"):
                    db.add_ticket("#95", "#221", "wrong spec", blocked_by=["#96/01"], queue_position=2)
            finally:
                db.close()

    def test_resolved_alias_allows_next_action_to_resume_current_spec(self):
        with tempfile.TemporaryDirectory() as d:
            db = ControlDB(Path(d) / "run.db")
            try:
                db.create_run("run", "initiative", "requirement")
                db.add_spec("run", "#94", "DebateSpec", 1)
                db.add_spec("run", "#95", "CoordinatorSpec", 2, blocked_by=["#94"])
                db.add_ticket("#95", "#220", "first ticket", queue_position=1)
                db.add_ticket("#95", "#221", "second ticket", blocked_by=["#95/01"], queue_position=2)
                db.conn.execute("UPDATE specs SET status='closed' WHERE spec_id='#94'")
                db.record_delivery_proof("spec", "#94", "delivery", "merge:#94", ["commit:#94", "test:#94"])
                db.conn.execute("UPDATE specs SET status='ready' WHERE spec_id='#95'")
                db.conn.execute("UPDATE runs SET run_phase='implementing' WHERE run_id='run'")

                action = next_action(db, "run", include_recovery=False)

                self.assertEqual({"kind": "ticket_current_spec", "target": "#95"}, action)
            finally:
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
            db.update_ticket("T", "closed", ["commit:abc"], ["test://abc/controller"], ["acceptance:#T"], gate=verified_gate(db, "r", "T"))
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
            enter_implementation(db, "r")
            self.assertEqual({"kind":"advance_spec","target":"S1","next_status":"ready"},next_action(db,"r"))
            db.update_spec("S1","ready")
            self.assertEqual("ticket_current_spec",next_action(db,"r")["kind"])
            db.close()


if __name__ == "__main__":
    unittest.main()
