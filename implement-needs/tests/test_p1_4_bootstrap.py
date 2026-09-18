import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = ControlDB(Path(self.temp.name) / "run.db")
        self.addCleanup(self.db.close)
        self.db.create_run("run", "initiative", "requirement")
        self.db.add_thread("run", "thread-1", "spec", expected_version=self.db.business_version("run"))

    def test_route_must_be_verified_before_assignment_and_survives_restart(self):
        version = self.db.business_version("run")
        with self.assertRaises(ValueError):
            self.db.advance_bootstrap("run", "thread-1", "assigned", {"status": "verified", "assignment_id": "a1"}, version)
        route = self.db.advance_bootstrap("run", "thread-1", "route_verifying", {"status": "verified", "model": "m", "effort": "medium"}, version)
        assigned = self.db.advance_bootstrap("run", "thread-1", "assigned", {"status": "verified", "assignment_id": "a1"}, route["business_version"])
        self.assertEqual("assigned", self.db.bootstrap("run", "thread-1")["state"])
        self.db.close()
        reopened = ControlDB.open_existing(self.db.path)
        self.addCleanup(reopened.close)
        self.assertEqual("assigned", reopened.bootstrap("run", "thread-1")["state"])

    def test_duplicate_registration_does_not_create_second_bootstrap(self):
        before = self.db.conn.execute("SELECT COUNT(*) FROM thread_bootstraps").fetchone()[0]
        inserted = self.db.add_thread("run", "thread-1", "spec", expected_version=self.db.business_version("run"))
        self.assertFalse(inserted)
        self.assertEqual(before, self.db.conn.execute("SELECT COUNT(*) FROM thread_bootstraps").fetchone()[0])

    def test_explicit_cancellation_is_persisted(self):
        result = self.db.advance_bootstrap("run", "thread-1", "cancelled", {"status": "verified", "reason": "user_cancelled"}, self.db.business_version("run"))
        self.assertEqual("cancelled", result["state"])
        self.assertEqual("user_cancelled", self.db.bootstrap("run", "thread-1")["cancellation_receipt"]["reason"])


if __name__ == "__main__":
    unittest.main()
