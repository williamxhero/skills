import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB
from evidence_gate import EvidenceGateError, verify_contract


def gate(db, run_id, target_id, *, candidate="abc", environment="test", scope="controller", **extra):
    value = {
        "schema_version": 1,
        "expected": {"run_id": run_id, "target_id": target_id, "candidate_sha": candidate, "environment": environment, "business_version": db.business_version(run_id)},
        "actor": {"id": "owner", "authorized": True},
        "source": {"kind": "independent-readback", "trust": "verified"},
        "readback": {"status": "verified", "run_id": run_id, "target_id": target_id, "candidate_sha": candidate, "environment": environment},
        "test_scope": scope,
    }
    value.update(extra)
    return value


class EvidenceGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db = ControlDB(Path(self.temporary.name) / "state.db")
        self.addCleanup(self.db.close)
        self.db.create_run("run", "initiative", "requirement")

    def seed_ticket_at_merge(self):
        self.db.add_spec("run", "S", "spec", 1)
        self.db.add_ticket("S", "T", "ticket")
        for status in ("ready", "implementing", "verified", "merged"):
            self.db.update_ticket("T", status)

    def test_empty_expected_is_rejected_by_the_shared_gate(self):
        contract = gate(self.db, "run", "T")
        contract["expected"] = {}
        with self.assertRaises(EvidenceGateError) as raised:
            verify_contract(contract, entity_type="ticket", run_id="run", target_id="T", expected_version=self.db.business_version("run"))
        self.assertEqual("expected_empty", raised.exception.code)

    def test_ticket_rejection_is_atomic_for_missing_or_mismatched_evidence(self):
        self.seed_ticket_at_merge()
        before = self.db.snapshot("run")
        invalid = gate(self.db, "run", "T", candidate="wrong")
        with self.assertRaises(EvidenceGateError) as raised:
            self.db.update_ticket("T", "closed", ["commit:abc"], ["test://abc/controller"], ["acceptance:T"], gate=invalid)
        self.assertEqual("commit_evidence_inapplicable", raised.exception.code)
        after = self.db.snapshot("run")
        self.assertEqual(before["run"]["business_version"], after["run"]["business_version"])
        self.assertEqual(len(before["events"]), len(after["events"]))
        self.assertEqual(len(before["evidence_refs"]), len(after["evidence_refs"]))
        self.assertEqual("merged", after["tickets"][0]["status"])

    def test_ticket_closure_requires_matching_commit_and_test_and_persists_gate(self):
        self.seed_ticket_at_merge()
        contract = gate(self.db, "run", "T")
        self.db.update_ticket("T", "closed", ["commit:abc"], ["test://abc/controller"], ["acceptance:T"], gate=contract)
        snapshot = self.db.snapshot("run")
        self.assertEqual("closed", snapshot["tickets"][0]["status"])
        self.assertEqual("verified_transition_gate", snapshot["evidence_refs"][-1]["evidence_kind"])

    def test_action_success_and_thread_archive_require_trusted_readbacks(self):
        action_id = self.db.set_action("run", "dispatch", "S")
        with self.assertRaises(EvidenceGateError):
            self.db.finish_action(action_id, "succeeded")
        action_gate = gate(self.db, "run", str(action_id), action_readback=True)
        self.db.finish_action(action_id, "succeeded", gate=action_gate)

        self.db.add_spec("run", "S", "spec", 1)
        self.db.add_thread("run", "thread", "spec", "S")
        with self.assertRaises(EvidenceGateError):
            self.db.update_thread("run", "thread", "archived", operation="archive", readback="readback")
        thread_gate = gate(self.db, "run", "thread", archive_operation=True, archive_readback=True)
        thread_gate["readback"]["archived"] = True
        self.db.update_thread("run", "thread", "archived", operation="archive", readback="readback", gate=thread_gate)

    def test_spec_close_requires_closed_tickets_and_archived_thread_readback(self):
        self.seed_ticket_at_merge()
        self.db.update_ticket("T", "closed", ["commit:abc"], ["test://abc/controller"], ["acceptance:T"], gate=gate(self.db, "run", "T"))
        self.db.add_thread("run", "thread", "spec", "S")
        thread_gate = gate(self.db, "run", "thread", archive_operation=True, archive_readback=True)
        thread_gate["readback"]["archived"] = True
        self.db.update_thread("run", "thread", "archived", operation="archive", readback="readback", gate=thread_gate)
        for status in ("ready", "ticketing", "tickets_ready", "implementing", "handoff_received", "verifying", "ready_to_merge", "merged"):
            self.db.update_spec("S", status)
        spec_gate = gate(self.db, "run", "S")
        spec_gate["readback"].update({"merged": True, "tickets_closed": True, "thread_archived": True})
        self.db.update_spec("S", "closed", gate=spec_gate)
        self.assertEqual("closed", self.db.snapshot("run")["specs"][0]["status"])

    def test_cli_and_programmatic_call_share_gate_missing_rejection(self):
        self.seed_ticket_at_merge()
        with self.assertRaises(EvidenceGateError) as direct:
            self.db.update_ticket("T", "closed", ["commit:abc"], ["test://abc/controller"], ["acceptance:T"])
        version = self.db.business_version("run")
        command = [
            sys.executable, str(Path(__file__).parents[1] / "scripts" / "controller.py"),
            "--db", str(self.db.path), "ticket-state", "--ticket-id", "T", "--status", "closed",
            "--commits", json.dumps(["commit:abc"]), "--tests", json.dumps(["test://abc/controller"]),
            "--acceptance", json.dumps(["acceptance:T"]), "--expected-version", str(version),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertNotEqual(0, completed.returncode)
        payload = json.loads(completed.stdout)
        self.assertEqual(direct.exception.code, payload["error"])
        self.assertEqual("merged", self.db.snapshot("run")["tickets"][0]["status"])


if __name__ == "__main__":
    unittest.main()
