import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB
from authorization import AuthorizationError
from sync_scope import SyncScopeError, build_sync_plan


def auth(full_project=False):
    actions = ["freeze-candidate", "invalidate-candidate", "run-scoped-sync"]
    if full_project:
        actions.append("full-project-sync")
    return {
        "schema_version": 1,
        "repository": {"id": "repo://skills", "branch": "master"},
        "target_ref": "master",
        "allowed_paths": ["implement-needs/*"],
        "allowed_tasks": ["run"],
        "allowed_actions": actions,
        "deployment_target": "staging",
        "full_project_submission": full_project,
    }


class SyncScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = ControlDB(Path(self.temp.name) / "run.db")
        self.addCleanup(self.db.close)
        self.db.create_run("run", "initiative", "requirement")
        self.db.configure_authorization("run", auth(), self.db.business_version("run"))
        digest = self.db.authorization("run")["authorization_digest"]
        self.candidate = "a" * 40
        freeze = {
            "status": "verified", "candidate_sha": self.candidate,
            "authorization_digest": digest, "business_version": self.db.business_version("run"),
            "evidence": ["freeze://verified"],
        }
        self.db.freeze_candidate("run", self.candidate, freeze, expected_version=self.db.business_version("run"))

    def sync_readback(self, *, candidate=None, full_project=False):
        digest = self.db.authorization("run")["authorization_digest"]
        candidate = candidate or self.candidate
        return {
            "status": "verified", "candidate_sha": candidate,
            "authorization_digest": digest, "target_ref": "master",
            "repository_id": "repo://skills", "local_head": candidate,
            "remote_head": candidate, "full_project": full_project,
            "evidence": ["sync://readback"],
        }

    def test_run_scoped_plan_preserves_unrelated_dirty_paths(self):
        plan = build_sync_plan(self.db.authorization("run")["payload"], ["implement-needs/scripts/a.py", "other-project/untouched.txt"])
        self.assertEqual(["implement-needs/scripts/a.py"], plan["sync_paths"])
        self.assertEqual(["other-project/untouched.txt"], plan["preserved_unrelated"])
        with self.assertRaises(SyncScopeError) as raised:
            build_sync_plan(self.db.authorization("run")["payload"], ["other-project/untouched.txt"], requested_paths=["other-project/untouched.txt"])
        self.assertEqual("sync_scope_expansion_denied", raised.exception.code)

    def test_sync_readback_requires_candidate_equality_and_records_evidence(self):
        before = self.db.business_version("run")
        result = self.db.record_synchronization("run", self.sync_readback(), before)
        self.assertEqual(self.candidate, result["candidate_sha"])
        self.assertEqual(1, len(self.db.candidate_snapshot("run")["evidence"]))
        before = self.db.snapshot("run")
        with self.assertRaises(SyncScopeError) as raised:
            self.db.record_synchronization("run", self.sync_readback(candidate="b" * 40), self.db.business_version("run"))
        self.assertEqual("sync_candidate_mismatch", raised.exception.code)
        after = self.db.snapshot("run")
        self.assertEqual(before["run"]["business_version"], after["run"]["business_version"])
        self.assertEqual(len(before["events"]), len(after["events"]))

    def test_full_project_readback_requires_explicit_permission(self):
        with self.assertRaises(AuthorizationError) as raised:
            self.db.record_synchronization("run", self.sync_readback(full_project=True), self.db.business_version("run"))
        self.assertEqual("authorization_action_denied", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
