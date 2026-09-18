import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
CONTROLLER = ROOT / "scripts" / "controller.py"


class RuntimeCliRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "run.db"

    def cli(self, *args):
        completed = subprocess.run(
            [sys.executable, str(CONTROLLER), "--db", str(self.db), *args],
            capture_output=True, text=True, check=False,
        )
        self.assertTrue(completed.stdout.strip(), completed.stderr)
        return completed, json.loads(completed.stdout)

    def snapshot(self):
        completed, payload = self.cli("snapshot", "--run-id", "run")
        self.assertEqual(0, completed.returncode, completed.stderr)
        return payload

    def phase(self, from_phase, to_phase):
        version = self.snapshot()["run"]["business_version"]
        receipt = {
            "run_id": "run", "from_phase": from_phase, "to_phase": to_phase,
            "status": "verified", "business_version": version,
            "evidence": [f"cli://phase/{to_phase}"],
        }
        completed, payload = self.cli(
            "run-phase", "--run-id", "run", "--phase", to_phase,
            "--receipt", json.dumps(receipt), "--expected-version", str(version),
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        return payload

    def test_cli_rejects_stale_phase_receipt_without_mutation(self):
        completed, payload = self.cli(
            "init", "--run-id", "run", "--initiative", "initiative", "--requirement", "requirement",
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        before = self.snapshot()
        receipt = {
            "run_id": "run", "from_phase": "initialized", "to_phase": "preflight_passed",
            "status": "verified", "business_version": 0, "evidence": ["cli://stale"],
        }
        completed, rejected = self.cli(
            "run-phase", "--run-id", "run", "--phase", "preflight_passed",
            "--receipt", json.dumps(receipt), "--expected-version", str(before["run"]["business_version"]),
        )
        self.assertEqual(1, completed.returncode)
        self.assertEqual("reject", rejected["decision"])
        self.assertEqual("phase_receipt_stale", rejected["error"])
        after = self.snapshot()
        self.assertEqual(before["run"], after["run"])
        self.assertEqual(len(before["events"]), len(after["events"]))

    def test_cli_empty_run_reaches_only_verified_no_change_after_restart(self):
        completed, _ = self.cli(
            "init", "--run-id", "run", "--initiative", "initiative", "--requirement", "requirement",
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("run_preflight", self.cli("next-action", "--run-id", "run")[1]["kind"])
        self.phase("initialized", "preflight_passed")
        self.phase("preflight_passed", "grilling")
        self.phase("grilling", "planning")
        completed, action = self.cli("next-action", "--run-id", "run")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("confirm_no_change", action["kind"])

        version = self.snapshot()["run"]["business_version"]
        receipt = {
            "run_id": "run", "phase": "planning", "result": "no_change",
            "status": "verified", "business_version": version,
            "reason": "requirement already satisfied", "evidence": ["cli://no-change"],
        }
        completed, result = self.cli(
            "run-result", "--run-id", "run", "--result", "no_change",
            "--reason", receipt["reason"], "--receipt", json.dumps(receipt),
            "--expected-version", str(version),
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("no_change", result["result"])
        terminal = self.snapshot()["run"]
        self.assertEqual("no_change", terminal["terminal_result"])
        completed, action = self.cli("next-action", "--run-id", "run")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual({"kind": "terminal", "target": "run", "result": "no_change", "phase": "planning"}, action)

        reopened = self.snapshot()
        self.assertEqual("planning", reopened["run"]["run_phase"])
        self.assertEqual("no_change", reopened["run"]["terminal_result"])


if __name__ == "__main__":
    unittest.main()
