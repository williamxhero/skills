from __future__ import annotations

from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone

import pytest

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
            run=run, store=store, operation_id="planning:" + run.run_id, error=_capacity_error("turn-capacity-1")
        )
        second = workflow._record_recovery_failure(
            run=run, store=store, operation_id="planning:" + run.run_id, error=_capacity_error("turn-capacity-2")
        )
        duplicate = workflow._record_recovery_failure(
            run=run, store=store, operation_id="planning:" + run.run_id, error=_capacity_error("turn-capacity-2")
        )
        assert first.action.value == "wait_retry"
        assert second.action.value == "service_wait"
        assert duplicate.action.value == "service_wait"
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
