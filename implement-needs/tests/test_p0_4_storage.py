import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB, DatabaseModeError, StaleState


class AtomicVersionedStorageTests(unittest.TestCase):
    def test_event_failure_rolls_back_state_event_and_business_version(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "state.db")
            db.create_run("run", "initiative", "requirement")
            db.add_spec("run", "spec", "Atomic spec", 1)
            before_version = db.business_version("run")
            before_events = db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

            with patch.object(db, "_business_event", side_effect=RuntimeError("event disk failure")):
                with self.assertRaisesRegex(RuntimeError, "event disk failure"):
                    db.update_spec("spec", "ready", expected_version=before_version)

            self.assertEqual("planned", db.conn.execute("SELECT status FROM specs WHERE spec_id='spec'").fetchone()[0])
            self.assertEqual(before_version, db.business_version("run"))
            self.assertEqual(before_events, db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            db.close()

    def test_stale_business_version_is_rejected_without_another_event(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "state.db")
            db.create_run("run", "initiative", "requirement")
            db.add_spec("run", "spec", "Versioned spec", 1)
            version = db.business_version("run")
            db.update_spec("spec", "ready", expected_version=version)
            events = db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

            with self.assertRaises(StaleState) as raised:
                db.update_spec("spec", "blocked", expected_version=version)

            self.assertEqual("stale_state", raised.exception.code)
            self.assertEqual("ready", db.conn.execute("SELECT status FROM specs WHERE spec_id='spec'").fetchone()[0])
            self.assertEqual(events, db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            db.close()

    def test_observations_have_a_separate_cursor_and_do_not_advance_delivery_version(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "state.db")
            db.create_run("run", "initiative", "requirement")
            before_version = db.business_version("run")
            before_events = db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

            db.add_observation("run", "runtime", "worker", {"tokens": 123, "status": "succeeded"})

            self.assertEqual(before_version, db.business_version("run"))
            self.assertEqual(before_events, db.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            self.assertEqual(1, db.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0])
            db.close()

    def test_delivery_proof_and_waiver_are_business_evidence_not_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            db = ControlDB(Path(directory) / "state.db")
            db.create_run("run", "initiative", "requirement")
            version = db.business_version("run")
            db.record_delivery_proof("run", "ticket", "T1", {"sha": "abc"}, version)
            version = db.business_version("run")
            db.record_dependency_waiver("run", "spec", "S1", {"reason": "documented"}, version)

            self.assertEqual(2, db.conn.execute("SELECT COUNT(*) FROM evidence_refs").fetchone()[0])
            self.assertEqual(2, db.conn.execute("SELECT COUNT(*) FROM events WHERE run_id='run' AND event_type LIKE '%_recorded'").fetchone()[0])
            self.assertEqual(0, db.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0])
            with self.assertRaises(ValueError):
                db.add_observation("run", "ticket", "T1", {"delivery_proof": {"sha": "abc"}})
            db.close()

    def test_read_only_and_open_existing_never_create_missing_database_or_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "new-parent" / "missing.db"
            with self.assertRaises(FileNotFoundError):
                ControlDB.open_existing(missing)
            with self.assertRaises(FileNotFoundError):
                ControlDB.read_only(missing)
            self.assertFalse(missing.parent.exists())

    def test_read_only_existing_database_does_not_change_database_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.db"
            db = ControlDB(path)
            db.create_run("run", "initiative", "requirement")
            db.close()
            before = {file.name: os.stat(file).st_mtime_ns for file in path.parent.iterdir()}

            read_only = ControlDB.read_only(path)
            self.assertEqual("run", read_only.snapshot("run")["run"]["run_id"])
            read_only.close()

            after = {file.name: os.stat(file).st_mtime_ns for file in path.parent.iterdir()}
            self.assertEqual(before, after)

    def test_read_only_rejects_incomplete_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partial.db"
            path.touch()
            with self.assertRaises(DatabaseModeError):
                ControlDB.read_only(path)


if __name__ == "__main__":
    unittest.main()
