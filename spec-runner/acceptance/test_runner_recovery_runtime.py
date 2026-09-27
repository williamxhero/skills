from __future__ import annotations

from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from spec_runner.errors import RunnerError
from spec_runner.recovery_runtime import RecoveryEpisode, RecoveryRuntime
from spec_runner.store import RunRecord, Store, now
from spec_runner import workflow


def _run(root: Path) -> RunRecord:
    return RunRecord(
        run_id="77777777-7777-7777-7777-777777777777",
        launch_key="recovery-runtime",
        input_digest="brief",
        config_digest="config",
        repository_path=str(root / "repo"),
        target_ref="HEAD",
        artifact_root="artifacts",
        backend_kind="codex_sdk",
        state="starting",
        current_step="codex_planning",
        log_path="logs/run.jsonl",
        created_at=now(),
        updated_at=now(),
    )


def _capacity_error(turn_id: str = "turn-capacity") -> RunnerError:
    return RunnerError(
        "sdk_rate_limited",
        "Codex SDK turn was rate limited",
        details={
            "fault_observation": {
                "message": "capacity temporarily unavailable",
                "source": "sdk_result",
                "structured": True,
                "request_admission": "rejected",
                "execution_outcome": "failed",
                "thread_id": "thread-capacity",
                "turn_id": turn_id,
            }
        },
    )


def _unknown_error(turn_id: str, progress: str | None = None) -> RunnerError:
    observation = {
        "message": "an unclassified provider failure",
        "source": "sdk_result",
        "structured": True,
        "request_admission": "rejected",
        "execution_outcome": "failed",
        "thread_id": "thread-unknown",
        "turn_id": turn_id,
    }
    if progress is not None:
        observation["last_verified_progress"] = progress
    return RunnerError("sdk_failure", "an unclassified provider failure", details={"fault_observation": observation})


def test_runner_persists_capacity_budget_and_escalates_to_service_wait(tmp_path: Path) -> None:
    root = tmp_path / "control"
    store = Store.open(root, create=True)
    run = _run(tmp_path)
    store.create_run(run, "start:" + run.run_id)
    store.begin_stage(
        run.run_id, step_name="codex_planning", operation_id="planning:" + run.run_id,
        backend_kind="codex_sdk", worker_id="worker-capacity",
    )
    try:
        episode = RecoveryEpisode(run=run, store=store)
        first = episode.record_failure(operation_id="planning:" + run.run_id, error=_capacity_error("turn-capacity-1"))
        second = episode.record_failure(operation_id="planning:" + run.run_id, error=_capacity_error("turn-capacity-2"))
        duplicate = episode.record_failure(operation_id="planning:" + run.run_id, error=_capacity_error("turn-capacity-2"))
        assert first.action.value == "wait_retry"
        assert second.action.value == "service_wait"
        assert duplicate.action.value == "service_wait"
        assert duplicate.next_check_at == second.next_check_at
        status = store.public_status(run.run_id)
        episode = status["recovery"]["episodes"][0]
        assert episode["capacity_attempts"] == 2
        assert episode["state"] == "service_wait"
        assert episode["observations"][0]["observation"]["worker_id"] == "worker-capacity"
        assert episode["observations"][0]["observation"]["attempt"] == 1
        assert len(episode["observations"]) == 2
        assert episode["decisions"][-1]["decision"]["action"] == "service_wait"
        assert any(event["event_type"] == "recovery_decision_recorded" for event in status["events"])
        assert any(event["event_type"] == "fault_observed" for event in status["events"])
        diagnostic = episode["diagnostic"]
        assert diagnostic["incident"]["family"] == "capacity"
        assert diagnostic["current_action"] == "service_wait"
        assert diagnostic["budget"]["next_check_at"]
        assert diagnostic["next_recovery_condition"].startswith("wait until next_check_at")
    finally:
        store.close()


def test_unknown_failures_track_durable_no_progress_attempts(tmp_path: Path) -> None:
    root = tmp_path / "control"
    store = Store.open(root, create=True)
    run = _run(tmp_path)
    store.create_run(run, "start:" + run.run_id)
    try:
        decisions = [
            RecoveryEpisode(run=run, store=store).record_failure(
                operation_id="start:" + run.run_id,
                error=_unknown_error(f"turn-unknown-{index}"),
            )
            for index in range(3)
        ]
        assert [item.action.value for item in decisions] == ["blocked", "blocked", "blocked"]
        episode = store.recovery_for_run(run.run_id)["episodes"][0]
        assert episode["same_thread_attempts"] == 3
        assert episode["no_progress_attempts"] == 3
        assert decisions[-1].reason == "no_progress_budget_exhausted"
    finally:
        store.close()


def test_verified_progress_is_persisted_and_does_not_count_as_no_progress(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "control", create=True)
    run = _run(tmp_path)
    store.create_run(run, "start:" + run.run_id)
    try:
        decision = RecoveryEpisode(run=run, store=store).record_failure(
            operation_id="start:" + run.run_id,
            error=_unknown_error("turn-progress", "ticket-plan:v1"),
        )
        assert decision.action.value == "blocked"
        assert decision.reason == "fault_family:unknown"
        episode = store.recovery_for_run(run.run_id)["episodes"][0]
        assert episode["last_verified_progress"] == "ticket-plan:v1"
        assert episode["no_progress_attempts"] == 0

        RecoveryEpisode(run=run, store=store).record_failure(
            operation_id="start:" + run.run_id, error=_unknown_error("turn-no-progress"),
        )
        episode = store.recovery_for_run(run.run_id)["episodes"][0]
        assert episode["last_verified_progress"] == "ticket-plan:v1"
        assert episode["no_progress_attempts"] == 1
    finally:
        store.close()


def test_duplicate_failure_observation_reserves_no_progress_once(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "control", create=True)
    run = _run(tmp_path)
    store.create_run(run, "start:" + run.run_id)
    error = _unknown_error("turn-duplicate")
    try:
        first = RecoveryEpisode(run=run, store=store).record_failure(
            operation_id="start:" + run.run_id, error=error,
        )
        replay = RecoveryEpisode(run=run, store=store).record_failure(
            operation_id="start:" + run.run_id, error=error,
        )
        assert replay.action == first.action
        episode = store.recovery_for_run(run.run_id)["episodes"][0]
        assert episode["same_thread_attempts"] == 1
        assert episode["no_progress_attempts"] == 1
    finally:
        store.close()


def test_runner_blocks_after_bounded_capacity_service_probes(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "control", create=True)
    run = _run(tmp_path)
    store.create_run(run, "start:" + run.run_id)
    try:
        decisions = []
        for attempt in range(5):
            current = store.find_by_run_id(run.run_id)
            assert current is not None
            transition = RecoveryRuntime(run=current, store=store).transition_failure(
                operation_id="start:" + run.run_id,
                error=_capacity_error(f"turn-capacity-{attempt}"),
            )
            decisions.append(transition.decision.action.value)
        assert decisions == ["wait_retry", "service_wait", "service_wait", "service_wait", "blocked"]
        blocked = store.find_by_run_id(run.run_id)
        assert blocked is not None and blocked.state == "blocked"
        assert RecoveryEpisode(run=blocked, store=store).waits()
        episode = store.recovery_for_run(run.run_id)["episodes"][0]
        assert episode["capacity_attempts"] == 5
        assert episode["decisions"][-1]["decision"]["reason"] == "capacity_probe_budget_exhausted"
        assert episode["wait_deadline"] is None
    finally:
        store.close()


def test_recovery_runtime_persists_transition_state_once(tmp_path: Path) -> None:
    root = tmp_path / "control"
    store = Store.open(root, create=True)
    run = _run(tmp_path)
    store.create_run(run, "start:" + run.run_id)
    try:
        transition = RecoveryRuntime(run=run, store=store).transition_failure(
            operation_id="planning:" + run.run_id,
            error=_capacity_error("turn-transition"),
        )

        assert transition.waits is True
        assert transition.state == "wait_retry"
        assert store.find_by_run_id(run.run_id).state == "wait_retry"
        assert not any(event["event_type"] == "recovery_blocked" for event in store.events_for_run(run.run_id))
    finally:
        store.close()


def test_unreconciled_external_result_is_durable_block_and_never_retries(tmp_path: Path) -> None:
    root = tmp_path / "control"
    store = Store.open(root, create=True)
    run = _run(tmp_path)
    store.create_run(run, "start:" + run.run_id)
    operation = "planning:" + run.run_id
    worker_id = "codex_sdk:" + run.run_id + ":codex_planning"
    store.begin_stage(
        run.run_id, step_name="codex_planning", operation_id=operation,
        backend_kind="codex_sdk", worker_id=worker_id,
    )
    store.record_codex_turn_started(
        run.run_id, operation, thread_id="thread-unreadable", turn_id="turn-unreadable",
        step_name="codex_planning", worker_id=worker_id,
    )
    try:
        # Preserve the earlier accepted/unknown execution evidence that caused
        # the read-only reconciliation attempt.
        observed = workflow._record_recovery_failure(
            run=run,
            store=store,
            operation_id=operation,
            error=RunnerError(
                "sdk_execution_failed", "response became unreadable",
                details={"fault_observation": {
                    "message": "stream disconnected", "source": "sdk_exception",
                    "structured": True, "request_admission": "accepted",
                    "execution_outcome": "unknown", "thread_id": "thread-unreadable",
                    "turn_id": "turn-unreadable",
                }},
            ),
        )
        assert observed.action.value == "observe"
        before_workers = len(store.workers_for_run(run.run_id))
        before_same_thread_attempts = store.recovery_for_run(run.run_id)["episodes"][0]["same_thread_attempts"]
        before_capacity_attempts = store.recovery_for_run(run.run_id)["episodes"][0]["capacity_attempts"]

        transition = RecoveryRuntime(run=run, store=store).transition_failure(
            operation_id="start:" + run.run_id,
            error=RunnerError(
                "recovery_blocked", "the persisted SDK thread could not be reconciled",
                details={"inspection_error": "sdk_thread_read_failed",
                         "thread_id": "thread-unreadable", "turn_id": "turn-unreadable"},
            ),
        )

        assert transition.state == "blocked"
        assert transition.decision.action.value == "blocked"
        assert transition.decision.reason == "external_result_unreconciled"
        assert transition.decision.preconditions == (
            "manual_reconciliation_required", "do_not_create_worker",
        )
        status = store.public_status(run.run_id)
        episode = status["recovery"]["episodes"][0]
        assert episode["same_thread_attempts"] == before_same_thread_attempts
        assert episode["capacity_attempts"] == before_capacity_attempts
        assert episode["observations"][0]["observation"]["execution_outcome"] == "unknown"
        assert episode["decisions"][-1]["decision"]["action"] == "blocked"
        assert episode["decisions"][-1]["decision"]["reason"] == "external_result_unreconciled"
        assert "manual_reconciliation_required" in episode["decisions"][-1]["decision"]["preconditions"]
        assert "inspection_error:sdk_thread_read_failed" in episode["decisions"][-1]["decision"]["evidence"]
        assert len(store.workers_for_run(run.run_id)) == before_workers

        # Replaying the same start/recovery check remains blocked and does not
        # turn the old unknown result into a new same-thread attempt.
        reopened = Store.open(root, create=False)
        try:
            current = reopened.find_by_run_id(run.run_id)
            assert current is not None
            replay = RecoveryRuntime(run=current, store=reopened).transition_failure(
                operation_id="start:" + run.run_id,
                error=RunnerError(
                    "recovery_blocked", "the persisted SDK thread could not be reconciled",
                    details={"inspection_error": "sdk_thread_read_failed",
                             "thread_id": "thread-unreadable", "turn_id": "turn-unreadable"},
                ),
            )
            assert replay.state == "blocked"
            assert replay.decision.action.value == "blocked"
            persisted = reopened.recovery_for_run(run.run_id)["episodes"][0]
            assert persisted["same_thread_attempts"] == before_same_thread_attempts
            assert persisted["capacity_attempts"] == before_capacity_attempts
            assert persisted["decisions"][-1]["decision"]["action"] == "blocked"
            assert len(reopened.workers_for_run(run.run_id)) == before_workers
        finally:
            reopened.close()
    finally:
        store.close()


def test_exact_terminal_readback_reopens_blocked_capacity_episode_without_new_budget(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "control", create=True)
    run = _run(tmp_path)
    store.create_run(run, "start:" + run.run_id)
    operation = "planning:" + run.run_id
    worker_id = "codex_sdk:" + run.run_id + ":codex_planning"
    store.begin_stage(
        run.run_id, step_name="codex_planning", operation_id=operation,
        backend_kind="codex_sdk", worker_id=worker_id,
    )
    store.record_codex_turn_started(
        run.run_id, operation, thread_id="thread-capacity", turn_id="turn-capacity-2",
        step_name="codex_planning", worker_id=worker_id,
    )
    try:
        for index in range(3):
            RecoveryEpisode(run=run, store=store).record_failure(
                operation_id="start:" + run.run_id,
                error=RunnerError("sdk_rate_limited", "capacity temporarily unavailable", details={
                    "fault_observation": {
                        "message": "capacity temporarily unavailable",
                        "source": "sdk_result", "structured": True,
                        "request_admission": "accepted", "execution_outcome": "unknown",
                        "thread_id": "thread-capacity", "turn_id": f"turn-capacity-{index}",
                    },
                }),
            )
        blocked = RecoveryRuntime(run=run, store=store).transition_failure(
            operation_id="start:" + run.run_id,
            error=RunnerError("recovery_blocked", "SDK thread was unreadable",
                              details={"inspection_error": "sdk_thread_read_failed"}),
        )
        assert blocked.decision.reason == "external_result_unreconciled"
        before = store.recovery_for_run(run.run_id)["episodes"][0]
        assert before["capacity_attempts"] == 3
        assert RecoveryRuntime(run=run, store=store).reconcile_failed_turn(
            operation_id="start:" + run.run_id,
            thread_id="different-thread", turn_id="turn-capacity-2",
        ) is None
        assert RecoveryRuntime(run=run, store=store).reconcile_failed_turn(
            operation_id="start:" + run.run_id,
            thread_id="thread-capacity", turn_id="turn-capacity-1",
        ) is None
        assert store.recovery_for_run(run.run_id)["episodes"][0]["decisions"][-1]["decision"]["action"] == "blocked"

        current = store.find_by_run_id(run.run_id)
        assert current is not None
        transition = RecoveryRuntime(run=current, store=store).reconcile_failed_turn(
            operation_id="start:" + run.run_id,
            thread_id="thread-capacity", turn_id="turn-capacity-2",
        )
        assert transition is not None and transition.state == "service_wait"
        episode = store.recovery_for_run(run.run_id)["episodes"][0]
        assert episode["capacity_attempts"] == 3
        assert episode["decisions"][-1]["decision"]["action"] == "service_wait"
        deadline = episode["wait_deadline"]
        assert deadline

        replay = RecoveryRuntime(run=store.find_by_run_id(run.run_id), store=store).reconcile_failed_turn(
            operation_id="start:" + run.run_id,
            thread_id="thread-capacity", turn_id="turn-capacity-2",
        )
        assert replay is not None and replay.state == "service_wait"
        replayed = store.recovery_for_run(run.run_id)["episodes"][0]
        assert replayed["capacity_attempts"] == 3
        assert replayed["wait_deadline"] == deadline
        assert len(replayed["decisions"]) == len(episode["decisions"])
    finally:
        store.close()


def test_runner_observes_accepted_unknown_result_before_retry(tmp_path: Path) -> None:
    root = tmp_path / "control"
    store = Store.open(root, create=True)
    run = _run(tmp_path)
    store.create_run(run, "start:" + run.run_id)
    operation = "planning:" + run.run_id
    worker = "codex_sdk:" + run.run_id + ":codex_planning"
    store.begin_stage(run.run_id, step_name="codex_planning", operation_id=operation, backend_kind="codex_sdk", worker_id=worker)
    store.record_codex_turn_started(
        run.run_id, operation, thread_id="thread-live", turn_id="turn-live",
        step_name="codex_planning", worker_id=worker,
    )
    try:
        decision = workflow._record_recovery_failure(
            run=run,
            store=store,
            operation_id=operation,
            error=RunnerError(
                "sdk_execution_failed",
                "connection closed before the response was readable",
                details={
                    "fault_observation": {
                        "message": "stream disconnected",
                        "source": "sdk_exception",
                        "structured": True,
                        "request_admission": "accepted",
                        "execution_outcome": "unknown",
                        "thread_id": "thread-live",
                        "turn_id": "turn-live",
                    }
                },
            ),
        )
        assert decision.action.value == "observe"
        status = store.public_status(run.run_id)
        observation = status["recovery"]["episodes"][0]["observations"][0]["observation"]
        assert observation["request_admission"] == "accepted"
        assert observation["execution_outcome"] == "unknown"
        assert status["recovery"]["episodes"][0]["decisions"][0]["decision"]["action"] == "observe"
        diagnostic = status["recovery"]["episodes"][0]["diagnostic"]
        assert diagnostic["thread"]["unconfirmed_execution"] is True
        assert diagnostic["next_recovery_condition"].startswith("reconcile the persisted execution")
    finally:
        store.close()


def test_recovery_uses_latest_worker_stage_for_episode_identity(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "control", create=True)
    run = _run(tmp_path)
    store.create_run(run, "start:" + run.run_id)
    store.begin_stage(
        run.run_id,
        step_name="codex_grill",
        operation_id="grill:" + run.run_id,
        backend_kind="codex_sdk",
        worker_id="worker-grill",
    )
    try:
        first = workflow._record_recovery_failure(
            run=run,
            store=store,
            operation_id="start:" + run.run_id,
            error=_capacity_error("turn-grill-1"),
        )
        second = workflow._record_recovery_failure(
            run=run,
            store=store,
            operation_id="start:" + run.run_id,
            error=_capacity_error("turn-grill-2"),
        )
        assert first.action.value == "wait_retry"
        assert second.action.value == "service_wait"
        episodes = store.recovery_for_run(run.run_id)["episodes"]
        assert len(episodes) == 1
        assert episodes[0]["stage"] == "codex_grill"
        assert episodes[0]["capacity_attempts"] == 2
    finally:
        store.close()


def test_recovery_keeps_distinct_requests_on_one_turn(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "control", create=True)
    run = _run(tmp_path)
    store.create_run(run, "start:" + run.run_id)
    try:
        for request_id in ("request-one", "request-two"):
            workflow._record_recovery_failure(
                run=run, store=store, operation_id="start:" + run.run_id,
                error=RunnerError(
                    "sdk_rate_limited", "capacity temporarily unavailable",
                    details={"fault_observation": {
                        "message": "capacity temporarily unavailable",
                        "source": "sdk_result", "structured": True,
                        "request_admission": "rejected", "execution_outcome": "failed",
                        "thread_id": "thread-capacity", "turn_id": "turn-capacity",
                        "request_id": request_id,
                    }},
                ),
            )
        episode = store.recovery_for_run(run.run_id)["episodes"][0]
        assert episode["capacity_attempts"] == 2
        assert [item["observation"]["request_id"] for item in episode["observations"]] == [
            "request-one", "request-two",
        ]
    finally:
        store.close()


def test_unidentified_rejected_request_cannot_bypass_recovery_budget(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "control", create=True)
    run = _run(tmp_path)
    store.create_run(run, "start:" + run.run_id)
    try:
        error = RunnerError(
            "sdk_rate_limited", "capacity temporarily unavailable",
            details={"fault_observation": {
                "message": "capacity temporarily unavailable",
                "source": "sdk_result", "structured": True,
                "request_admission": "rejected", "execution_outcome": "failed",
            }},
        )
        first = workflow._record_recovery_failure(
            run=run, store=store, operation_id="start:" + run.run_id, error=error,
        )
        second = workflow._record_recovery_failure(
            run=run, store=store, operation_id="start:" + run.run_id, error=error,
        )
        assert first.action.value == second.action.value == "observe"
        assert first.reason == "attempt_identity_missing"
        assert store.recovery_for_run(run.run_id)["episodes"][0]["capacity_attempts"] == 0
    finally:
        store.close()


def test_wait_retry_is_durable_and_does_not_issue_a_worker_early(tmp_path: Path) -> None:
    root = tmp_path / "control"
    store = Store.open(root, create=True)
    run = _run(tmp_path)
    store.create_run(run, "start:" + run.run_id)
    try:
        decision = workflow._record_recovery_failure(
            run=run, store=store, operation_id="planning:" + run.run_id, error=_capacity_error()
        )
        assert decision.action.value == "wait_retry"
        store.fail_run(run.run_id, "start:" + run.run_id, state="wait_retry")
        waiting = store.find_by_run_id(run.run_id)
        assert waiting is not None
        assert workflow._recovery_waits(run=waiting, store=store, config=None)
        assert store.public_status(run.run_id)["run"]["state"] == "wait_retry"
    finally:
        store.close()


def test_corrupt_persisted_recovery_deadline_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "control"
    store = Store.open(root, create=True)
    run = _run(tmp_path)
    store.create_run(run, "start:" + run.run_id)
    try:
        workflow._record_recovery_failure(
            run=run, store=store, operation_id="start:" + run.run_id,
            error=_capacity_error("turn-invalid-deadline"),
        )
        episode = store.recovery_for_run(run.run_id)["episodes"][0]
        store.upsert_recovery_episode(
            episode_id=episode["episode_id"], run_id=run.run_id,
            operation_kind=episode["operation_kind"], stage=episode["stage"],
            generation=episode["generation"], state="wait_retry",
            counters={key: episode[key] for key in (
                "same_thread_attempts", "capacity_attempts", "route_probe_attempts",
                "clean_probe_attempts", "migration_attempts", "no_progress_attempts",
            )},
            retry_deadline="not-a-timestamp",
        )
        store.fail_run(run.run_id, "start:" + run.run_id, state="wait_retry")

        with pytest.raises(RunnerError) as raised:
            RecoveryRuntime.wait_record(store=store, run_id=run.run_id)
        assert raised.value.code == "recovery_deadline_invalid"

        current = store.find_by_run_id(run.run_id)
        assert current is not None
        assert RecoveryEpisode(run=current, store=store).waits() is False
        assert store.find_by_run_id(run.run_id).state == "blocked"
        assert any(
            event["event_type"] == "recovery_wait_invalid"
            for event in store.events_for_run(run.run_id)
        )
    finally:
        store.close()


def test_reconciled_turn_with_corrupt_recovery_deadline_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "control"
    store = Store.open(root, create=True)
    run = _run(tmp_path)
    store.create_run(run, "start:" + run.run_id)
    try:
        for turn_id in ("turn-reconcile-1", "turn-reconcile-2"):
            workflow._record_recovery_failure(
                run=run, store=store, operation_id="start:" + run.run_id,
                error=_capacity_error(turn_id),
            )
        episode = store.recovery_for_run(run.run_id)["episodes"][0]
        store.upsert_recovery_episode(
            episode_id=episode["episode_id"], run_id=run.run_id,
            operation_kind=episode["operation_kind"], stage=episode["stage"],
            generation=episode["generation"], state="service_wait",
            counters={key: episode[key] for key in (
                "same_thread_attempts", "capacity_attempts", "route_probe_attempts",
                "clean_probe_attempts", "migration_attempts", "no_progress_attempts",
            )},
            wait_deadline="not-a-timestamp",
        )
        store.fail_run(run.run_id, "start:" + run.run_id, state="service_wait")
        current = store.find_by_run_id(run.run_id)
        assert current is not None

        with pytest.raises(RunnerError) as raised:
            RecoveryRuntime(run=current, store=store).reconcile_failed_turn(
                operation_id="start:" + run.run_id,
                thread_id="thread-capacity", turn_id="turn-reconcile-2",
            )

        assert raised.value.code == "recovery_deadline_invalid"
        assert store.find_by_run_id(run.run_id).state == "blocked"
        invalid_events = [
            event for event in store.events_for_run(run.run_id)
            if event["event_type"] == "recovery_wait_invalid"
        ]
        assert invalid_events[-1]["payload"] == {
            "action": "service_wait", "reason": "invalid_persisted_deadline",
        }
    finally:
        store.close()


def test_missing_persisted_recovery_deadline_fails_closed_at_start_seam(tmp_path: Path) -> None:
    root = tmp_path / "control"
    store = Store.open(root, create=True)
    run = _run(tmp_path)
    store.create_run(run, "start:" + run.run_id)
    try:
        workflow._record_recovery_failure(
            run=run, store=store, operation_id="start:" + run.run_id,
            error=_capacity_error("turn-missing-deadline"),
        )
        episode = store.recovery_for_run(run.run_id)["episodes"][0]
        store.upsert_recovery_episode(
            episode_id=episode["episode_id"], run_id=run.run_id,
            operation_kind=episode["operation_kind"], stage=episode["stage"],
            generation=episode["generation"], state="wait_retry",
            counters={key: episode[key] for key in (
                "same_thread_attempts", "capacity_attempts", "route_probe_attempts",
                "clean_probe_attempts", "migration_attempts", "no_progress_attempts",
            )},
        )
        store.fail_run(run.run_id, "start:" + run.run_id, state="wait_retry")

        current = store.find_by_run_id(run.run_id)
        assert current is not None
        assert RecoveryEpisode(run=current, store=store).waits() is False
        assert store.find_by_run_id(run.run_id).state == "blocked"
        invalid_events = [
            event for event in store.events_for_run(run.run_id)
            if event["event_type"] == "recovery_wait_invalid"
        ]
        assert invalid_events[-1]["payload"]["reason"] == "missing_persisted_deadline"
    finally:
        store.close()


@pytest.mark.parametrize(
    ("requested_state", "expected_action", "expected_reason"),
    [
        ("pause_requested", "wait_for_config", "user_paused"),
        ("cancel_requested", "blocked", "user_cancelled"),
    ],
)
def test_recovery_control_precedes_budget_reservation(
    tmp_path: Path, requested_state: str, expected_action: str, expected_reason: str,
) -> None:
    store = Store.open(tmp_path / requested_state, create=True)
    run = _run(tmp_path)
    store.create_run(run, "start:" + run.run_id)
    try:
        store.request_control(run.run_id, requested_state)
        decision = RecoveryEpisode(run=run, store=store).record_failure(
            operation_id="start:" + run.run_id,
            error=_capacity_error("turn-controlled"),
        )
        assert decision.action.value == expected_action
        assert decision.reason == expected_reason
        episode = store.recovery_for_run(run.run_id)["episodes"][0]
        assert episode["capacity_attempts"] == 0
        assert episode["same_thread_attempts"] == 0
        assert episode["decisions"][-1]["decision"]["preconditions"]
        assert not any(event["event_type"] == "retry_scheduled" for event in store.events_for_run(run.run_id))
    finally:
        store.close()


@pytest.mark.parametrize("failure_count, expected_action", [(1, "wait_retry"), (2, "service_wait")])
def test_drive_wakes_once_after_persisted_retry_deadline(tmp_path: Path, monkeypatch, failure_count: int, expected_action: str) -> None:
    root = tmp_path / "control"
    store = Store.open(root, create=True)
    run = _run(tmp_path)
    store.create_run(run, "start:" + run.run_id)
    for index in range(failure_count):
        workflow._record_recovery_failure(
            run=run, store=store, operation_id="start:" + run.run_id,
            error=_capacity_error(f"turn-capacity-{index}"),
        )
    episode = store.recovery_for_run(run.run_id)["episodes"][0]
    deadline = (datetime.now(timezone.utc) + timedelta(seconds=0.04)).isoformat()
    store.upsert_recovery_episode(
        episode_id=episode["episode_id"], run_id=run.run_id,
        operation_kind=episode["operation_kind"], stage=episode["stage"],
        generation=episode["generation"], state="wait_retry",
        counters={key: episode[key] for key in (
            "same_thread_attempts", "capacity_attempts", "route_probe_attempts",
            "clean_probe_attempts", "migration_attempts", "no_progress_attempts",
        )},
        retry_deadline=deadline if expected_action == "wait_retry" else None,
        wait_deadline=deadline if expected_action == "service_wait" else None,
    )
    decision = dict(episode["decisions"][-1]["decision"])
    decision["next_check_at"] = deadline
    store.record_recovery_decision(
        decision_id=episode["episode_id"] + ":timer-test",
        episode_id=episode["episode_id"], decision=decision,
    )
    store.fail_run(run.run_id, "start:" + run.run_id, state=expected_action)
    store.close()

    calls = []

    def start_once(**kwargs):
        calls.append(kwargs["run_id"])
        current_store = Store.open(root, create=False)
        try:
            if len(calls) == 2:
                current_store.set_run_state(run.run_id, "completed")
            return {"created": False, **current_store.public_status(run.run_id)}
        finally:
            current_store.close()

    monkeypatch.setattr(workflow, "start", start_once)
    result = workflow.drive(
        brief_file=tmp_path / "brief.md", config_file=tmp_path / "config.json",
        control_root=root, launch_key=run.launch_key, run_id=run.run_id,
    )

    assert calls == [run.run_id, run.run_id]
    assert result["run"]["state"] == "completed"
    assert sum(event["event_type"] == "recovery_timer_woke" for event in result["events"]) == 1


@pytest.mark.parametrize(
    ("wait_action", "deadline_field", "deadline_value", "expected_reason"),
    [
        ("wait_retry", "retry_deadline", None, "missing_persisted_deadline"),
        ("wait_retry", "retry_deadline", "not-a-timestamp", "invalid_persisted_deadline"),
        ("service_wait", "wait_deadline", None, "missing_persisted_deadline"),
        ("service_wait", "wait_deadline", "not-a-timestamp", "invalid_persisted_deadline"),
    ],
)
def test_public_drive_fails_closed_on_corrupt_persisted_wait(
    tmp_path: Path,
    monkeypatch,
    wait_action: str,
    deadline_field: str,
    deadline_value: str | None,
    expected_reason: str,
) -> None:
    root = tmp_path / "control"
    store = Store.open(root, create=True)
    run = _run(tmp_path)
    store.create_run(run, "start:" + run.run_id)
    for index in range(2 if wait_action == "service_wait" else 1):
        workflow._record_recovery_failure(
            run=run,
            store=store,
            operation_id="start:" + run.run_id,
            error=_capacity_error(f"turn-corrupt-wait-{index}"),
        )
    episode = store.recovery_for_run(run.run_id)["episodes"][0]
    store.upsert_recovery_episode(
        episode_id=episode["episode_id"],
        run_id=run.run_id,
        operation_kind=episode["operation_kind"],
        stage=episode["stage"],
        generation=episode["generation"],
        state=wait_action,
        counters={key: episode[key] for key in (
            "same_thread_attempts", "capacity_attempts", "route_probe_attempts",
            "clean_probe_attempts", "migration_attempts", "no_progress_attempts",
        )},
        retry_deadline=deadline_value if deadline_field == "retry_deadline" else None,
        wait_deadline=deadline_value if deadline_field == "wait_deadline" else None,
    )
    store.fail_run(run.run_id, "start:" + run.run_id, state=wait_action)
    status = store.public_status(run.run_id)
    store.close()

    calls = []

    def start_once(**kwargs):
        calls.append(kwargs["run_id"])
        return {"created": False, **status}

    monkeypatch.setattr(workflow, "start", start_once)
    result = workflow.drive(
        brief_file=tmp_path / "brief.md",
        config_file=tmp_path / "config.json",
        control_root=root,
        launch_key=run.launch_key,
        run_id=run.run_id,
    )

    assert calls == [run.run_id]
    assert result["run"]["state"] == "blocked"
    invalid_events = [
        event for event in result["events"]
        if event["event_type"] == "recovery_wait_invalid"
    ]
    assert invalid_events[-1]["payload"] == {
        "action": wait_action, "reason": expected_reason,
    }


def test_drive_wait_observes_pause_and_cancel_without_starting_another_check(tmp_path: Path, monkeypatch) -> None:
    for requested_state, expected_state in (("pause_requested", "paused"), ("cancel_requested", "cancelled")):
        root = tmp_path / expected_state
        store = Store.open(root, create=True)
        run = _run(tmp_path)
        store.create_run(run, "start:" + run.run_id)
        workflow._record_recovery_failure(
            run=run, store=store, operation_id="start:" + run.run_id, error=_capacity_error()
        )
        store.fail_run(run.run_id, "start:" + run.run_id, state="wait_retry")
        store.close()
        calls = []

        def start_waiting(**kwargs):
            calls.append(kwargs["run_id"])
            current_store = Store.open(root, create=False)
            try:
                return {"created": False, **current_store.public_status(run.run_id)}
            finally:
                current_store.close()

        def request_control(_seconds):
            control_store = Store.open(root, create=False)
            try:
                control_store.request_control(run.run_id, requested_state)
            finally:
                control_store.close()

        monkeypatch.setattr(workflow, "start", start_waiting)
        monkeypatch.setattr(workflow.time, "sleep", request_control)
        result = workflow.drive(
            brief_file=tmp_path / "brief.md", config_file=tmp_path / "config.json",
            control_root=root, launch_key=run.launch_key, run_id=run.run_id,
        )

        assert calls == [run.run_id]
        assert result["run"]["state"] == expected_state
        assert any(
            event["event_type"] == "control_applied"
            and event["payload"].get("during") == "recovery_wait"
            for event in result["events"]
        )


def _fault_error(message: str, turn_id: str) -> RunnerError:
    return RunnerError(
        "sdk_failure",
        message,
        details={
            "fault_observation": {
                "message": message,
                "source": "sdk_result",
                "structured": True,
                "request_admission": "rejected",
                "execution_outcome": "failed",
                "thread_id": "thread-recovery",
                "turn_id": turn_id,
            }
        },
    )


def test_route_probe_and_clean_migration_budgets_are_durable_and_bounded(tmp_path: Path) -> None:
    for family, messages, expected in (
        (
            "route",
            ["model does not exist or you do not have access"] * 3,
            ["use_approved_route", "wait_for_config", "wait_for_config"],
        ),
        (
            "encrypted",
            ["encrypted item-id mismatch"] * 3,
            ["probe_clean_context", "request_clean_migration", "blocked"],
        ),
    ):
        root = tmp_path / family
        store = Store.open(root, create=True)
        run = _run(tmp_path)
        store.create_run(run, "start:" + run.run_id)
        try:
            decisions = []
            for index, message in enumerate(messages):
                decisions.append(
                    workflow._record_recovery_failure(
                        run=run,
                        store=store,
                        operation_id="start:" + run.run_id,
                        error=_fault_error(message, f"turn-{family}-{index}"),
                    ).action.value
                )
            assert decisions == expected
            episode = store.recovery_for_run(run.run_id)["episodes"][0]
            if family == "route":
                assert episode["route_probe_attempts"] == 1
            else:
                assert episode["clean_probe_attempts"] == 1
                assert episode["migration_attempts"] == 1
        finally:
            store.close()


def test_failed_worker_result_keeps_structured_fault_family() -> None:
    from spec_runner.recovery import observation_from_worker_result

    observed = observation_from_worker_result(
        operation_kind="codex_turn",
        result={
            "thread_id": "thread-1",
            "turn_id": "turn-1",
            "status": "failed",
            "error": None,
            "fault_observation": {
                "message": "capacity temporarily unavailable",
                "source": "sdk_result",
                "structured": True,
                "request_admission": "accepted",
                "execution_outcome": "failed",
            },
        },
    )
    assert observed is not None
    assert observed.family == "capacity"
    assert observed.thread_id == "thread-1"
    assert observed.turn_id == "turn-1"
    assert observed.execution_outcome == "failed"
