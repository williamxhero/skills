import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from control_db import ControlDB, StaleState
from controller_recovery import ControllerRecoveryError, complete_child_and_persist_next_action


class AtomicCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = ControlDB(Path(self.temp.name) / "run.db")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.db.close)
        self.db.create_run("run", "initiative", "requirement")
        self.db.add_spec("run", "S1", "closed child", 1)
        self.db.add_spec("run", "S2", "successor", 2, blocked_by=["S1"])
        self.db.conn.execute("UPDATE specs SET status='closed' WHERE spec_id='S1'")
        self.db.conn.execute("UPDATE runs SET run_phase='implementing' WHERE run_id='run'")

    def test_completion_and_next_action_share_one_business_event(self):
        before = self.db.business_version("run")
        result = complete_child_and_persist_next_action(
            self.db,
            "run",
            child_kind="spec",
            child_id="S1",
            next_action={"kind": "advance_spec", "target": "S2", "next_status": "ready"},
            result={"status": "closed", "readback": "sqlite://spec/S1"},
            expected_version=before,
        )
        self.assertTrue(result["created"])
        self.assertEqual(before + 1, result["business_version"])
        self.assertEqual(1, self.db.conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='spec_completed_next_action_persisted'"
        ).fetchone()[0])
        action = self.db.conn.execute("SELECT kind,target,status FROM actions").fetchone()
        self.assertEqual(("advance_spec", "S2", "pending"), tuple(action))

    def test_repeating_completion_is_idempotent_without_new_event(self):
        version = self.db.business_version("run")
        first = complete_child_and_persist_next_action(
            self.db, "run", child_kind="spec", child_id="S1",
            next_action={"kind": "advance_spec", "target": "S2"},
            expected_version=version,
        )
        after_first = self.db.business_version("run")
        second = complete_child_and_persist_next_action(
            self.db, "run", child_kind="spec", child_id="S1",
            next_action={"kind": "advance_spec", "target": "S2"},
            expected_version=after_first,
        )
        self.assertEqual(first["action"]["action_id"], second["action"]["action_id"])
        self.assertFalse(second["created"])
        self.assertEqual(after_first, second["business_version"])
        self.assertEqual(1, self.db.conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='spec_completed_next_action_persisted'"
        ).fetchone()[0])

    def test_stale_completion_does_not_mutate_child_or_run(self):
        stale = self.db.business_version("run")
        self.db.event("run", "test", "write", "unrelated_change", {})
        before = self.db.snapshot("run")
        with self.assertRaises(StaleState):
            complete_child_and_persist_next_action(
                self.db, "run", child_kind="spec", child_id="S1",
                next_action={"kind": "advance_spec", "target": "S2"},
                expected_version=stale,
            )
        after = self.db.snapshot("run")
        self.assertEqual(before["run"], after["run"])
        self.assertEqual(before["actions"], after["actions"])

    def test_non_terminal_child_is_rejected_without_action(self):
        self.db.conn.execute("UPDATE specs SET status='ready' WHERE spec_id='S1'")
        with self.assertRaisesRegex(ControllerRecoveryError, "child_not_terminal"):
            complete_child_and_persist_next_action(
                self.db, "run", child_kind="spec", child_id="S1",
                next_action={"kind": "advance_spec", "target": "S2"},
                expected_version=self.db.business_version("run"),
            )
        self.assertEqual([], self.db.snapshot("run")["actions"])


if __name__ == "__main__":
    unittest.main()
