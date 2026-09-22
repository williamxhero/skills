import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from validation.scripts.continuation_scenario import fixture, run_continuation_matrix
from continuation_gate import verify_continuation


class ContinuationTests(unittest.TestCase):
    def test_all_acceptance_boundaries(self):
        for name, row in run_continuation_matrix()["cases"].items():
            with self.subTest(name=name):
                self.assertEqual(row["expected"], row["actual"])

    def test_receipts_are_required(self):
        self.assertEqual("blocked", verify_continuation({})["decision"])
        receipt = fixture()
        receipt["after"]["evidence"] = []
        self.assertIn("continuation_after_evidence_missing", verify_continuation(receipt)["reasons"])

    def test_recovery_frontier_cannot_qualify(self):
        receipt = fixture()
        receipt["after"]["next_action"] = {"kind": "repair_dependency"}
        self.assertIn("continuation_next_action_blocked", verify_continuation(receipt)["reasons"])
