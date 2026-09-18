import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB
from runtime_metrics import build_metrics


class RuntimeMetricsTests(unittest.TestCase):
    def test_metrics_are_deterministic_and_keep_unknown_usage_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run-1", "demo", "requirement")
            common = dict(run_id="run-1", entity_type="action", scope="run", unit="milliseconds", source="test")
            db.record_runtime_observation(
                **common, observation_key="a", entity_id="a", phase="controller_turn",
                status="completed", duration_ms=10, metadata={"role": "controller", "first_acceptance": True, "evidence": ["test://a"]},
            )
            db.record_runtime_observation(
                **common, observation_key="b", entity_id="b", phase="external_wait",
                status="completed", duration_ms=20, usage={"input_tokens": "unknown"},
                metadata={"retry": True, "recovery_success": True, "evidence": ["test://b"]},
            )
            db.record_runtime_observation(
                **common, observation_key="c", entity_id="c", phase="worker_turn",
                status="completed", duration_ms=30, metadata={"role": "worker", "first_acceptance": False, "duplicate_external_operation": True},
            )
            before = db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            first = build_metrics(db, "run-1", "baseline-1")
            second = build_metrics(db, "run-1", "baseline-1")
            self.assertEqual(first, second)
            self.assertEqual(before, db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            self.assertEqual(60.0, first["metrics"]["total_duration_ms"]["value"])
            self.assertEqual(20.0, first["metrics"]["waiting_duration_ms"]["value"])
            self.assertEqual(1, first["metrics"]["controller_turns"])
            self.assertEqual(1, first["metrics"]["worker_turns"])
            self.assertEqual(1, first["metrics"]["retry_count"])
            self.assertEqual(1, first["metrics"]["duplicate_external_operations"])
            self.assertEqual(1 / 2, first["metrics"]["first_acceptance_rate"]["value"])
            self.assertTrue(first["metrics"]["usage"]["input_tokens"]["unknown"])
            self.assertEqual(["test://a", "test://b"], first["source"]["evidence"])
            db.close()

    def test_empty_run_reports_unknown_rates_and_percentiles(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run-1", "demo", "requirement")
            result = build_metrics(db, "run-1")
            self.assertTrue(result["metrics"]["total_duration_ms"]["unknown"])
            self.assertTrue(result["metrics"]["first_acceptance_rate"]["unknown"])
            self.assertTrue(result["metrics"]["duration_percentiles_ms"]["unknown"])
            db.close()


if __name__ == "__main__":
    unittest.main()
