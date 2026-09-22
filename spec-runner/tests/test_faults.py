from __future__ import annotations

import unittest

from spec_runner.faults import run_fault_matrix


class FaultMatrixTests(unittest.TestCase):
    def test_public_cli_fault_matrix_is_replayable_and_separates_live_gaps(self):
        report = run_fault_matrix(seed="test-seed")
        self.assertTrue(report["passed"])
        self.assertEqual(report["evidence_kind"], "deterministic")
        self.assertTrue(any(item["kind"] == "live_sdk" for item in report["unverified"]))
        self.assertEqual([case["id"] for case in report["cases"]], ["normal_two_stage", "completed_drive_is_idempotent", "input_drift_rejected", "completed_cancel_does_not_reopen"])
