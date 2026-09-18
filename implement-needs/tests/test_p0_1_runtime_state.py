import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB
from run_state import RunStateError


def phase_receipt(db, run_id, from_phase, to_phase):
    return {
        "run_id": run_id,
        "from_phase": from_phase,
        "to_phase": to_phase,
        "status": "verified",
        "business_version": db.business_version(run_id),
        "evidence": [f"phase://{to_phase}/verified"],
    }


def result_receipt(db, run_id, phase, result, reason, recovery_action=None):
    value = {
        "run_id": run_id,
        "phase": phase,
        "result": result,
        "status": "verified",
        "business_version": db.business_version(run_id),
        "reason": reason,
        "evidence": [f"result://{result}/verified"],
    }
    if recovery_action is not None:
        value["recovery_action"] = recovery_action
    return value


class RuntimeStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "run.db"
        self.db = ControlDB(self.path)
        self.addCleanup(self.db.close)
        self.db.create_run("run", "initiative", "requirement")

    def advance(self, from_phase, to_phase):
        return self.db.advance_run_phase(
            "run", to_phase, phase_receipt(self.db, "run", from_phase, to_phase),
            self.db.business_version("run"),
        )

    def test_new_run_is_initialized_and_empty_run_cannot_release(self):
        snapshot = self.db.snapshot("run")
        self.assertEqual("initialized", snapshot["run"]["run_phase"])
        self.advance("initialized", "preflight_passed")
        self.advance("preflight_passed", "grilling")
        self.advance("grilling", "planning")
        self.assertEqual("planning", self.db.snapshot("run")["run"]["run_phase"])

    def test_invalid_jump_and_stale_receipt_are_atomic(self):
        before = self.db.snapshot("run")
        with self.assertRaises(RunStateError) as raised:
            self.db.advance_run_phase("run", "release", phase_receipt(self.db, "run", "initialized", "release"))
        self.assertEqual("illegal_run_phase_transition", raised.exception.code)
        after_jump = self.db.snapshot("run")
        self.assertEqual(before["run"]["business_version"], after_jump["run"]["business_version"])
        self.assertEqual(len(before["events"]), len(after_jump["events"]))

        stale = phase_receipt(self.db, "run", "initialized", "preflight_passed")
        stale["business_version"] = -1
        with self.assertRaises(RunStateError) as raised:
            self.db.advance_run_phase("run", "preflight_passed", stale)
        self.assertEqual("phase_receipt_stale", raised.exception.code)
        self.assertEqual(before["run"]["business_version"], self.db.business_version("run"))

    def test_verified_no_change_is_distinct_and_survives_restart(self):
        self.advance("initialized", "preflight_passed")
        self.advance("preflight_passed", "grilling")
        self.advance("grilling", "planning")
        receipt = result_receipt(self.db, "run", "planning", "no_change", "repository already satisfies requirement")
        result = self.db.set_run_result("run", "no_change", receipt["reason"], receipt, self.db.business_version("run"))
        self.assertEqual("no_change", result["result"])
        self.assertEqual("no_change", self.db.snapshot("run")["run"]["terminal_result"])

        self.db.close()
        reopened = ControlDB.open_existing(self.path)
        self.addCleanup(reopened.close)
        persisted = reopened.snapshot("run")["run"]
        self.assertEqual("planning", persisted["run_phase"])
        self.assertEqual("no_change", persisted["terminal_result"])
        self.assertIn("already satisfies", persisted["stop_reason"])

    def test_blocked_run_retains_phase_and_can_resume(self):
        self.advance("initialized", "preflight_passed")
        receipt = result_receipt(self.db, "run", "preflight_passed", "blocked", "provider unavailable", "retry preflight")
        self.db.set_run_result("run", "blocked", receipt["reason"], receipt, self.db.business_version("run"))
        blocked = self.db.snapshot("run")["run"]
        self.assertEqual("preflight_passed", blocked["run_phase"])
        self.assertEqual("blocked", blocked["terminal_result"])
        self.assertEqual("blocked", self.db.snapshot("run")["run"]["terminal_result"])
        self.db.resume_run("run", self.db.business_version("run"))
        self.assertEqual("preflight_passed", self.db.snapshot("run")["run"]["run_phase"])


if __name__ == "__main__":
    unittest.main()
