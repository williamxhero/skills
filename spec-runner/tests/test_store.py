from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import unittest
import hashlib
import json
from pathlib import Path

from spec_runner.errors import RunnerError
from spec_runner.store import Store, RunRecord, now


class StoreLeaseTests(unittest.TestCase):
    def test_control_db_busy_write_is_reported_as_recoverable_runner_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "control"
            store = Store.open(root, create=True)
            lock = sqlite3.connect(root / "spec-runner.sqlite3", timeout=0.01, isolation_level=None)
            try:
                lock.execute("BEGIN EXCLUSIVE")
                with self.assertRaises(RunnerError) as raised:
                    with store.transaction():
                        store.connection.execute(
                            "INSERT OR REPLACE INTO metadata(key, value) VALUES('lock-probe', 'unexpected')"
                        )
                self.assertEqual(raised.exception.code, "control_database_busy")
                self.assertEqual(raised.exception.details["database_error"], "database is locked")
            finally:
                lock.execute("ROLLBACK")
                lock.close()
                store.close()

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
                self.assertEqual(status["operations"][0]["operation_kind"], "codex_sdk_stage")
                self.assertEqual(status["workers"][0]["external_thread_id"], "thread-live")
                self.assertEqual(status["workers"][0]["external_turn_id"], "turn-live")
                self.assertIn("worker_turn_started", [event["event_type"] for event in status["events"]])
            finally:
                store.close()

    def test_interrupted_codex_turn_is_not_recorded_as_completed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Store.open(Path(temp) / "control", create=True)
            try:
                from spec_runner.store import RunRecord, now

                timestamp = now()
                run = RunRecord(
                    run_id="run-interrupted",
                    launch_key="interrupted",
                    input_digest="input",
                    config_digest="config",
                    repository_path=temp,
                    target_ref="HEAD",
                    artifact_root="artifacts",
                    backend_kind="codex_sdk",
                    state="starting",
                    current_step="codex_example",
                    log_path="logs/run-interrupted.jsonl",
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                store.create_run(run, "start:run-interrupted")
                store.record_codex_turn_started(
                    run.run_id,
                    "start:run-interrupted",
                    thread_id="thread-interrupted",
                    turn_id="turn-interrupted",
                    step_name="codex_example",
                    worker_id="codex_sdk:run-interrupted",
                )
                interrupted = store.complete_codex_stage(
                    run.run_id,
                    "start:run-interrupted",
                    thread_id="thread-interrupted",
                    turn_id="turn-interrupted",
                    state="paused",
                )
                self.assertEqual(interrupted.state, "paused")
                event_types = [event["event_type"] for event in store.events_for_run(run.run_id)]
                self.assertIn("worker_turn_interrupted", event_types)
                self.assertNotIn("step_completed", event_types)
            finally:
                store.close()

    def test_thread_migration_is_durable_idempotent_and_generation_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Store.open(Path(temp) / "control", create=True)
            try:
                from spec_runner.store import RunRecord, now
                timestamp = now()
                run = RunRecord(
                    run_id="run-migration", launch_key="migration", input_digest="input",
                    config_digest="config", repository_path=temp, target_ref="HEAD",
                    artifact_root="artifacts", backend_kind="codex_sdk", state="starting",
                    current_step="codex_planning", log_path="logs/run-migration.jsonl",
                    created_at=timestamp, updated_at=timestamp,
                )
                store.create_run(run, "start:run-migration")
                intent = store.prepare_thread_migration(
                    migration_key="takeover:1:thread-migration", run_id=run.run_id,
                    stage="codex_planning", source_thread_id="source-thread",
                    handover_digest=hashlib.sha256(json.dumps({"schema_version": "spec-runner-sdk-thread-interrupt/v1", "thread_id": "source-thread", "accepted": True}, sort_keys=True).encode("utf-8")).hexdigest(), input_revision="brief-v1",
                )
                self.assertEqual(intent["state"], "intent")
                handover = {"schema_version": "spec-runner-sdk-thread-interrupt/v1", "thread_id": "source-thread", "accepted": True}
                with self.assertRaisesRegex(RunnerError, "handover"):
                    store.record_migration_successor(migration_key="takeover:1:thread-migration", successor_thread_id="successor-thread")
                store.record_migration_handover(migration_key="takeover:1:thread-migration", handover=handover)
                successor = store.record_migration_successor(
                    migration_key="takeover:1:thread-migration", successor_thread_id="successor-thread",
                    successor={"thread_id": "successor-thread", "turn_started": False},
                )
                self.assertEqual(successor["state"], "successor_registered")
                transferred = store.complete_migration_owner_transfer(
                    migration_key="takeover:1:thread-migration", expected_generation=0,
                    owner_worker_id="codex_sdk:run-migration:codex_planning",
                )
                self.assertEqual(transferred["owner_generation"], 1)
                self.assertEqual(store.record_migration_successor(
                    migration_key="takeover:1:thread-migration", successor_thread_id="successor-thread"
                )["state"], "owner_transferred")
                with self.assertRaisesRegex(RunnerError, "owner"):
                    store.complete_migration_owner_transfer(
                        migration_key="takeover:1:thread-migration", expected_generation=0,
                        owner_worker_id="other-worker",
                    )
                stale = store.record_migration_event(
                    migration_key="takeover:1:thread-migration", generation=0,
                    event_key="migration:1:old-event", payload={"thread_id": "source-thread"},
                )
                self.assertFalse(stale["applied"])
                current = store.record_migration_event(
                    migration_key="takeover:1:thread-migration", generation=1,
                    event_key="migration:1:new-event", payload={"thread_id": "successor-thread"},
                )
                self.assertTrue(current["applied"])
                status = store.public_status(run.run_id)
                self.assertEqual(status["thread_migrations"][0]["successor_thread_id"], "successor-thread")
                self.assertIn("thread_migration_stale_event", [event["event_type"] for event in status["events"]])
            finally:
                store.close()

    def test_thread_migration_identity_and_uncertain_creation_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Store.open(Path(temp) / "control", create=True)
            try:
                from spec_runner.store import RunRecord, now
                timestamp = now()
                run = RunRecord(
                    run_id="run-uncertain", launch_key="uncertain", input_digest="input",
                    config_digest="config", repository_path=temp, target_ref="HEAD",
                    artifact_root="artifacts", backend_kind="codex_sdk", state="starting",
                    current_step="codex_example", log_path="logs/run-uncertain.jsonl",
                    created_at=timestamp, updated_at=timestamp,
                )
                store.create_run(run, "start:run-uncertain")
                store.prepare_thread_migration(
                    migration_key="migration-uncertain", run_id=run.run_id, stage="codex_example",
                    source_thread_id="source", handover_digest="h1", input_revision="r1",
                )
                with self.assertRaisesRegex(RunnerError, "identity"):
                    store.prepare_thread_migration(
                        migration_key="migration-uncertain", run_id=run.run_id, stage="codex_example",
                        source_thread_id="other-source", handover_digest="h1", input_revision="r1",
                    )
                uncertain = store.record_migration_uncertainty(
                    migration_key="migration-uncertain", details={"reason": "provider response lost"}
                )
                self.assertEqual(uncertain["state"], "uncertain")
                with self.assertRaisesRegex(RunnerError, "handover"):
                    store.record_migration_successor(migration_key="migration-uncertain", successor_thread_id="successor")
            finally:
                store.close()

    def test_external_operation_returns_structured_receipt_after_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Store.open(Path(temp) / "control", create=True)
            try:
                prepared = store.prepare_external_operation(
                    operation_id="github:issue:1", run_id="run-external",
                    operation_kind="github_issue_publication", repository="owner/repo",
                    input_digest="digest",
                )
                self.assertEqual(prepared["state"], "intent")
                self.assertNotIn("receipt_json", prepared)
                completed = store.complete_external_operation(
                    operation_id="github:issue:1", receipt={"key": "S1", "number": 1},
                )
                self.assertEqual(completed["receipt"], {"key": "S1", "number": 1})
                self.assertNotIn("receipt_json", completed)
                replay = store.prepare_external_operation(
                    operation_id="github:issue:1", run_id="run-external",
                    operation_kind="github_issue_publication", repository="owner/repo",
                    input_digest="digest",
                )
                self.assertEqual(replay["state"], "completed")
                self.assertEqual(replay["receipt"], {"key": "S1", "number": 1})
            finally:
                store.close()

    def test_recovery_episode_budget_and_observations_survive_status_readback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Store.open(Path(temp) / "control", create=True)
            try:
                from spec_runner.store import RunRecord, now
                timestamp = now()
                run = RunRecord(
                    run_id="run-recovery", launch_key="recovery", input_digest="input",
                    config_digest="config", repository_path=temp, target_ref="HEAD",
                    artifact_root="artifacts", backend_kind="codex_sdk", state="starting",
                    current_step="implement", log_path="logs/run-recovery.jsonl",
                    created_at=timestamp, updated_at=timestamp,
                )
                store.create_run(run, "start:run-recovery")
                episode = store.upsert_recovery_episode(
                    episode_id="episode-1", run_id=run.run_id, operation_kind="implementation",
                    stage="implement", generation=2,
                    counters={"same_thread_attempts": 1, "capacity_attempts": 2},
                )
                self.assertEqual(episode["capacity_attempts"], 2)
                store.record_recovery_observation(
                    observation_id="observation-1", episode_id="episode-1",
                    observation={"fingerprint": "fp-1", "family": "capacity"},
                )
                store.record_recovery_decision(
                    decision_id="decision-1", episode_id="episode-1",
                    decision={"action": "service_wait", "remaining_budget": {"capacity_retries": 0}},
                )
                status = store.public_status(run.run_id)
                self.assertEqual(status["recovery"]["episodes"][0]["generation"], 2)
                self.assertEqual(status["recovery"]["episodes"][0]["observations"][0]["observation"]["family"], "capacity")
                self.assertEqual(status["recovery"]["episodes"][0]["decisions"][0]["decision"]["action"], "service_wait")
            finally:
                store.close()

    def test_recovery_budget_reservation_is_idempotent_and_monotonic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Store.open(Path(temp) / "control", create=True)
            try:
                timestamp = now()
                run = RunRecord(
                    run_id="run-reservation", launch_key="reservation", input_digest="input",
                    config_digest="config", repository_path=temp, target_ref="HEAD",
                    artifact_root="artifacts", backend_kind="codex_sdk", state="starting",
                    current_step="implement", log_path="logs/run.jsonl",
                    created_at=timestamp, updated_at=timestamp,
                )
                store.create_run(run, "start:run-reservation")
                store.upsert_recovery_episode(
                    episode_id="episode-reservation", run_id=run.run_id,
                    operation_kind="implementation", stage="implement", generation=0,
                )
                first = store.reserve_recovery_budget(
                    reservation_id="attempt-1", episode_id="episode-reservation",
                    counter_name="capacity_attempts",
                )
                duplicate = store.reserve_recovery_budget(
                    reservation_id="attempt-1", episode_id="episode-reservation",
                    counter_name="capacity_attempts",
                )
                store.upsert_recovery_episode(
                    episode_id="episode-reservation", run_id=run.run_id,
                    operation_kind="implementation", stage="implement", generation=0,
                    counters={"capacity_attempts": 0},
                )
                second = store.reserve_recovery_budget(
                    reservation_id="attempt-2", episode_id="episode-reservation",
                    counter_name="capacity_attempts",
                )
                self.assertEqual(first["capacity_attempts"], 1)
                self.assertEqual(duplicate["capacity_attempts"], 1)
                self.assertEqual(second["capacity_attempts"], 2)
            finally:
                store.close()

    def test_route_probe_has_single_half_open_owner_across_store_connections(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "control"
            seed = Store.open(root, create=True)
            try:
                seed.record_route_failure(
                    route_scope="model:gpt-test|tier:default",
                    failure_fingerprint="route-failure-1",
                    cooldown_seconds=0,
                    observed_at="2026-09-26T00:00:00+00:00",
                )
            finally:
                seed.close()

            barrier = threading.Barrier(2)
            results: dict[str, dict[str, object]] = {}

            def acquire(owner_token: str) -> None:
                store = Store.open(root, create=False)
                try:
                    barrier.wait()
                    results[owner_token] = store.acquire_route_probe(
                        route_scope="model:gpt-test|tier:default",
                        owner_token=owner_token,
                        lease_seconds=60,
                        observed_at="2026-09-26T00:00:01+00:00",
                    )
                finally:
                    store.close()

            first = threading.Thread(target=acquire, args=("runner-a",))
            second = threading.Thread(target=acquire, args=("runner-b",))
            first.start()
            second.start()
            first.join()
            second.join()

            self.assertEqual({result["acquired"] for result in results.values()}, {True, False})
            held = next(result for result in results.values() if result["acquired"] is False)
            self.assertEqual(held["reason"], "half_open_owned")

    def test_route_circuit_cooldown_scope_isolation_and_verified_close(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Store.open(Path(temp) / "control", create=True)
            try:
                scope = "model:gpt-test|tier:fast"
                store.record_route_failure(
                    route_scope=scope,
                    failure_fingerprint="route-failure-2",
                    cooldown_seconds=60,
                    observed_at="2026-09-26T00:00:00+00:00",
                )
                cooling = store.acquire_route_probe(
                    route_scope=scope,
                    owner_token="runner-a",
                    lease_seconds=30,
                    observed_at="2026-09-26T00:00:30+00:00",
                )
                self.assertFalse(cooling["acquired"])
                self.assertEqual(cooling["reason"], "cooldown_active")
                isolated = store.acquire_route_probe(
                    route_scope="model:gpt-test|tier:default",
                    owner_token="runner-a",
                    lease_seconds=30,
                    observed_at="2026-09-26T00:00:30+00:00",
                )
                self.assertFalse(isolated["acquired"])
                self.assertEqual(isolated["reason"], "circuit_closed")

                acquired = store.acquire_route_probe(
                    route_scope=scope,
                    owner_token="runner-a",
                    lease_seconds=30,
                    observed_at="2026-09-26T00:01:01+00:00",
                )
                self.assertTrue(acquired["acquired"])
                replay = store.acquire_route_probe(
                    route_scope=scope,
                    owner_token="runner-a",
                    lease_seconds=30,
                    observed_at="2026-09-26T00:01:02+00:00",
                )
                self.assertTrue(replay["acquired"])
                self.assertEqual(replay["reason"], "owner_replay")
                with self.assertRaisesRegex(RunnerError, "success evidence"):
                    store.complete_route_probe(
                        route_scope=scope,
                        owner_token="runner-a",
                        success_evidence="ordinary_http_200",
                        observed_at="2026-09-26T00:01:03+00:00",
                    )
                closed = store.complete_route_probe(
                    route_scope=scope,
                    owner_token="runner-a",
                    success_evidence="business_progress:verified-artifact",
                    observed_at="2026-09-26T00:01:04+00:00",
                )
                self.assertTrue(closed["closed"])
                self.assertEqual(closed["state"], "closed")
                self.assertFalse(
                    store.acquire_route_probe(
                        route_scope=scope,
                        owner_token="runner-b",
                        lease_seconds=30,
                        observed_at="2026-09-26T00:01:05+00:00",
                    )["acquired"]
                )
            finally:
                store.close()

    def test_route_probe_expired_owner_can_be_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Store.open(Path(temp) / "control", create=True)
            try:
                scope = "model:gpt-test|tier:default"
                store.record_route_failure(
                    route_scope=scope,
                    failure_fingerprint="route-failure-3",
                    cooldown_seconds=0,
                    observed_at="2026-09-26T00:00:00+00:00",
                )
                first = store.acquire_route_probe(
                    route_scope=scope,
                    owner_token="runner-a",
                    lease_seconds=10,
                    observed_at="2026-09-26T00:00:01+00:00",
                )
                self.assertTrue(first["acquired"])
                reclaimed = store.acquire_route_probe(
                    route_scope=scope,
                    owner_token="runner-b",
                    lease_seconds=10,
                    observed_at="2026-09-26T00:00:12+00:00",
                )
                self.assertTrue(reclaimed["acquired"])
                self.assertEqual(reclaimed["half_open_owner"], "runner-b")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
