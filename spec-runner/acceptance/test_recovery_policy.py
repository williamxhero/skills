from __future__ import annotations

from datetime import datetime, timezone

import pytest

from spec_runner.recovery import (
    FaultFamily,
    FaultObservation,
    RecoveryAction,
    RecoveryPolicy,
    RecoverySnapshot,
    classify_fault,
    decide_recovery,
    observation_from_error,
    recovery_diagnostic,
)


@pytest.mark.parametrize(
    ("message", "status", "expected"),
    [
        ("encrypted item-id mismatch", None, FaultFamily.ENCRYPTED_ITEM_MISMATCH),
        ("capacity temporarily unavailable", 503, FaultFamily.CAPACITY),
        ("stream disconnected", None, FaultFamily.STREAM_DISCONNECTED),
        ("model does not exist or you do not have access", 404, FaultFamily.ROUTE_NOT_FOUND),
        ("Fast price not configured", None, FaultFamily.FAST_NOT_CONFIGURED),
        ("permission denied", 403, FaultFamily.AUTHORIZATION),
    ],
)
def test_fault_classification_covers_public_error_families(message, status, expected):
    family, _ = classify_fault(message=message, http_status=status)
    assert family is expected


def test_observation_preserves_unknown_admission_and_redacts_secret():
    class Error(Exception):
        code = "stream_error"
        message = "stream disconnected token=secret-value"
        details = {"turn_id": None, "request_id": "req-1"}

    observation = observation_from_error(
        operation_kind="implementation",
        error=Error(),
        run_id="run-1",
        stage="implement",
        requested_model="model-a",
    )
    assert observation.family == FaultFamily.STREAM_DISCONNECTED.value
    assert observation.request_admission == "unknown"
    assert observation.execution_outcome == "unknown"
    assert "secret-value" not in (observation.message or "")
    assert observation.confidence == "fallback_text"
    assert observation.fingerprint


def test_runtime_version_requires_runtime_observation_not_client_config():
    configured = observation_from_error(
        operation_kind="implementation",
        error={"message": "temporary failure", "client_version": "configured-client/1.2"},
    )
    observed = observation_from_error(
        operation_kind="implementation",
        error={"message": "temporary failure", "client_version": "configured-client/1.2", "runtime_version": "runtime/3.4"},
    )

    assert configured.runtime_version is None
    assert observed.runtime_version == "runtime/3.4"


def test_capacity_retry_after_overrides_local_delay():
    timestamp = datetime(2026, 9, 25, tzinfo=timezone.utc)
    observation = FaultObservation(
        operation_kind="implementation", stage="implement",
        family=FaultFamily.CAPACITY.value, reason="capacity_or_transient",
        retry_after_seconds=17.5,
    )
    decision = decide_recovery(
        RecoverySnapshot(run_id="run-1", operation_kind="implementation", stage="implement"),
        [observation], RecoveryPolicy(retry_delay_seconds=5), now=timestamp,
    )
    assert decision.action is RecoveryAction.WAIT_RETRY
    assert decision.next_check_at == "2026-09-25T00:00:17.500000+00:00"


def test_decision_is_pure_and_capacity_becomes_service_wait_after_budget():
    timestamp = datetime(2026, 9, 25, tzinfo=timezone.utc)
    observation = FaultObservation(
        operation_kind="implementation", stage="implement",
        family=FaultFamily.CAPACITY.value, reason="capacity_or_transient",
    )
    snapshot = RecoverySnapshot(
        run_id="run-1", operation_kind="implementation", stage="implement",
        capacity_attempts=2,
    )
    decision = decide_recovery(snapshot, [observation], RecoveryPolicy(capacity_retries=2), now=timestamp)
    assert decision.action is RecoveryAction.SERVICE_WAIT
    assert decision.next_check_at == "2026-09-25T00:01:00+00:00"
    assert decision.remaining_budget["capacity_retries"] == 0
    assert decision.evidence


@pytest.mark.parametrize(
    ("control", "active", "admission", "outcome", "expected"),
    [
        ("cancel_requested", False, "unknown", "unknown", RecoveryAction.BLOCKED),
        ("pause_requested", False, "unknown", "unknown", RecoveryAction.WAIT_FOR_CONFIG),
        (None, True, "accepted", "unknown", RecoveryAction.OBSERVE),
        (None, False, "accepted", "completed", RecoveryAction.ADOPT_RESULT),
    ],
)
def test_control_and_unknown_result_precede_fault_policy(control, active, admission, outcome, expected):
    observation = FaultObservation(
        operation_kind="implementation", stage="implement",
        family=FaultFamily.ENCRYPTED_ITEM_MISMATCH.value,
        reason="encrypted_item_mismatch",
    )
    snapshot = RecoverySnapshot(
        run_id="run-1", operation_kind="implementation", stage="implement",
        user_control=control, active_execution=active,
        request_admission=admission, execution_outcome=outcome,
    )
    assert decide_recovery(snapshot, [observation]).action is expected


def test_encrypted_mismatch_is_bounded_and_never_infinite_thread_creation():
    observation = FaultObservation(
        operation_kind="implementation", stage="implement",
        family=FaultFamily.ENCRYPTED_ITEM_MISMATCH.value,
        reason="encrypted_item_mismatch",
    )
    first = decide_recovery(
        RecoverySnapshot(run_id="r", operation_kind="implementation", stage="implement"),
        [observation], RecoveryPolicy(),
    )
    exhausted = decide_recovery(
        RecoverySnapshot(run_id="r", operation_kind="implementation", stage="implement", clean_probe_attempts=1, migration_attempts=1),
        [observation], RecoveryPolicy(),
    )
    assert first.action is RecoveryAction.PROBE_CLEAN_CONTEXT
    assert exhausted.action is RecoveryAction.BLOCKED


def test_recovery_diagnostic_does_not_call_waits_or_blocks_cleanup_debt():
    base = {
        "run_id": "run-1", "operation_kind": "codex_turn", "stage": "implementation",
        "generation": 0, "state": "service_wait",
    }
    observation = {"observation": {"family": "capacity", "reason": "capacity_or_transient"}}
    decision = {"decision": {"action": "service_wait", "remaining_budget": {}}}

    waiting = recovery_diagnostic(
        episode=base, observations=[observation], decisions=[decision], run_state="service_wait",
    )
    blocked = recovery_diagnostic(
        episode={**base, "state": "blocked"}, observations=[observation], decisions=[decision], run_state="blocked",
    )
    cleanup = recovery_diagnostic(
        episode={**base, "state": "cleanup_pending"}, observations=[observation], decisions=[decision], run_state="cleanup_pending",
    )

    assert waiting["cleanup_debt"]["present"] is False
    assert blocked["cleanup_debt"]["present"] is False
    assert cleanup["cleanup_debt"]["present"] is True


@pytest.mark.parametrize(
    ("action", "expected_condition"),
    [
        ("observe", "reconcile the persisted execution"),
        ("service_wait", "wait until next_check_at"),
        ("wait_for_config", "approved configuration"),
        ("blocked", "operator must resolve"),
    ],
)
def test_recovery_diagnostic_explains_next_condition_and_preserves_unknown_execution(action, expected_condition):
    diagnostic = recovery_diagnostic(
        episode={
            "run_id": "run-1", "operation_kind": "codex_turn", "stage": "implementation",
            "generation": 0, "state": action, "capacity_attempts": 2,
            "retry_deadline": "2026-09-25T00:00:05+00:00", "wait_deadline": "2026-09-25T00:01:00+00:00",
        },
        observations=[{
            "observed_at": "2026-09-25T00:00:00+00:00",
            "observation": {
                "fingerprint": "fp", "family": "capacity", "reason": "capacity_or_transient",
                "request_admission": "accepted", "execution_outcome": "unknown",
                "thread_id": "thread-1", "turn_id": "turn-1", "confidence": "structured",
                "source": "sdk_exception", "evidence": ["sdk_exception"], "route_scope": "model:tier",
                "sdk_retry_coverage": "unknown",
            },
        }],
        decisions=[{
            "decision": {
                "action": action, "next_check_at": "2026-09-25T00:01:00+00:00",
                "remaining_budget": {"capacity_retries": 0}, "family": "capacity",
            },
        }],
        execution_owner={"worker_id": "worker-1", "state": "running"},
        run_state=action,
    )
    assert diagnostic["schema_version"] == "spec-runner-recovery-diagnostic/v1"
    assert diagnostic["thread"]["unconfirmed_execution"] is True
    assert expected_condition in diagnostic["next_recovery_condition"]
    assert diagnostic["budget"]["remaining"]["capacity_retries"] == 0
    assert diagnostic["sdk_retry"]["coverage"] == "unknown"


def test_recovery_policy_rejects_invalid_budget_configuration_and_records_version():
    with pytest.raises(ValueError, match="non-negative integer"):
        RecoveryPolicy(capacity_retries=-1)
    with pytest.raises(ValueError, match="non-negative integer"):
        RecoveryPolicy(capacity_retries=True)
    with pytest.raises(ValueError, match="finite non-negative number"):
        RecoveryPolicy(retry_delay_seconds=float("nan"))
    with pytest.raises(ValueError, match="finite non-negative number"):
        RecoveryPolicy(service_wait_delay_seconds=float("inf"))

    decision = decide_recovery(
        RecoverySnapshot(run_id="run-1", operation_kind="implementation", stage="implement"),
        [FaultObservation(operation_kind="implementation", stage="implement")],
    )
    assert decision.public()["policy_version"] == "spec-runner-recovery-policy/v1"
