import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB
from recovery import record_failure, resume_recovery


class RecoveryTests(unittest.TestCase):
    def _db(self):
        directory = tempfile.TemporaryDirectory()
        db = ControlDB(Path(directory.name) / "run.db")
        db.create_run("run-1", "demo", "req")
        action_id = db.set_action("run-1", "repair", "task-1")
        return directory, db, action_id

    def test_same_failure_without_progress_escalates_and_does_not_loop(self):
        directory, db, action_id = self._db()
        try:
            first = record_failure(db, "run-1", action_id, "code_defect", "assertion failed", "code-a", "env-a", "strategy-a", "marker-a", "repair", ["test://1"], max_attempts=3)
            second = record_failure(db, "run-1", action_id, "code_defect", "assertion failed", "code-a", "env-a", "strategy-a", "marker-a", "repair", ["test://1"], max_attempts=3)
            self.assertEqual("retry", first["status"])
            self.assertEqual("escalated", second["status"])
            self.assertEqual("blocked", db.conn.execute("SELECT status FROM actions WHERE action_id=?", (action_id,)).fetchone()[0])
            with self.assertRaisesRegex(ValueError, "new strategy"):
                resume_recovery(db, second["recovery_id"], True, "strategy-a", "marker-a", ["diagnosis://same"])
            resumed = resume_recovery(db, second["recovery_id"], True, "strategy-b", "marker-b", ["diagnosis://new"])
            self.assertEqual("resolved", resumed["status"])
            self.assertEqual("pending", db.conn.execute("SELECT status FROM actions WHERE action_id=?", (action_id,)).fetchone()[0])
        finally:
            db.close(); directory.cleanup()

    def test_budget_and_owner_survive_reopen(self):
        directory, db, action_id = self._db()
        try:
            first = record_failure(db, "run-1", action_id, "transient", "network", "code-a", "env-a", "s1", "p1", "adapter", ["net://1"], max_attempts=1)
            db.close()
            reopened = ControlDB(Path(directory.name) / "run.db")
            with self.assertRaisesRegex(ValueError, "retry owner"):
                record_failure(reopened, "run-1", action_id, "transient", "network", "code-a", "env-a", "s1", "p2", "controller", ["net://2"], max_attempts=1)
            second = record_failure(reopened, "run-1", action_id, "transient", "network", "code-a", "env-a", "s1", "p2", "adapter", ["net://2"], max_attempts=1)
            self.assertEqual("paused", second["status"])
            reopened.close()
        finally:
            if db.conn:
                try: db.close()
                except Exception: pass
            directory.cleanup()

    def test_resume_requires_archived_old_attempt_and_cli_path_is_available(self):
        directory, db, action_id = self._db()
        try:
            failure = record_failure(db, "run-1", action_id, "unknown_outcome", "lost response", "code-a", "env-a", "s1", "p1", "controller", ["op://1"])
            with self.assertRaisesRegex(ValueError, "archived"):
                resume_recovery(db, failure["recovery_id"], False, "s2", "p2", ["repair://1"])
            controller = Path(__file__).parents[1] / "scripts" / "controller.py"
            output = subprocess.run([sys.executable, str(controller), "--db", str(Path(directory.name) / "run.db"), "record-recovery-failure", "--run-id", "run-1", "--action-id", str(action_id), "--category", "unknown_outcome", "--error-fingerprint", "lost response", "--code-digest", "code-a", "--environment-digest", "env-a", "--strategy-digest", "s1", "--progress-marker", "p1", "--retry-owner", "controller", "--evidence", '["op://1"]'], capture_output=True, text=True, check=True)
            self.assertEqual("escalated", json.loads(output.stdout)["status"])
        finally:
            db.close(); directory.cleanup()


if __name__ == "__main__":
    unittest.main()
