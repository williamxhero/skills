import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from context_projection import build_context, measure_context
from control_db import ControlDB


class ContextMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "run.db"
        self.db = ControlDB(self.path)
        self.addCleanup(self.db.close)
        self.db.create_run("run", "initiative", "requirement")
        self.db.add_spec("run", "S1", "First", 1, expected_version=self.db.business_version("run"))

    def test_measurement_is_explicit_about_unknown_provider_data_and_does_not_advance_run(self):
        context = build_context(self.db, "run", "implementation", "spec", "S1")
        before = self.db.business_version("run")
        measurement = measure_context(context, latency_ms=1.5, refresh_count=2, rejection_count=1)
        saved = self.db.record_context_measurement("run", context, measurement)
        self.assertEqual(before, saved["business_version"])
        self.assertGreater(measurement["payload_bytes"], 0)
        self.assertGreater(measurement["token_estimate"], 0)
        self.assertEqual("estimated", measurement["coverage"]["tokens"])
        self.assertEqual("unknown", measurement["coverage"]["fees"])
        self.assertEqual(before, self.db.business_version("run"))

    def test_cli_measurement_and_readback_survive_restart(self):
        controller = Path(__file__).parents[1] / "scripts" / "controller.py"
        expected_version = self.db.business_version("run")
        self.db.close()
        measured = subprocess.run(
            [sys.executable, str(controller), "--db", str(self.path), "measure-context", "--run-id", "run", "--phase", "implementation", "--entity-type", "spec", "--entity-id", "S1", "--refresh-count", "2", "--rejection-count", "1"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, measured.returncode, measured.stderr)
        packet = json.loads(measured.stdout)
        self.assertEqual(expected_version, packet["business_version"])
        listed = subprocess.run(
            [sys.executable, str(controller), "--db", str(self.path), "context-measurements", "--run-id", "run"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, listed.returncode, listed.stderr)
        measurements = json.loads(listed.stdout)["measurements"]
        self.assertEqual(1, len(measurements))
        self.assertEqual({"estimated", "unknown"}, {measurements[0]["coverage"]["tokens"], measurements[0]["coverage"]["fees"]})
        reopened = ControlDB.open_existing(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(1, len(reopened.context_measurements("run")))
        self.assertEqual(expected_version, reopened.business_version("run"))


if __name__ == "__main__":
    unittest.main()
