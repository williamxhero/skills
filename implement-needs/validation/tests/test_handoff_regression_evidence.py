from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


VALIDATION_ROOT = Path(__file__).resolve().parents[1]
REPORTS_ROOT = VALIDATION_ROOT / "reports"
SCHEMA_PATH = REPORTS_ROOT / "handoff-regression-evidence.schema.v1.json"
EXAMPLE_PATH = REPORTS_ROOT / "qualification-whole-spec-v1-20260921-handofffix-spec1.example.json"


class HandoffRegressionEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)

    def test_run_example_matches_the_handoff_evidence_contract(self) -> None:
        self.validator.validate(self.example)

    def test_scope_is_limited_to_spec_1_and_its_three_tickets(self) -> None:
        self.assertEqual(
            {"repository": "williamxhero/skills", "spec_issue": 149, "ticket_issues": [152, 153, 154]},
            self.example["scope"],
        )

    def test_missing_archive_readback_is_rejected(self) -> None:
        invalid = copy.deepcopy(self.example)
        invalid["child_cleanup"]["archive_readback"] = False
        errors = list(self.validator.iter_errors(invalid))
        self.assertTrue(errors)
        self.assertIn("True was expected", errors[0].message)

    def test_successor_cannot_start_before_child_archive_readback(self) -> None:
        invalid = copy.deepcopy(self.example)
        invalid["successor_gate"]["next_spec_started"] = True
        errors = list(self.validator.iter_errors(invalid))
        self.assertTrue(errors)
        self.assertIn("False was expected", errors[0].message)


if __name__ == "__main__":
    unittest.main()
