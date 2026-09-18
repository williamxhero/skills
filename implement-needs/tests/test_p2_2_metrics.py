import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB
from safety_metrics import BENCHMARK_TASKS, collect_metrics


class SafetyMetricsTests(unittest.TestCase):
    def test_fixed_benchmark_and_unknown_data_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run", "initiative", "requirement")
            before = db.business_version("run")
            report = collect_metrics(db, "run")
            self.assertEqual(len(BENCHMARK_TASKS), len(report["benchmark"]["tasks"]))
            self.assertEqual("unknown", report["coverage"]["tokens"])
            self.assertIsNone(report["efficiency"]["fee"])
            self.assertIsNone(report["behavior"]["duplicate_external_operations"])
            self.assertEqual(before, db.business_version("run"))
            db.close()

    def test_lost_response_is_not_reported_as_success_or_no_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run", "initiative", "requirement")
            intent = db.prepare_intent("run", "deploy", "service", "v1", db.business_version("run"))
            db.record_intent_outcome(intent["intent_id"], "outcome_unknown", {"status": "response_lost"}, expected_version=db.business_version("run"))
            report = collect_metrics(db, "run")
            self.assertEqual(1, report["behavior"]["unknown_outcomes"])
            self.assertIsNone(report["behavior"]["duplicate_external_operations"])
            self.assertNotEqual(True, report["correctness"]["delivery_success"])
            db.close()


if __name__ == "__main__": unittest.main()
