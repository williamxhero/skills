import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB
from fake_runtime_backend import SCENARIOS, run_scenario
from runtime_metrics import build_metrics


class FakeRuntimeBackendTests(unittest.TestCase):
    def test_all_scenarios_are_replayable_and_have_distinct_outcomes(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                db = ControlDB(Path(directory) / "run.db")
                db.create_run("run-1", "fixture", scenario)
                first = run_scenario(db, "run-1", scenario)
                second = run_scenario(db, "run-1", scenario)
                metrics = build_metrics(db, "run-1", "fixture-v1")
                self.assertEqual(len(first), len(second))
                self.assertEqual(len(first), metrics["source"]["observation_count"])
                self.assertEqual(len(first), db.conn.execute("SELECT COUNT(*) FROM runtime_observations").fetchone()[0])
                if scenario == "external-wait":
                    self.assertEqual(250.0, metrics["metrics"]["waiting_duration_ms"]["value"])
                if scenario == "retry":
                    self.assertEqual(1, metrics["metrics"]["retry_count"])
                if scenario == "repair":
                    self.assertEqual(1.0, metrics["metrics"]["recovery_success_rate"]["value"])
                if scenario == "verification-failure":
                    self.assertEqual(0.0, metrics["metrics"]["first_acceptance_rate"]["value"])
                db.close()

    def test_fixture_does_not_change_state_when_metrics_are_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run-1", "fixture", "normal")
            run_scenario(db, "run-1", "normal")
            before = db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            build_metrics(db, "run-1")
            self.assertEqual(before, db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            db.close()


if __name__ == "__main__":
    unittest.main()
