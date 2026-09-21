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
    / "qualification-whole-spec-v1-20260921-handofffix-spec3.validation.json"
)


class Spec3FinalValidationEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)

    def assert_rejected(self, payload: dict) -> None:
        self.assertTrue(list(self.validator.iter_errors(payload)))

    def test_final_artifact_matches_spec_3_scope_and_run_marker(self) -> None:
        self.validator.validate(self.evidence)
        self.assertEqual(
            "qualification-whole-spec-v1-20260921-handofffix",
            self.evidence["run_marker"],
        )
        self.assertEqual(
            {
                "repository": "williamxhero/skills",
                "spec_issue": 151,
                "ticket_issues": [158, 159],
            },
            self.evidence["scope"],
        )

    def test_successful_child_handoff_cleanup_contract_is_complete(self) -> None:
        self.assertEqual("completed", self.evidence["parent_continuation"]["turn_status"])
        self.assertTrue(
            self.evidence["parent_continuation"]["classified_as_uncertain"]
        )
        self.assertTrue(
            self.evidence["external_effect_reconciliation"][
                "github_effects_read_back"
            ]
        )
        self.assertTrue(
            self.evidence["controller_action"]["pending_action_reconciled"]
        )
        self.assertTrue(self.evidence["child_cleanup"]["archive_requested"])
        self.assertTrue(self.evidence["child_cleanup"]["archive_readback"])

        for section, field in (
            ("parent_continuation", "classified_as_uncertain"),
            ("external_effect_reconciliation", "github_effects_read_back"),
            ("external_effect_reconciliation", "pull_request_effects_read_back"),
            ("controller_action", "pending_action_reconciled"),
            ("child_cleanup", "archive_requested"),
            ("child_cleanup", "archive_readback"),
        ):
            invalid = copy.deepcopy(self.evidence)
            invalid[section][field] = False
            self.assert_rejected(invalid)

    def test_finalization_placeholders_are_explicit_and_required(self) -> None:
        self.assertEqual(
            {"cleanup", "release", "repository_sync"},
            set(self.evidence["finalization_evidence"]),
        )
        for section in ("cleanup", "release", "repository_sync"):
            self.assertEqual(
                "pending_controller_readback",
                self.evidence["finalization_evidence"][section]["status"],
            )
            invalid = copy.deepcopy(self.evidence)
            del invalid["finalization_evidence"][section]
            self.assert_rejected(invalid)

    def test_spec_3_scope_cannot_be_cross_wired(self) -> None:
        for spec_issue, ticket_issues in (
            (150, [158, 159]),
            (151, [155, 156, 157]),
        ):
            invalid = copy.deepcopy(self.evidence)
            invalid["scope"]["spec_issue"] = spec_issue
            invalid["scope"]["ticket_issues"] = ticket_issues
            self.assert_rejected(invalid)

    def test_spec_3_requires_final_validation_contract(self) -> None:
        invalid = copy.deepcopy(self.evidence)
        invalid["fixture_status"] = "contract_example"
        self.assert_rejected(invalid)


if __name__ == "__main__":
    unittest.main()
