import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from control_db import ControlDB
from dependency_readiness import readiness_from_db
from next_action import next_action


class SchedulerReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = ControlDB(Path(self.temp.name) / "run.db")
        self.addCleanup(self.db.close)
        self.db.create_run("run", "initiative", "requirement")
        self.db.add_spec("run", "S1", "first", 1)
        self.db.add_spec("run", "S2", "second", 2, ["S1"])
        for from_phase, to_phase in (("initialized", "preflight_passed"), ("preflight_passed", "grilling"), ("grilling", "planning")):
            self.db.advance_run_phase("run", to_phase, {"run_id": "run", "from_phase": from_phase, "to_phase": to_phase, "status": "verified", "business_version": self.db.business_version("run"), "evidence": [f"phase://{to_phase}"]}, self.db.business_version("run"))

    def test_readiness_from_db_is_waiting_without_repair_side_effect(self):
        before = self.db.snapshot("run")
        result = readiness_from_db(self.db, "run", "S2")
        self.assertEqual("waiting", result["status"])
        self.assertEqual("S1", result["next_check"])
        self.assertEqual(before["run"]["business_version"], self.db.business_version("run"))
        self.assertEqual(len(before["events"]), len(self.db.snapshot("run")["events"]))

    def test_cancelled_predecessor_is_blocked_without_waiver_and_waiver_releases(self):
        self.db.update_spec("S1", "cancelled", expected_version=self.db.business_version("run"))
        blocked = readiness_from_db(self.db, "run", "S2")
        self.assertEqual("blocked", blocked["status"])
        self.db.record_dependency_waiver("run", "spec", "S1", {"reason": "cancelled but accepted"}, self.db.business_version("run"))
        self.assertEqual("ready", readiness_from_db(self.db, "run", "S2")["status"])

    def test_scheduler_returns_repair_for_structural_error_not_waiting(self):
        self.db.add_spec("run", "S3", "bad", 3, ["MISSING"], expected_version=self.db.business_version("run"))
        self.db.update_spec("S1", "cancelled", expected_version=self.db.business_version("run"))
        self.db.record_dependency_waiver("run", "spec", "S1", {"reason": "accepted"}, self.db.business_version("run"))
        self.db.update_spec("S2", "cancelled", expected_version=self.db.business_version("run"))
        self.db.advance_run_phase("run", "implementing", {"run_id": "run", "from_phase": "planning", "to_phase": "implementing", "status": "verified", "business_version": self.db.business_version("run"), "evidence": ["phase://implementing"]}, self.db.business_version("run"))
        action = next_action(self.db, "run")
        self.assertEqual("repair_spec", action["kind"])
        self.assertEqual("structural_error", action["reason"])


if __name__ == "__main__":
    unittest.main()
