"""Regression for the SF-02 independent review delivery gate."""

from __future__ import annotations

import json
import unittest

from spec_runner.codex_adapter import CodexWorkerResult
from spec_runner.production_gates import independent_review


class IndependentReviewGateTests(unittest.TestCase):
    def _review(self, findings: list[dict[str, str]]) -> dict[str, object]:
        result = CodexWorkerResult(
            thread_id="independent-reviewer",
            turn_id="review-turn",
            status="completed",
            error=None,
            final_response=json.dumps({
                "schema_version": "spec-runner-review-result/v1",
                "candidate_sha": "candidate-sha",
                "acceptance_version": "acceptance-v1",
                "findings": findings,
            }),
            item_count=1,
            started_at=1,
            completed_at=2,
        )
        return independent_review(
            result,
            implementation_thread="implementation-owner",
            candidate_sha="candidate-sha",
            acceptance_version="acceptance-v1",
        )

    def test_open_contract_defect_requires_repair(self) -> None:
        finding = {
            "severity": "medium",
            "status": "open",
            "description": "JSON output is not UTF-8 under a non-UTF-8 locale, violating acceptance.",
        }
        review = self._review([finding])
        self.assertFalse(review["approved"])
        self.assertEqual(review["blocking"], [finding])

    def test_resolved_defect_and_low_severity_note_allow_delivery(self) -> None:
        review = self._review([
            {"severity": "medium", "status": "resolved", "description": "UTF-8 fixed."},
            {"severity": "info", "status": "open", "description": "Documentation could be clearer."},
        ])
        self.assertTrue(review["approved"])
        self.assertEqual(review["blocking"], [])


if __name__ == "__main__":
    unittest.main()
