import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB


class OperationIntentTests(unittest.TestCase):
    def test_retry_reuses_intent_and_request_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.db"
            db = ControlDB(path)
            db.create_run("run-1", "demo", "req")
            first = db.create_operation_intent("run-1", "create-task", "spec-1", {"branch": "a", "files": ["x"]})
            second = db.create_operation_intent("run-1", "create-task", "spec-1", {"files": ["x"], "branch": "a"})
            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            self.assertEqual(first["intent"]["intent_id"], second["intent"]["intent_id"])
            self.assertEqual(first["intent"]["external_request_id"], second["intent"]["external_request_id"])
            self.assertEqual(first["intent"]["input_digest"], second["intent"]["input_digest"])
            db.close()
            reopened = ControlDB(path)
            self.assertEqual("prepared", reopened.conn.execute("SELECT status FROM operation_intents").fetchone()[0])
            reopened.close()

    def test_concurrent_creators_converge_on_one_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.db"
            setup = ControlDB(path)
            setup.create_run("run-1", "demo", "req")
            setup.close()
            barrier = threading.Barrier(2)
            results = []
            errors = []

            def create():
                db = ControlDB(path)
                try:
                    barrier.wait()
                    results.append(db.create_operation_intent("run-1", "create", "task-1", {"branch": "feature"}))
                except Exception as exc:
                    errors.append(exc)
                finally:
                    db.close()

            workers = [threading.Thread(target=create) for _ in range(2)]
            for worker in workers: worker.start()
            for worker in workers: worker.join()
            self.assertEqual([], errors)
            self.assertEqual(2, len(results))
            self.assertEqual(results[0]["intent"]["intent_id"], results[1]["intent"]["intent_id"])
            self.assertEqual(1, sum(item["created"] for item in results))

    def test_changed_generation_or_input_is_new_work_and_explicit_key_rejects_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run-1", "demo", "req")
            first = db.create_operation_intent("run-1", "verify", "commit-a", {"tests": ["unit"]})
            second = db.create_operation_intent("run-1", "verify", "commit-b", {"tests": ["unit"]})
            third = db.create_operation_intent("run-1", "verify", "commit-a", {"tests": ["unit"]}, generation=1)
            self.assertNotEqual(first["intent"]["idempotency_key"], second["intent"]["idempotency_key"])
            self.assertNotEqual(first["intent"]["idempotency_key"], third["intent"]["idempotency_key"])
            with self.assertRaises(ValueError):
                db.create_operation_intent("run-1", "verify", "commit-a", {"tests": ["changed"]}, idempotency_key=first["intent"]["idempotency_key"])
            db.close()

    def test_unknown_outcome_must_be_reconciled_before_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run-1", "demo", "req")
            intent = db.create_operation_intent("run-1", "merge", "pr-1", {"sha": "abc"})["intent"]
            db.start_operation_intent(intent["intent_id"], "controller-1")
            db.mark_operation_unknown(intent["intent_id"], "response_lost", ["op://request/1"])
            with self.assertRaisesRegex(ValueError, "reconciled"):
                db.start_operation_intent(intent["intent_id"], "controller-1")
            reconciled = db.reconcile_operation_intent(intent["intent_id"], "not_found", ["readback://inventory/2"])
            self.assertEqual("reconciled_not_found", reconciled["status"])
            restarted = db.start_operation_intent(intent["intent_id"], "controller-2")
            self.assertEqual("executing", restarted["status"])
            self.assertEqual(2, restarted["attempts"])
            db.close()

    def test_unknown_result_can_be_confirmed_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run-1", "demo", "req")
            intent = db.create_operation_intent("run-1", "archive", "task-1", {})["intent"]
            db.start_operation_intent(intent["intent_id"], "controller-1")
            db.mark_operation_unknown(intent["intent_id"], "crash_after_request", ["op://request/2"])
            result = db.reconcile_operation_intent(intent["intent_id"], "succeeded", ["archive://readback/2"], {"archived": True})
            self.assertEqual("succeeded", result["status"])
            with self.assertRaises(ValueError):
                db.start_operation_intent(intent["intent_id"], "controller-2")
            db.close()

    def test_cli_carries_one_unknown_operation_through_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.db"
            controller = Path(__file__).parents[1] / "scripts" / "controller.py"
            command = [sys.executable, str(controller), "--db", str(path)]
            subprocess.run(command + ["init", "--run-id", "run-1", "--initiative", "demo", "--requirement", "req"], check=True, capture_output=True, text=True)
            created = subprocess.run(command + ["operation-intent", "--run-id", "run-1", "--operation", "merge", "--target", "pr-1", "--parameters", '{"sha":"abc"}'], check=True, capture_output=True, text=True)
            intent = json.loads(created.stdout)["intent"]
            subprocess.run(command + ["start-operation-intent", "--intent-id", str(intent["intent_id"]), "--executor-id", "controller"], check=True, capture_output=True, text=True)
            subprocess.run(command + ["operation-outcome-unknown", "--intent-id", str(intent["intent_id"]), "--reason", "lost", "--evidence", '["op://1"]'], check=True, capture_output=True, text=True)
            reconciled = subprocess.run(command + ["reconcile-operation-intent", "--intent-id", str(intent["intent_id"]), "--outcome", "not_found", "--evidence", '["readback://1"]'], check=True, capture_output=True, text=True)
            self.assertEqual("reconciled_not_found", json.loads(reconciled.stdout)["status"])


if __name__ == "__main__":
    unittest.main()
