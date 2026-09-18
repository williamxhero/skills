import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB


POLICY = {"name": "safe-policy", "rules": ["no-unbounded-scope"], "version": 1}


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = ControlDB(Path(self.temp.name) / "run.db")
        self.addCleanup(self.db.close)
        self.db.create_run("run", "initiative", "requirement")

    def test_same_policy_pin_is_idempotent_and_loaded_digest_is_verified(self):
        first = self.db.pin_policy("run", POLICY, "impl-a", expected_version=self.db.business_version("run"))
        second = self.db.pin_policy("run", POLICY, "impl-a", expected_version=self.db.business_version("run"))
        self.assertFalse(second["changed"])
        self.assertEqual(first["policy_digest"], second["policy_digest"])
        self.assertEqual("allow", self.db.verify_policy("run", POLICY, "impl-a")["decision"])
        with self.assertRaises(ValueError):
            self.db.verify_policy("run", {**POLICY, "version": 2}, "impl-a")

    def test_different_policy_requires_migration_evidence(self):
        self.db.pin_policy("run", POLICY, "impl-a", expected_version=self.db.business_version("run"))
        with self.assertRaises(ValueError) as raised:
            self.db.pin_policy("run", {**POLICY, "version": 2}, "impl-b", expected_version=self.db.business_version("run"))
        self.assertEqual("policy_migration_evidence_required", str(raised.exception))
        migrated = self.db.pin_policy("run", {**POLICY, "version": 2}, "impl-b", expected_version=self.db.business_version("run"), migration={"compatibility": "compatible", "authorization": "approved", "rollback": "impl-a"})
        self.assertTrue(migrated["changed"])


if __name__ == "__main__":
    unittest.main()
