from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from spec_runner.errors import RunnerError
from spec_runner.store import Store


class StoreLeaseTests(unittest.TestCase):
    def test_live_writer_cannot_be_displaced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            first = Store.open(Path(temp) / "control", create=True)
            try:
                first.acquire_lease(scope="repo@HEAD", run_id="run-1", owner_token="owner-1", pid=os.getpid())
                with self.assertRaisesRegex(RunnerError, "another Spec Runner writer"):
                    first.acquire_lease(scope="repo@HEAD", run_id="run-2", owner_token="owner-2", pid=os.getpid())
            finally:
                first.close()

    def test_dead_stale_local_writer_can_be_reclaimed_only_after_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "control"
            first = Store.open(root, create=True)
            first.acquire_lease(scope="repo@HEAD", run_id="run-1", owner_token="owner-1", pid=2_147_483_647)
            first.close()
            second = Store.open(root, create=False)
            try:
                with self.assertRaisesRegex(RunnerError, "another Spec Runner writer"):
                    second.acquire_lease(scope="repo@HEAD", run_id="run-2", owner_token="owner-2", pid=os.getpid(), stale_after_seconds=60.0)
                second.acquire_lease(scope="repo@HEAD", run_id="run-2", owner_token="owner-2", pid=os.getpid(), stale_after_seconds=0.0)
                self.assertEqual(second.lease("repo@HEAD")["owner_token"], "owner-2")
            finally:
                second.close()


if __name__ == "__main__":
    unittest.main()
