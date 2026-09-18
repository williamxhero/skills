import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from control_db import ControlDB
from reconcile import probe_and_record, reconcile_backend, reconcile_inventory
from task_identity import TaskIdentity, format_token


class ReconcileBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = ControlDB(Path(self.tmp.name) / "state.db")
        self.db.create_run("R1", "initiative", "requirement")
        identity = TaskIdentity("T1", "R1", "01", "0123456789abcdef")
        self.db.add_thread("R1", "local-1", "spec", identity=identity, client_thread_id="client-1", owner_id="owner", cwd="C:/w", project_id="p", title_token=format_token(identity))

    def tearDown(self):
        self.db.close(); self.tmp.cleanup()

    def seed_line_ledger(self, run_id, initiative, controller, queue):
        self.db.create_run(run_id, initiative, "queue", "single-ticket-line", controller, queue)
        self.db.add_spec(run_id, "SPEC", "spec", 1)
        self.db.import_ticket_ledger(run_id, {
            "expected_ticket_count": len(queue), "queue": queue,
            "source": {"kind": "github", "evidence": ["github://issue/444"]},
            "tickets": [
                {"ticket_id": ticket_id, "spec_id": "SPEC", "title": ticket_id,
                 "status": "planned", "queue_position": position,
                 "readback_evidence": [f"github://issue/{ticket_id[1:]}"]}
                for position, ticket_id in enumerate(queue, 1)
            ],
        })
        for from_phase, to_phase in (
            ("initialized", "preflight_passed"),
            ("preflight_passed", "grilling"),
            ("grilling", "planning"),
            ("planning", "implementing"),
        ):
            receipt = {
                "run_id": run_id, "from_phase": from_phase, "to_phase": to_phase,
                "status": "verified", "business_version": self.db.business_version(run_id),
                "evidence": [f"phase://{to_phase}/verified"],
            }
            self.db.advance_run_phase(run_id, to_phase, receipt, self.db.business_version(run_id))

    def test_formal_readback_binds_and_persists(self):
        result = reconcile_inventory(self.db, "R1", {"reconciliation_status":"complete", "tasks":[{"formal_thread_id":"formal-1", "host_id":"host-1", "client_thread_id":"client-1", "title":format_token(TaskIdentity("T1","R1","01","0123456789abcdef")) + " work", "readback":{"formal_thread_id":"formal-1","host_id":"host-1","task_id":"T1","run_id":"R1","attempt_id":"01","owner_id":"owner","cwd":"C:/w","project_id":"p","lifecycle":"active","readback_evidence":["thread/read"]}}]})
        self.assertEqual("allow", result["decision"])
        row = self.db.threads_by_client_id("client-1")[0]
        self.assertEqual("active", row["lifecycle"])
        self.assertEqual("formal-1", row["formal_thread_id"])
        self.assertEqual("host-1", row["host_id"])

    def test_inconclusive_inventory_blocks_replacement(self):
        result = reconcile_inventory(self.db, "R1", {"reconciliation_status":"inconclusive", "tasks":[]})
        self.assertEqual("repair", result["decision"])
        self.assertIn("backend_inventory_inconclusive", result["errors"])

    def test_duplicate_candidates_block(self):
        identity = TaskIdentity("T2", "R1", "01", "fedcba9876543210")
        self.db.add_thread("R1", "local-2", "spec", identity=identity, client_thread_id="same", owner_id="owner", cwd="C:/w", project_id="p", title_token=format_token(identity))
        self.db.add_thread("R1", "local-3", "spec", identity=TaskIdentity("T3", "R1", "01", "0011223344556677"), client_thread_id="same", owner_id="owner", cwd="C:/w", project_id="p", title_token="[INN v=1 task=T3 run=R1 attempt=01 nonce=0011223344556677]")
        result = reconcile_inventory(self.db, "R1", {"reconciliation_status":"complete", "tasks":[{"client_thread_id":"same"}]})
        self.assertEqual("repair", result["decision"])
        self.assertIn("identity_ambiguous", result["errors"])

    def test_controller_cli_reconcile_entrypoint(self):
        inventory = Path(self.tmp.name) / "inventory.json"
        inventory.write_text(json.dumps({"reconciliation_status":"inconclusive", "tasks":[]}), encoding="utf-8")
        completed = subprocess.run([sys.executable, str(Path(__file__).parents[1] / "scripts" / "controller.py"), "--db", str(self.db.path), "reconcile-backend", "--run-id", "R1", "--inventory", str(inventory)], capture_output=True, text=True, check=False)
        self.assertEqual(1, completed.returncode)
        self.assertEqual("repair", json.loads(completed.stdout)["decision"])

    def test_controller_cli_backend_command_cannot_bypass_handshake(self):
        completed = subprocess.run([
            sys.executable, str(Path(__file__).parents[1] / "scripts" / "controller.py"),
            "--db", str(self.db.path), "reconcile-backend", "--run-id", "R1",
            "--backend-command", "missing-task-bridge-command",
        ], capture_output=True, text=True, check=False)
        self.assertEqual(1, completed.returncode)
        payload = json.loads(completed.stdout)
        self.assertEqual("repair", payload["decision"])
        self.assertTrue(payload["errors"][0].startswith("capability_handshake_failed:"))

    def test_capability_probe_persists_operation_level_evidence(self):
        class Connector:
            def request(self, operation, params):
                if operation == "capabilities":
                    return {"operations": [
                        "create_thread", "list_tasks", "read_thread", "read_applied_route",
                        "send_message_to_thread", "set_thread_archived", "read_archive_state",
                    ], "formal_identity": True, "route_readback": True,
                    "probe_target": {"formal_thread_id": "formal-probe", "host_id": "local"},
                    "evidence": ["connector/capabilities"]}
                if operation == "list_tasks":
                    return {"tasks": [{"formal_thread_id": "formal-probe", "host_id": "local"}]}
                if operation == "read_thread":
                    return {"formal_thread_id": "formal-probe", "host_id": "local",
                            "task_id": "PROBE", "run_id": "R1", "attempt_id": "01",
                            "owner_id": "owner", "cwd": "C:/w", "project_id": "p",
                            "lifecycle": "active", "readback_evidence": ["thread/read"]}
                if operation == "read_applied_route":
                    return {"model": "gpt-5.6-sol", "effort": "high", "evidence": ["route/read"]}
                if operation == "read_archive_state":
                    return {"archived": False, "evidence": ["archive/read"]}
                return {"dry_run": True, "evidence": [f"{operation}/dry-run"]}

        capability = probe_and_record(self.db, "R1", Connector())
        self.assertIn("probe:read_applied_route", capability["capability_evidence"])
        row = self.db.conn.execute(
            "SELECT payload FROM observations WHERE run_id='R1' AND entity_type='backend' "
            "AND entity_id='task-backend'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertIn("formal-probe", row[0])
        self.assertIn("gpt-5.6-sol", row[0])

    def test_live_reconciliation_recovers_designated_single_line_controller(self):
        queue = ["#433", "#434", "#437", "#439", "#438", "#414", "#415", "#416", "#417", "#418", "#419", "#420", "#421", "#422", "#427", "#428", "#391", "#392", "#393", "#435", "#441", "#394", "#395", "#396", "#397", "#398", "#399", "#401", "#442", "#404", "#408", "#429", "#443"]
        self.seed_line_ledger("R444", "#444", "codex://threads/01a09a58-dfcd-72b0-a5d7-c359eefce9a2", queue)
        identity = TaskIdentity("CONTROLLER", "RTEST", "01", "0123456789abcdef")
        self.db.add_thread(
            "R444", "registry-controller", "controller", identity=identity,
            formal_thread_id="codex://threads/01a09a58-dfcd-72b0-a5d7-c359eefce9a2",
            host_id="local", owner_id="owner", cwd="C:/w", project_id="p",
        )

        class Backend:
            def list_tasks(self, **params):
                return {"tasks": [{
                    "formal_thread_id": params["formal_thread_id"], "host_id": "local",
                }]}

            def read_thread(self, **params):
                return {"formal_thread_id": params["formal_thread_id"], "host_id": params["host_id"],
                        "task_id": "CONTROLLER", "run_id": "R444", "attempt_id": "01",
                        "owner_id": "owner", "cwd": "C:/w", "project_id": "p",
                        "lifecycle": "active", "readback_evidence": ["thread/read"]}

            def read_applied_route(self, **params):
                return {"model": "gpt-5.6-sol", "effort": "high", "evidence": ["route/read"]}

        result = reconcile_backend(self.db, "R444", Backend())
        self.assertEqual("allow", result["decision"])
        self.assertEqual("bound", result["matches"][0]["status"])
        row = self.db.conn.execute("SELECT formal_thread_id,host_id,route_readback FROM threads WHERE thread_id='registry-controller'").fetchone()
        self.assertEqual("codex://threads/01a09a58-dfcd-72b0-a5d7-c359eefce9a2", row[0])
        self.assertEqual("local", row[1])
        self.assertIn("gpt-5.6-sol", row[2])

    def test_single_line_recovery_rejects_partial_identity(self):
        self.seed_line_ledger("RTEST", "test", "thread-controller", ["TICKET"])
        identity = TaskIdentity("CONTROLLER", "RTEST", "01", "0123456789abcdef")
        self.db.add_thread("RTEST", "registry-controller", "controller", identity=identity, formal_thread_id="thread-controller", host_id="local", owner_id="owner", cwd="C:/w", project_id="p")

        class Backend:
            def list_tasks(self, **params):
                return {"tasks": [{"formal_thread_id": "thread-controller", "host_id": "local"}]}
            def read_thread(self, **params):
                return {"formal_thread_id": "thread-controller", "host_id": "local", "lifecycle": "active", "readback_evidence": ["thread/read"]}

        result = reconcile_backend(self.db, "RTEST", Backend())
        self.assertEqual("repair", result["decision"])
        self.assertIn("controller_identity_unresolved", result["errors"])

    def test_single_line_recovery_requires_applied_route(self):
        self.seed_line_ledger("RTEST", "test", "thread-controller", ["TICKET"])
        identity = TaskIdentity("CONTROLLER", "RTEST", "01", "0123456789abcdef")
        self.db.add_thread("RTEST", "registry-controller", "controller", identity=identity, formal_thread_id="thread-controller", host_id="local", owner_id="owner", cwd="C:/w", project_id="p")

        class Backend:
            def list_tasks(self, **params):
                return {"tasks": [{"formal_thread_id": "thread-controller", "host_id": "local"}]}
            def read_thread(self, **params):
                return {"formal_thread_id": "thread-controller", "host_id": "local", "task_id": "CONTROLLER", "run_id": "RTEST", "attempt_id": "01", "owner_id": "owner", "cwd": "C:/w", "project_id": "p", "lifecycle": "active", "readback_evidence": ["thread/read"]}
            def read_applied_route(self, **params):
                return {"model": "gpt-5.6-sol", "effort": "high"}

        result = reconcile_backend(self.db, "RTEST", Backend())
        self.assertEqual("repair", result["decision"])
        self.assertTrue(any(error.startswith("controller_applied_route_failed") for error in result["errors"]))

    def test_recovery_persists_identity_route_and_recomputed_first_action_atomically(self):
        self.seed_line_ledger("RTEST", "test", "thread-controller", ["TICKET"])
        identity = TaskIdentity("CONTROLLER", "RTEST", "01", "0123456789abcdef")
        self.db.add_thread("RTEST", "registry-controller", "controller", identity=identity,
                           formal_thread_id="thread-controller", host_id="local",
                           owner_id="owner", cwd="C:/w", project_id="p")

        class Backend:
            def list_tasks(self, **params):
                return {"tasks": [{"formal_thread_id": "thread-controller", "host_id": "local"}]}
            def read_thread(self, **params):
                return {"formal_thread_id": "thread-controller", "host_id": "local",
                        "task_id": "CONTROLLER", "run_id": "RTEST", "attempt_id": "01",
                        "owner_id": "owner", "cwd": "C:/w", "project_id": "p",
                        "lifecycle": "active", "readback_evidence": ["thread/read"]}
            def read_applied_route(self, **params):
                return {"model": "gpt-5.6-sol", "effort": "high", "evidence": ["route/read"]}

        result = reconcile_backend(self.db, "RTEST", Backend())
        self.assertEqual("allow", result["decision"])
        self.assertEqual("advance_ticket", result["matches"][0]["next_action"]["kind"])
        event = self.db.conn.execute(
            "SELECT event_type FROM events WHERE run_id='RTEST' ORDER BY event_id DESC LIMIT 2"
        ).fetchall()
        self.assertEqual({"controller_recovery_committed", "controller_next_action_recomputed"}, {row[0] for row in event})
