import tempfile
import unittest
from pathlib import Path
import sys
import json
import subprocess

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB, RestoreValidationError, SCHEMA_VERSION


class BackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = ControlDB(Path(self.temp.name) / "run.db")
        self.addCleanup(self.db.close)
        self.db.create_run("run", "initiative", "requirement")
        self.db.pin_policy("run", {"name": "safe", "version": 1}, "impl-a", self.db.business_version("run"))

    def test_manifest_covers_schema_policy_files_and_reachable_evidence(self):
        self.db.record_receipt("run", "ticket", "T1", {"status": "verified", "source": "github:https://example.test/T1"}, self.db.business_version("run"))
        manifest = self.db.create_backup_manifest("run", {"policy.json": "sha-policy", "run.db": "sha-db"}, "sha-db", self.db.business_version("run"))
        checked = self.db.validate_backup_manifest("run", manifest["manifest_id"], {"policy.json": "sha-policy", "run.db": "sha-db"}, "sha-db")
        self.assertEqual("allow", checked["decision"])
        self.assertEqual(SCHEMA_VERSION, checked["schema_version"])
        self.assertFalse(checked["external_rollback"])

    def test_manifest_tampering_and_digest_mismatch_fail_closed(self):
        manifest = self.db.create_backup_manifest("run", {"run.db": "sha-db"}, "sha-db", self.db.business_version("run"))
        with self.assertRaises(RestoreValidationError) as raised:
            self.db.validate_backup_manifest("run", manifest["manifest_id"], {"run.db": "different"}, "sha-db")
        self.assertEqual("restore_blocked", raised.exception.code)
        self.db.conn.execute("UPDATE backup_manifests SET payload=? WHERE manifest_id=?", ('{"schema_version": 1}', manifest["manifest_id"]))
        with self.assertRaises(RestoreValidationError) as raised:
            self.db.validate_backup_manifest("run", manifest["manifest_id"])
        self.assertIn("manifest_digest_mismatch", raised.exception.details["errors"])

    def test_restore_stays_pending_until_unknown_intents_are_reconciled(self):
        manifest = self.db.create_backup_manifest("run", expected_version=self.db.business_version("run"))
        intent = self.db.prepare_intent("run", "publish", "external-job", "started", self.db.business_version("run"))
        self.db.record_intent_outcome(intent["intent_id"], "outcome_unknown", {"status": "timeout"}, expected_version=self.db.business_version("run"))
        restore = self.db.begin_restore("run", manifest["manifest_id"], self.db.business_version("run"))
        self.assertEqual("pending_reconciliation", restore["status"])
        with self.assertRaises(RestoreValidationError):
            self.db.record_restore_reconciliation(restore["restore_id"], "complete", {"verified": True}, self.db.business_version("run"))
        self.db.reconcile_intent(intent["intent_id"], {"status": "verified", "source": "external-readback"}, self.db.business_version("run"))
        ready = self.db.record_restore_reconciliation(restore["restore_id"], "complete", {"verified": True, "source": "external-readback"}, self.db.business_version("run"))
        self.assertEqual("ready", ready["status"])
        row = self.db.conn.execute("SELECT status,reconciliation_status FROM restore_records WHERE restore_id=?", (restore["restore_id"],)).fetchone()
        self.assertEqual(("ready", "complete"), tuple(row))

    def test_cli_returns_structured_fail_closed_result_and_restart_rebuilds_restore_state(self):
        manifest = self.db.create_backup_manifest("run", {"run.db": "sha-db"}, "sha-db", self.db.business_version("run"))
        self.db.close()
        controller = Path(__file__).parents[1] / "scripts" / "controller.py"
        rejected = subprocess.run(
            [sys.executable, str(controller), "--db", str(self.db.path), "validate-backup-manifest", "--run-id", "run", "--manifest-id", str(manifest["manifest_id"]), "--database-digest", "wrong"],
            capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(0, rejected.returncode)
        packet = json.loads(rejected.stdout)
        self.assertEqual({"decision", "error", "details"}, set(packet))
        self.assertEqual("reject", packet["decision"])
        self.assertEqual("restore_blocked", packet["error"])

        reopened = ControlDB.open_existing(self.db.path)
        self.addCleanup(reopened.close)
        started = reopened.begin_restore("run", manifest["manifest_id"], reopened.business_version("run"))
        self.assertEqual("pending_reconciliation", started["status"])
        reopened.close()
        restarted = ControlDB.open_existing(self.db.path)
        self.addCleanup(restarted.close)
        saved_policy = restarted.policy("run")
        saved_restore = restarted.conn.execute("SELECT status,reconciliation_status FROM restore_records WHERE restore_id=?", (started["restore_id"],)).fetchone()
        self.assertEqual("impl-a", saved_policy["implementation_digest"])
        self.assertEqual(("pending_reconciliation", "pending"), tuple(saved_restore))


if __name__ == "__main__":
    unittest.main()
