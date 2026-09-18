import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from control_db import ActionConflict, ControlDB


class ClaimRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = ControlDB(Path(self.temp.name) / "run.db")
        self.addCleanup(self.db.close)
        self.db.create_run("run", "initiative", "requirement")
        self.intent = self.db.prepare_intent("run", "deploy", "service", "v1", self.db.business_version("run"))

    def test_active_lease_blocks_second_owner_and_expired_takeover_requires_fencing(self):
        self.db.claim_intent(self.intent["intent_id"], "worker-a", "9999-12-31T00:00:00+00:00", False, None, self.db.business_version("run"))
        with self.assertRaises(ActionConflict):
            self.db.claim_intent(self.intent["intent_id"], "worker-b", "9999-12-31T00:00:00+00:00", False, None, self.db.business_version("run"))

    def test_recovery_budget_pauses_intent_without_new_run(self):
        first = self.db.record_recovery(self.intent["intent_id"], "controller", "transient", 1, {"evidence": "first"}, self.db.business_version("run"))
        self.assertEqual(0, first["remaining"])
        paused = self.db.record_recovery(self.intent["intent_id"], "controller", "transient", 1, {"evidence": "exhausted"}, self.db.business_version("run"))
        self.assertEqual("paused", paused["status"])
        self.assertEqual("paused", self.db.intent(self.intent["intent_id"])["status"])
        self.assertEqual(1, self.db.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
