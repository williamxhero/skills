import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB
from delivery_receipts import project, record


def receipt(**overrides):
    value = {"repository": "github.com/acme/repo", "target_sha": "a" * 40, "target_ref": "refs/heads/main", "test_plan": "unit-v2", "test_selection": "tests/core", "environment_fingerprint": "env-a", "acceptance_version": "acceptance-v3", "validator_version": "validator-v2", "result": "passed", "source_kind": "controller_ci", "provenance": "github-actions:run-1", "source_uri": "ci://run/1", "observed_at": "2026-09-18T00:00:00+00:00"}
    value.update(overrides)
    return value


class DeliveryReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.db = ControlDB(Path(self.temp.name) / "run.db")
        self.db.create_run("r", "demo", "req")
    def tearDown(self): self.db.close(); self.temp.cleanup()
    def test_trusted_matching_ci_reuses_and_each_key_change_invalidates(self):
        current = receipt(); record(self.db, "r", "ticket", "T1", current)
        self.assertEqual("allow", project(self.db, "r", "ticket", "T1", current)["decision"])
        for field, replacement in {"target_sha":"b" * 40, "test_plan":"unit-v3", "environment_fingerprint":"env-b", "validator_version":"validator-v3"}.items():
            expected = dict(current); expected[field] = replacement
            result = project(self.db, "r", "ticket", "T1", expected)
            self.assertEqual("reject", result["decision"])
            self.assertIn(f"receipt_{field}_mismatch", result["invalid"])
    def test_worker_claim_and_missing_required_field_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "source_not_trusted"):
            record(self.db, "r", "ticket", "T1", receipt(source_kind="worker_claim"))
        with self.assertRaisesRegex(ValueError, "environment_fingerprint_missing"):
            record(self.db, "r", "ticket", "T1", receipt(environment_fingerprint=""))

if __name__ == "__main__": unittest.main()
