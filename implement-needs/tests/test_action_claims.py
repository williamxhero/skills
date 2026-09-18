import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ActionClaimConflict, ControlDB, UnsafeLeaseTakeover
from resource_coordinator import ResourceCoordinator


class ActionClaimTests(unittest.TestCase):
    def test_two_independent_connections_allow_one_action_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.db"
            first = ControlDB(path)
            first.create_run("run-1", "demo", "req")
            action_id = first.set_action("run-1", "merge", "pr-1")
            second = ControlDB(path)
            claim = first.claim_action(action_id, "controller-a")
            self.assertEqual("controller-a", claim["owner_id"])
            with self.assertRaises(ActionClaimConflict):
                second.claim_action(action_id, "controller-b")
            self.assertEqual("running", second.conn.execute("SELECT status FROM actions WHERE action_id=?", (action_id,)).fetchone()[0])
            self.assertEqual(1, second.conn.execute("SELECT COUNT(*) FROM events WHERE event_type='action_claimed'").fetchone()[0])
            first.close()
            second.close()

    def test_expired_action_claim_cannot_take_over_without_fencing_and_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.db"
            first = ControlDB(path)
            first.create_run("run-1", "demo", "req")
            action_id = first.set_action("run-1", "archive", "task-1")
            first.claim_action(action_id, "controller-a")
            first.conn.execute("UPDATE action_claims SET lease_expires_at='2000-01-01T00:00:00+00:00'")
            second = ControlDB(path)
            with self.assertRaises(UnsafeLeaseTakeover):
                second.claim_action(action_id, "controller-b")
            claim = second.claim_action(action_id, "controller-b", supports_fencing=True, outcome_reconciled=True)
            self.assertEqual("controller-b", claim["owner_id"])
            first.close()
            second.close()

    def test_effect_requires_current_owner_and_unexpired_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run-1", "demo", "req")
            action_id = db.set_action("run-1", "create", "task-1")
            db.claim_action(action_id, "controller-a")
            self.assertEqual("controller-a", db.assert_action_effect_permitted(action_id, "controller-a")["owner_id"])
            with self.assertRaises(ActionClaimConflict):
                db.assert_action_effect_permitted(action_id, "controller-b")
            db.conn.execute("UPDATE action_claims SET lease_expires_at='2000-01-01T00:00:00+00:00'")
            with self.assertRaises(UnsafeLeaseTakeover):
                db.assert_action_effect_permitted(action_id, "controller-a")
            db.close()

    def test_shared_resource_claims_coordinate_separate_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator_path = Path(directory) / "coordination.db"
            first = ResourceCoordinator(coordinator_path)
            second = ResourceCoordinator(coordinator_path)
            claim = first.acquire("repo:https://github.com/acme/example#main", "controller-a", "run-a")
            self.assertEqual("run-a", claim["run_id"])
            with self.assertRaises(ActionClaimConflict):
                second.acquire("repo:https://github.com/acme/example#main", "controller-b", "run-b")
            first.conn.execute("UPDATE resource_claims SET lease_expires_at='2000-01-01T00:00:00+00:00'")
            with self.assertRaises(UnsafeLeaseTakeover):
                second.acquire("repo:https://github.com/acme/example#main", "controller-b", "run-b")
            replacement = second.acquire("repo:https://github.com/acme/example#main", "controller-b", "run-b", supports_fencing=True, outcome_reconciled=True)
            self.assertEqual("run-b", replacement["run_id"])
            first.close()
            second.close()


if __name__ == "__main__":
    unittest.main()
