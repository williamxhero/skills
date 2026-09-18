import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB


class RuntimeObservationTests(unittest.TestCase):
    def test_observation_is_structured_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.db"
            db = ControlDB(path)
            db.create_run("run-1", "demo", "requirement")
            payload = dict(
                run_id="run-1", observation_key="run-1:wait:1", entity_type="action",
                entity_id="1", phase="external_wait", status="completed",
                scope="action", unit="milliseconds", started_at="2026-09-18T00:00:00+00:00",
                ended_at="2026-09-18T00:00:01+00:00", duration_ms=1000,
                source="fake-backend", usage={"input_tokens": "unknown"},
                metadata={"operation": "wait", "evidence": ["wait://1"]},
            )
            first = db.record_runtime_observation(**payload)
            second = db.record_runtime_observation(**payload)
            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            self.assertEqual(first["observation_id"], second["observation_id"])
            self.assertEqual(1, db.conn.execute("SELECT COUNT(*) FROM runtime_observations").fetchone()[0])
            self.assertEqual(1, db.conn.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='runtime_observation_recorded'"
            ).fetchone()[0])
            stored = db.conn.execute("SELECT usage,metadata FROM runtime_observations").fetchone()
            self.assertEqual({"input_tokens": "unknown"}, json.loads(stored[0]))
            self.assertEqual({"evidence": ["wait://1"], "operation": "wait"}, json.loads(stored[1]))
            db.close()

    def test_same_key_with_changed_data_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run-1", "demo", "requirement")
            args = dict(
                run_id="run-1", observation_key="same", entity_type="run", entity_id="run-1",
                phase="controller", status="started", scope="run", unit="count",
            )
            db.record_runtime_observation(**args)
            with self.assertRaises(ValueError):
                db.record_runtime_observation(**{**args, "status": "completed"})
            db.close()

    def test_cli_records_structured_observation_and_keeps_legacy_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.db"
            controller = Path(__file__).parents[1] / "scripts" / "controller.py"
            command = [sys.executable, str(controller), "--db", str(path)]
            subprocess.run(command + ["init", "--run-id", "run-1", "--initiative", "demo", "--requirement", "req"], check=True, capture_output=True, text=True)
            result = subprocess.run(command + [
                "record-observation", "--run-id", "run-1", "--entity-type", "action",
                "--entity-id", "a1", "--operation", "verify", "--status", "completed",
                "--observation-key", "run-1:a1", "--phase", "verification",
                "--duration-ms", "12.5", "--usage", '{"input_tokens":"unknown"}',
            ], check=True, capture_output=True, text=True)
            self.assertTrue(json.loads(result.stdout)["created"])
            db = ControlDB(path)
            self.assertEqual("verification", db.conn.execute("SELECT phase FROM runtime_observations").fetchone()[0])
            db.close()


if __name__ == "__main__":
    unittest.main()
