import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCENARIO = ROOT / "scripts" / "recovery_scenario.py"

sys.path.insert(0, str(ROOT / "scripts"))
from recovery_scenario import run_scenario


class RecoveryScenarioTests(unittest.TestCase):
    def test_public_cli_scenario_reaches_successor_after_two_restarts(self):
        result = run_scenario()
        self.assertTrue(result["public_cli"])
        self.assertEqual(2, result["process_restarts"])
        self.assertEqual(3, result["spec_count"])
        self.assertTrue(result["final_frontier"]["successor_reached"])
        self.assertTrue(result["recovery_results"]["controller_interrupted"]["detected"])
        self.assertTrue(result["recovery_results"]["controller_interrupted"]["executed"])
        self.assertTrue(result["recovery_results"]["stream_disconnect"]["executed"])
        self.assertTrue(result["recovery_results"]["capacity_fallback"]["executed"])
        self.assertTrue(result["recovery_results"]["uncertain_side_effect"]["executed"])
        self.assertFalse(result["duplicate_action_keys"])

    def test_cli_writes_machine_readable_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "recovery.json"
            completed = subprocess.run(
                [sys.executable, str(SCENARIO), "--output", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("whole-spec-recovery-v1", payload["scenario_version"])
            self.assertTrue(payload["recovery_execution"])


if __name__ == "__main__":
    unittest.main()
