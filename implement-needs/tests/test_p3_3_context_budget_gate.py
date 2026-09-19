import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from context_budget_gate import BUDGET_VERSION, PHASE_BUDGETS, admit_context
from control_db import ControlDB
from phase_context import assemble_context


class ContextBudgetGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "run.db"
        self.db = ControlDB(self.path)
        self.db.create_run("run", "initiative", "requirement")
        self.db.add_spec("run", "S1", "First", 1, acceptance=["acceptance://" + ("x" * 200)])
        assemble_context(self.db, "run", "planning")
        self.snapshot = self.db.read_snapshot("run")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_normal_admission_is_versioned_read_only_and_unknown_provider_data(self):
        before = self.db.business_version("run")
        report = admit_context(self.db, "run", "planning")
        self.assertEqual("allow", report["decision"])
        self.assertEqual(BUDGET_VERSION, report["budget"]["version"])
        self.assertEqual(PHASE_BUDGETS["planning"]["max_bytes"], report["budget"]["max_bytes"])
        self.assertEqual("unknown", report["measurement"]["coverage"]["provider_tokens"])
        self.assertEqual(before, self.db.business_version("run"))

    def test_overflow_uses_delta_fallback_without_dropping_required_facts(self):
        report = admit_context(self.db, "run", "planning", budget={"max_bytes": 300, "max_tokens": 75})
        self.assertEqual("allow", report["decision"])
        self.assertEqual("delta", report["fallback"])
        self.assertEqual("delta", report["context"]["mode"])
        self.assertTrue(report["safety"]["required_fields_preserved"])
        self.assertTrue(report["safety"]["pointer_reachability"])

    def test_impossible_overflow_is_blocked_without_business_side_effect(self):
        before = self.db.business_version("run")
        before_events = self.db.event_cursor("run")
        report = admit_context(self.db, "run", "planning", budget={"max_bytes": 10, "max_tokens": 2})
        self.assertEqual("inconclusive", report["decision"])
        self.assertEqual("blocked", report["fallback"])
        self.assertIsNone(report["context"])
        self.assertEqual(before, self.db.business_version("run"))
        self.assertEqual(before_events, self.db.event_cursor("run"))

    def test_missing_delta_base_is_structured_block(self):
        report = admit_context(self.db, "run", "planning", mode="delta")
        self.assertEqual("blocked", report["decision"])
        self.assertEqual("delta_base_cursor_missing", report["reason"])

    def test_read_only_budget_cli_is_public(self):
        controller = Path(__file__).parents[1] / "scripts" / "controller.py"
        self.db.close()
        result = subprocess.run(
            [sys.executable, str(controller), "--db", str(self.path), "context-budget-gate", "--run-id", "run", "--phase", "planning"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("allow", payload["decision"])
        self.assertEqual(BUDGET_VERSION, payload["budget"]["version"])
        self.db = ControlDB(self.path)


if __name__ == "__main__":
    unittest.main()
