import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from control_db import ControlDB
from next_action import next_action


class IntentLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = ControlDB(Path(self.temp.name) / "run.db")
        self.addCleanup(self.db.close)
        self.db.create_run("run", "initiative", "requirement")

    def test_identity_includes_target_state_and_repeat_is_idempotent(self):
        first = self.db.prepare_intent("run", "deploy", "service-a", "v1", self.db.business_version("run"))
        repeat = self.db.prepare_intent("run", "deploy", "service-a", "v1", self.db.business_version("run"))
        different = self.db.prepare_intent("run", "deploy", "service-a", "v2", self.db.business_version("run"))
        self.assertFalse(repeat["changed"])
        self.assertNotEqual(first["intent_id"], different["intent_id"])

    def test_success_requires_response_and_authoritative_readback(self):
        intent = self.db.prepare_intent("run", "deploy", "service-a", "v1", self.db.business_version("run"))
        with self.assertRaises(ValueError):
            self.db.record_intent_outcome(intent["intent_id"], "succeeded", {"status": "verified"}, None, self.db.business_version("run"))
        result = self.db.record_intent_outcome(intent["intent_id"], "succeeded", {"status": "verified"}, {"status": "verified", "target": "service-a"}, self.db.business_version("run"))
        self.assertEqual("succeeded", result["status"])

    def test_unknown_result_is_reconciled_before_success_and_is_prioritized(self):
        intent = self.db.prepare_intent("run", "deploy", "service-a", "v1", self.db.business_version("run"))
        self.db.record_intent_outcome(intent["intent_id"], "outcome_unknown", {"status": "response_lost"}, None, self.db.business_version("run"))
        action = next_action(self.db, "run")
        self.assertEqual("reconcile_intent", action["kind"])
        with self.assertRaises(ValueError):
            self.db.record_intent_outcome(intent["intent_id"], "succeeded", {"status": "verified"}, {"status": "verified"}, self.db.business_version("run"))
        result = self.db.reconcile_intent(intent["intent_id"], {"status": "verified", "target": "service-a"}, self.db.business_version("run"))
        self.assertEqual("succeeded", result["status"])


if __name__ == "__main__":
    unittest.main()
