from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


VALIDATION_ROOT = Path(__file__).resolve().parents[1]
REPORTS_ROOT = VALIDATION_ROOT / "reports"
SCHEMA_PATH = REPORTS_ROOT / "handoff-regression-evidence.schema.v1.json"
EVIDENCE_PATH = (
    REPORTS_ROOT
    / "qualification-whole-spec-v1-20260921-handofffix-spec2.example.json"
)


class Spec2HandoffRegressionEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)

    def assert_rejected(self, payload: dict) -> None:
        self.assertTrue(list(self.validator.iter_errors(payload)))

    def test_spec_2_artifact_matches_the_successful_handoff_contract(self) -> None:
        self.validator.validate(self.evidence)
        self.assertEqual(
            {
                "repository": "williamxhero/skills",
                "spec_issue": 150,
                "ticket_issues": [155, 156, 157],
            },
            self.evidence["scope"],
        )

    def test_parent_empty_completed_turn_is_classified_for_reconciliation(self) -> None:
        self.assertEqual(
            {
                "turn_status": "completed",
                "persisted_output_count": 0,
                "persisted_item_count": 0,
                "classified_as_uncertain": True,
            },
            self.evidence["parent_continuation"],
        )
        invalid = copy.deepcopy(self.evidence)
        invalid["parent_continuation"]["classified_as_uncertain"] = False
        self.assert_rejected(invalid)

    def test_external_effects_and_controller_action_require_readback(self) -> None:
        self.assertTrue(
            self.evidence["external_effect_reconciliation"]["github_effects_read_back"]
        )
        self.assertTrue(
            self.evidence["external_effect_reconciliation"][
                "pull_request_effects_read_back"
            ]
        )
        self.assertEqual(
            "succeeded", self.evidence["controller_action"]["persisted_status"]
        )
        for section, field in (
            ("external_effect_reconciliation", "github_effects_read_back"),
            ("controller_action", "pending_action_reconciled"),
        ):
            invalid = copy.deepcopy(self.evidence)
            invalid[section][field] = False
            self.assert_rejected(invalid)

    def test_child_archive_must_be_requested_and_read_back(self) -> None:
        self.assertEqual(
            {
                "archive_requested": True,
                "archive_readback": True,
                "evidence_refs": [
                    "qualification://successful-child-handoff/spec-2/archive/request",
                    "qualification://successful-child-handoff/spec-2/archive/readback",
                ],
            },
            self.evidence["child_cleanup"],
        )
        invalid = copy.deepcopy(self.evidence)
        invalid["child_cleanup"]["archive_readback"] = False
        self.assert_rejected(invalid)

    def test_spec_and_ticket_scope_cannot_be_cross_wired(self) -> None:
        invalid = copy.deepcopy(self.evidence)
        invalid["scope"]["ticket_issues"] = [152, 153, 154]
        self.assert_rejected(invalid)


if __name__ == "__main__":
    unittest.main()
