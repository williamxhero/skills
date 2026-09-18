import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from advance_runtime import advance
from control_db import ControlDB


class AdvanceRuntimeTests(unittest.TestCase):
    def test_advances_local_spec_and_stops_before_semantic_ticketing(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run-1", "demo", "req")
            db.add_spec("run-1", "S1", "first", 1)
            result = advance(db, "run-1")
            self.assertEqual("needs_llm", result["boundary"])
            self.assertEqual("ticket_current_spec", result["action"]["kind"])
            self.assertEqual(["advance_spec"], [item["kind"] for item in result["processed_actions"]])
            self.assertEqual("ready", db.conn.execute("SELECT status FROM specs WHERE spec_id='S1'").fetchone()[0])
            self.assertEqual("succeeded", db.conn.execute("SELECT status FROM actions").fetchone()[0])
            self.assertEqual(result["state_version"], result["event_cursor"])
            db.close()

    def test_waiting_and_blocked_are_distinct_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run-1", "demo", "req")
            db.add_spec("run-1", "S1", "first", 1)
            db.update_spec("S1", "ready")
            waiting = advance(db, "run-1")
            self.assertEqual("needs_llm", waiting["boundary"])
            db.update_spec("S1", "ticketing")
            db.update_spec("S1", "blocked")
            blocked = advance(db, "run-1")
            self.assertEqual("blocked", blocked["boundary"])
            db.close()

    def test_completed_requires_all_specs_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run-1", "demo", "req")
            pending_validation = advance(db, "run-1")
            self.assertEqual("needs_llm", pending_validation["boundary"])
            self.assertEqual("terminal_validation_required", pending_validation["reason"])
            db.record_terminal_validation("run-1", "allow", ["terminal://run-1"])
            completed = advance(db, "run-1")
            self.assertEqual("completed", completed["boundary"])
            db.add_spec("run-1", "S1", "first", 1)
            blocked = advance(db, "run-1", max_actions=1)
            self.assertEqual("needs_llm", blocked["boundary"])
            db.close()

    def test_advance_budget_and_invalid_budget_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "run.db")
            db.create_run("run-1", "demo", "req")
            db.add_spec("run-1", "S1", "first", 1)
            with self.assertRaises(ValueError):
                advance(db, "run-1", 0)
            result = advance(db, "run-1", 1)
            self.assertEqual("needs_llm", result["boundary"])
            self.assertEqual("advance_budget_exhausted", result["reason"])
            db.close()


if __name__ == "__main__":
    unittest.main()
