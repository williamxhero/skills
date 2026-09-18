import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from authorization import AuthorizationError
from control_db import ControlDB


def authorization(*, full_project=False):
    actions = ["run-scoped-sync", "deploy"]
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


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = ControlDB(Path(self.temp.name) / "run.db")
        self.addCleanup(self.db.close)
        self.db.create_run("run", "initiative", "requirement")

    def test_unconfigured_run_rejects_scope_checks(self):
        with self.assertRaises(AuthorizationError) as raised:
            self.db.authorize("run", action="run-scoped-sync")
        self.assertEqual("authorization_unconfigured", raised.exception.code)

    def test_authorization_is_immutable_and_scope_denials_have_no_side_effect(self):
        version = self.db.business_version("run")
        configured = self.db.configure_authorization("run", authorization(), version)
        self.assertTrue(configured["changed"])
        allowed = self.db.authorize("run", action="run-scoped-sync", path="implement-needs/scripts/x.py", target_ref="master")
        self.assertEqual("allow", allowed["decision"])
        before = self.db.snapshot("run")
        with self.assertRaises(AuthorizationError) as raised:
            self.db.authorize("run", action="full-project-sync", full_project=True)
        self.assertEqual("authorization_action_denied", raised.exception.code)
        with self.assertRaises(AuthorizationError) as raised:
            self.db.authorize("run", action="run-scoped-sync", path="other-project/secret.txt")
        self.assertEqual("authorization_path_denied", raised.exception.code)
        after = self.db.snapshot("run")
        self.assertEqual(before["run"]["business_version"], after["run"]["business_version"])
        self.assertEqual(len(before["events"]), len(after["events"]))
        with self.assertRaises(AuthorizationError) as raised:
            self.db.configure_authorization("run", authorization(full_project=True), self.db.business_version("run"))
        self.assertEqual("authorization_immutable", raised.exception.code)

    def test_full_project_requires_explicit_authorization(self):
        self.db.configure_authorization("run", authorization(full_project=True), self.db.business_version("run"))
        result = self.db.authorize("run", action="full-project-sync", full_project=True)
        self.assertEqual("allow", result["decision"])


if __name__ == "__main__":
    unittest.main()
