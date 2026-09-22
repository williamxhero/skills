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

    def test_events_are_deduplicated_and_control_requests_are_audited(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Store.open(Path(temp) / "control", create=True)
            try:
                from spec_runner.store import RunRecord, now

                timestamp = now()
                run = RunRecord(
                    run_id="run-events",
                    launch_key="events",
                    input_digest="input",
                    config_digest="config",
                    repository_path=temp,
                    target_ref="HEAD",
                    artifact_root="artifacts",
                    backend_kind="deterministic_test",
                    state="starting",
                    current_step="deterministic_example",
                    log_path="logs/run-events.jsonl",
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                store.create_run(run, "start:run-events")
                self.assertTrue(store.append_event(run_id=run.run_id, event_key="same", event_type="late_event", payload={"n": 1}))
                self.assertFalse(store.append_event(run_id=run.run_id, event_key="same", event_type="late_event", payload={"n": 2}))
                self.assertTrue(store.append_event(run_id=run.run_id, event_key="same-2", event_type="late_event", payload={"n": 2}))
                store.request_control(run.run_id, "pause_requested")
                events = store.events_for_run(run.run_id)
                self.assertEqual(sum(event["event_key"] == "same" for event in events), 1)
                self.assertEqual(sum(event["event_key"] == "same-2" for event in events), 1)
                self.assertEqual(events[-1]["event_type"], "control_requested")
            finally:
                store.close()

    def test_codex_turn_identity_is_durable_before_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Store.open(Path(temp) / "control", create=True)
            try:
                from spec_runner.store import RunRecord, now

                timestamp = now()
                run = RunRecord(
                    run_id="run-turn",
                    launch_key="turn",
                    input_digest="input",
                    config_digest="config",
                    repository_path=temp,
                    target_ref="HEAD",
                    artifact_root="artifacts",
                    backend_kind="codex_sdk",
                    state="starting",
                    current_step="codex_example",
                    log_path="logs/run-turn.jsonl",
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                store.create_run(run, "start:run-turn")
                store.record_codex_turn_started(
                    run.run_id,
                    "start:run-turn",
                    thread_id="thread-live",
                    turn_id="turn-live",
                    step_name="codex_example",
                    worker_id="codex_sdk:run-turn",
                )
                status = store.public_status(run.run_id)
                self.assertEqual(status["run"]["state"], "running")
                self.assertEqual(status["workers"][0]["external_thread_id"], "thread-live")
                self.assertEqual(status["workers"][0]["external_turn_id"], "turn-live")
                self.assertIn("worker_turn_started", [event["event_type"] for event in status["events"]])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
