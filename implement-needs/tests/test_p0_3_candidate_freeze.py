import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB


def auth():
    return {
        "schema_version": 1,
        "repository": {"id": "repo://skills", "branch": "master"},
        "target_ref": "master",
        "allowed_paths": ["implement-needs/*"],
        "allowed_tasks": ["run"],
        "allowed_actions": ["freeze-candidate", "invalidate-candidate", "run-scoped-sync", "deploy"],
        "deployment_target": "staging",
        "full_project_submission": False,
    }


def evidence(db, candidate, kind="freeze"):
    return {
        "status": "verified",
        "candidate_sha": candidate,
        "authorization_digest": db.authorization("run")["authorization_digest"],
        "business_version": db.business_version("run"),
        "evidence": [f"{kind}://verified"],
    }


class CandidateFreezeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = ControlDB(Path(self.temp.name) / "run.db")
        self.addCleanup(self.db.close)
        self.db.create_run("run", "initiative", "requirement")
        self.db.configure_authorization("run", auth(), self.db.business_version("run"))

    def test_freeze_and_all_evidence_share_candidate_and_authorization(self):
        frozen = self.db.freeze_candidate("run", "a" * 40, evidence(self.db, "a" * 40), "merge-a", self.db.business_version("run"))
        self.assertTrue(frozen["changed"])
        for kind in ("test", "package", "deployment", "synchronization"):
            self.db.record_candidate_evidence("run", kind, evidence(self.db, "a" * 40, kind), self.db.business_version("run"))
        snapshot = self.db.candidate_snapshot("run")
        self.assertEqual("active", snapshot["freeze"][0]["status"])
        self.assertEqual({"a" * 40}, {row["candidate_sha"] for row in snapshot["evidence"]})
        self.assertEqual(4, len(snapshot["evidence"]))

    def test_mismatched_evidence_is_rejected_without_reusing_old_candidate(self):
        candidate = "a" * 40
        self.db.freeze_candidate("run", candidate, evidence(self.db, candidate), expected_version=self.db.business_version("run"))
        before = self.db.snapshot("run")
        wrong = evidence(self.db, "b" * 40, "deployment")
        with self.assertRaises(ValueError):
            self.db.record_candidate_evidence("run", "deployment", wrong, self.db.business_version("run"))
        after = self.db.snapshot("run")
        self.assertEqual(before["run"]["business_version"], after["run"]["business_version"])
        self.assertEqual(len(before["events"]), len(after["events"]))
        with self.assertRaises(ValueError):
            self.db.freeze_candidate("run", "b" * 40, evidence(self.db, "b" * 40), expected_version=self.db.business_version("run"))

    def test_candidate_drift_invalidates_old_evidence_before_new_freeze(self):
        candidate_a = "a" * 40
        candidate_b = "b" * 40
        self.db.freeze_candidate("run", candidate_a, evidence(self.db, candidate_a), expected_version=self.db.business_version("run"))
        self.db.record_candidate_evidence("run", "test", evidence(self.db, candidate_a, "test"), self.db.business_version("run"))
        self.db.invalidate_candidate("run", "remote advanced", candidate_b, self.db.business_version("run"))
        old = self.db.candidate_snapshot("run")["freeze"][0]
        self.assertEqual("invalidated", old["status"])
        self.db.freeze_candidate("run", candidate_b, evidence(self.db, candidate_b), expected_version=self.db.business_version("run"))
        snapshot = self.db.candidate_snapshot("run")
        self.assertEqual("active", snapshot["freeze"][1]["status"])
        self.assertEqual(candidate_a, snapshot["evidence"][0]["candidate_sha"])


if __name__ == "__main__":
    unittest.main()
