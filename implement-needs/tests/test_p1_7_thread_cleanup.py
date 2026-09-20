import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from control_db import ControlDB
from task_identity import TaskIdentity
from thread_cleanup import archive_with_readback, classify_inventory, record_cleanup_receipt


class ThreadCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = ControlDB(Path(self.temp.name) / "state.db")
        self.db.create_run("run", "initiative", "requirement")
        self.db.add_thread("run", "helper", "grill", identity=TaskIdentity("task", "run", "01", "0123456789abcdef"), formal_thread_id="formal", host_id="local", owner_id="owner", cwd="C:/x", project_id="p")
        self.db.observe_thread("run", "helper", lifecycle="idle")

    def tearDown(self):
        self.db.close(); self.temp.cleanup()

    def test_idle_is_not_archived_until_readback_then_is_finalized(self):
        entry = dict(self.db.conn.execute("SELECT * FROM threads WHERE thread_id='helper'").fetchone())
        receipt = archive_with_readback(archive=lambda **_: {"ok": True}, readback=lambda **_: {"archived": True}, entry=entry)
        record_cleanup_receipt(self.db, "run", receipt)
        self.assertEqual("archived", self.db.conn.execute("SELECT lifecycle FROM threads WHERE thread_id='helper'").fetchone()[0])

    def test_inventory_tracks_both_orphan_directions_by_formal_identity(self):
        entry = dict(self.db.conn.execute("SELECT * FROM threads WHERE thread_id='helper'").fetchone())
        result = classify_inventory(run_id="run", registry=[entry], host_tasks=[])
        self.assertEqual("repair", result["decision"])
        self.assertEqual(1, len(result["registry_absent_from_host"]))
        host = {"formal_thread_id": "other", "host_id": "local"}
        result = classify_inventory(run_id="run", registry=[entry], host_tasks=[host])
        self.assertEqual(1, len(result["host_unregistered"]))


if __name__ == "__main__":
    unittest.main()
