import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
CONTROLLER = ROOT / "scripts" / "controller.py"


class DeliveryReceiptCLITests(unittest.TestCase):
    def test_public_record_and_projection_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "run.db"
            call = lambda *args: json.loads(subprocess.run([sys.executable, str(CONTROLLER), "--db", str(db), *args], check=True, capture_output=True, text=True).stdout)
            call("init", "--run-id", "r", "--initiative", "demo", "--requirement", "req")
            value = {"repository":"repo", "target_sha":"a" * 40, "target_ref":"refs/heads/main", "test_plan":"unit", "test_selection":"all", "environment_fingerprint":"env", "acceptance_version":"a1", "validator_version":"v1", "result":"passed", "source_kind":"controller_ci", "provenance":"ci:1", "source_uri":"log://1", "observed_at":"2026-09-18T00:00:00+00:00"}
            call("record-delivery-receipt", "--run-id", "r", "--entity-type", "run", "--entity-id", "r", "--receipt", json.dumps(value))
            result = call("validate-delivery-receipt", "--run-id", "r", "--entity-type", "run", "--entity-id", "r", "--expected", json.dumps(value))
            self.assertEqual("allow", result["decision"])


if __name__ == "__main__": unittest.main()
