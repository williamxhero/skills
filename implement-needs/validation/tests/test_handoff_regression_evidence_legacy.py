import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "qualification-whole-spec-v1-20260921-handofffix"
SCHEMA_PATH = REPORT_DIR / "handoff-regression-evidence.schema.json"
EXAMPLE_PATH = REPORT_DIR / "handoff-regression-evidence.example.json"


class LegacyHandoffRegressionEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema, format_checker=Draft202012Validator.FORMAT_CHECKER)

    def test_example_matches_run_scoped_schema(self) -> None:
        self.validator.validate(self.example)

    def test_wrong_run_marker_is_rejected(self) -> None:
        evidence = copy.deepcopy(self.example)
        evidence["run_marker"] = "qualification-whole-spec-v1-wrong-run"
        with self.assertRaises(ValidationError):
            self.validator.validate(evidence)

    def test_ticket_level_artifacts_are_rejected(self) -> None:
        evidence = copy.deepcopy(self.example)
        evidence["whole_spec_invariants"]["ticket_level_pr_count"] = 1
        with self.assertRaises(ValidationError):
            self.validator.validate(evidence)

    def test_out_of_scope_ticket_is_rejected(self) -> None:
        evidence = copy.deepcopy(self.example)
        evidence["ticket_issues"][1]["number"] = 150
        evidence["ticket_issues"][1]["url"] = "https://github.com/williamxhero/skills/issues/150"
        with self.assertRaises(ValidationError):
            self.validator.validate(evidence)


if __name__ == "__main__":
    unittest.main()
