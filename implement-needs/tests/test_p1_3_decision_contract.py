import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from control_db import ControlDB, DecisionError


AUTHORIZATION = {
    "schema_version": 1,
    "repository": {"id": "repo://skills", "branch": "master"},
    "target_ref": "master",
    "allowed_paths": ["implement-needs/*"],
    "allowed_tasks": ["run"],
    "allowed_actions": ["decide"],
    "deployment_target": "staging",
    "full_project_submission": False,
}


class DecisionContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "run.db"
        self.db = ControlDB(self.path)
        self.addCleanup(self.db.close)
        self.db.create_run("run", "initiative", "requirement")
        self.db.configure_authorization("run", AUTHORIZATION, self.db.business_version("run"))

    def approved(self):
        return self.db.authorize("run", action="decide")

    def record(self):
        return self.db.decide(
            "run", "merge_strategy", "merge", "merge", ["repo-rule"],
            "repository default", "controller", {"action": "decide"},
            "repository-default", self.approved(), self.db.business_version("run"),
        )

    def test_public_decision_is_structured_and_audited(self):
        result = self.record()
        self.assertEqual(1, result["decision_id"])
        decision = self.db.snapshot("run")["decisions"][0]
        self.assertEqual("controller", decision["actor"])
        self.assertEqual("repository-default", decision["source"])
        self.assertEqual(self.approved()["authorization_digest"], decision["authorization_digest"])
        self.assertEqual("controller_approved", self.db.snapshot("run")["events"][-1]["event_type"])

    def test_missing_contract_fields_and_authorization_have_no_side_effect(self):
        before = self.db.snapshot("run")
        with self.assertRaises(DecisionError) as raised:
            self.db.decide("run", "subject", "selected", "recommendation", ["evidence"], "rationale", "", {"action": "decide"}, "source", self.approved(), before["run"]["business_version"])
        self.assertEqual("decision_field_missing", raised.exception.code)
        with self.assertRaises(DecisionError) as raised:
            self.db.decide("run", "subject", "selected", "recommendation", ["evidence"], "rationale", "controller", {"action": "decide"}, "source", {}, before["run"]["business_version"])
        self.assertEqual("decision_authorization_required", raised.exception.code)
        after = self.db.snapshot("run")
        self.assertEqual(before["run"]["business_version"], after["run"]["business_version"])
        self.assertEqual(len(before["events"]), len(after["events"]))

    def test_observation_cannot_impersonate_business_evidence(self):
        with self.assertRaises(ValueError):
            self.db.add_observation("run", "run", "run", {"receipt": {"status": "verified"}})
        before = self.db.snapshot("run")
        with self.assertRaises(ValueError):
            self.db.add_observation("run", "run", "run", {"delivery_proof": {"status": "verified"}})
        after = self.db.snapshot("run")
        self.assertEqual(len(before["observations"]), len(after["observations"]))
        self.db.record_receipt("run", "run", "run", {"status": "verified"}, self.db.business_version("run"))
        self.assertEqual("receipt", self.db.snapshot("run")["evidence_refs"][-1]["evidence_kind"])

    def test_decide_cli_is_public_and_returns_structured_result(self):
        controller = Path(__file__).parents[1] / "scripts" / "controller.py"
        approved = self.approved()
        args = [sys.executable, str(controller), "--db", str(self.path), "decide", "--run-id", "run", "--subject", "merge_strategy", "--selected", '"merge"', "--recommendation", '"merge"', "--evidence", '["repo-rule"]', "--rationale", "repository default", "--actor", "controller", "--scope", '{"action":"decide"}', "--source", "repository-default", "--authorization", json.dumps(approved), "--expected-version", str(self.db.business_version("run"))]
        result = subprocess.run(args, capture_output=True, text=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(1, json.loads(result.stdout)["decision_id"])


if __name__ == "__main__":
    unittest.main()
