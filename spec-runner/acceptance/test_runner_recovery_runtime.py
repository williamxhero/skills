from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from spec_runner.errors import RunnerError
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


def _capacity_error() -> RunnerError:
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
                "turn_id": "turn-capacity",
            }
        },
    )


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
        first = workflow._record_recovery_failure(
            run=run, store=store, operation_id="planning:" + run.run_id, error=_capacity_error()
        )
        second = workflow._record_recovery_failure(
            run=run, store=store, operation_id="planning:" + run.run_id, error=_capacity_error()
        )
        assert first.action.value == "wait_retry"
        assert second.action.value == "service_wait"
        status = store.public_status(run.run_id)
        episode = status["recovery"]["episodes"][0]
        assert episode["capacity_attempts"] == 2
        assert episode["state"] == "service_wait"
        assert episode["observations"][0]["observation"]["worker_id"] == "worker-capacity"
        assert episode["observations"][0]["observation"]["attempt"] == 1
        assert len(episode["observations"]) == 1
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
