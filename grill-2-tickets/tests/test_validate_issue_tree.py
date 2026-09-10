import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("validate_issue_tree", ROOT / "scripts" / "validate_issue_tree.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class IssueTreeTests(unittest.TestCase):
    def record(self):
        return {
            "schema_version": 1,
            "grill": {"frontier_empty": True, "round_evidence": ["chat://round-1"]},
            "umbrella_spec": {"id": "S0", "artifact": "github://S0"},
            "specs": [{
                "id": "S1", "artifact": "github://S1",
                "parent_issue": {"parent_id": "S0", "evidence": ["github://S1/parent"]},
                "tickets": [{"id": "T1", "artifact": "github://T1", "parent_issue": {"parent_id": "S1", "evidence": ["github://T1/parent"]}}],
            }],
        }

    def test_valid_tree(self):
        self.assertEqual([], MODULE.validate(self.record()))

    def test_wrong_spec_and_ticket_parents_fail(self):
        record = self.record()
        record["specs"][0]["parent_issue"]["parent_id"] = "wrong"
        record["specs"][0]["tickets"][0]["parent_issue"]["parent_id"] = "wrong"
        errors = MODULE.validate(record)
        self.assertTrue(any("umbrella Parent issue" in error for error in errors))
        self.assertTrue(any("owning-SPEC Parent issue" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
