"""Structured fault observations and deterministic recovery policy.

The policy in this module is deliberately side-effect free.  The Runner stores
observations and episodes, then executes the returned decision through its
normal worker and ownership boundaries.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


class RecoveryAction(str, Enum):
    OBSERVE = "observe"
    ADOPT_RESULT = "adopt_result"
    WAIT_RETRY = "wait_retry"
    SERVICE_WAIT = "service_wait"
    RESUME_SAME_THREAD = "resume_same_thread"
    RECONNECT_RUNTIME = "reconnect_runtime"
    USE_APPROVED_ROUTE = "use_approved_route"
    PROBE_CLEAN_CONTEXT = "probe_clean_context"
    REQUEST_CLEAN_MIGRATION = "request_clean_migration"
    WAIT_FOR_CONFIG = "wait_for_config"
    NEEDS_INPUT = "needs_input"
    BLOCKED = "blocked"


class FaultFamily(str, Enum):
    ENCRYPTED_ITEM_MISMATCH = "encrypted_item_mismatch"
    CAPACITY = "capacity"
    STREAM_DISCONNECTED = "stream_disconnected"
    ROUTE_NOT_FOUND = "route_not_found"
    FAST_NOT_CONFIGURED = "fast_not_configured"
    AUTHORIZATION = "authorization"
    BILLING = "billing"
    USER_CANCELLED = "user_cancelled"
    NEEDS_INPUT = "needs_input"
    UNKNOWN = "unknown"


def _text(value: Any, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _redact(value: Any) -> str | None:
    text = _text(value, 160)
    if not text:
        return None
    text = re.sub(r"(?i)(bearer\s+|sk-[A-Za-z0-9_-]+|token|secret|password)(?:\s*[:=]\s*)[^\s,;]+", r"\1[redacted]", text)
    return text


def _field(value: Any, *names: str) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    else:
        for name in names:
            result = getattr(value, name, None)
            if result is not None:
                return result
    return None


def classify_fault(*, code: Any = None, error_type: Any = None, message: Any = None,
                   http_status: Any = None) -> tuple[FaultFamily, str]:
    """Classify a provider/adapter error without treating text as proof."""
    haystack = " ".join(str(value).lower() for value in (code, error_type, message) if value is not None)
    try:
        status = int(http_status) if http_status is not None else None
    except (TypeError, ValueError):
        status = None
    if any(token in haystack for token in ("encrypted", "item-id mismatch", "item id mismatch")):
        return FaultFamily.ENCRYPTED_ITEM_MISMATCH, "encrypted_item_mismatch"
    if any(token in haystack for token in ("fast price", "fast pricing", "price not configured", "service_tier_fast")):
        return FaultFamily.FAST_NOT_CONFIGURED, "fast_not_configured"
    if status == 404 or any(token in haystack for token in ("model not found", "does not exist or you do not have access", "route not found")):
        return FaultFamily.ROUTE_NOT_FOUND, "route_not_found"
    if status in {401, 403} or any(token in haystack for token in ("unauthorized", "forbidden", "permission denied", "access denied")):
        return FaultFamily.AUTHORIZATION, "authorization_rejected"
    if any(token in haystack for token in ("billing", "insufficient funds", "quota exceeded", "credit")):
        return FaultFamily.BILLING, "billing_or_quota"
    if any(token in haystack for token in ("cancelled", "canceled", "user cancel")):
        return FaultFamily.USER_CANCELLED, "user_cancelled"
    if any(token in haystack for token in ("needs_input", "request_user_input")):
        return FaultFamily.NEEDS_INPUT, "needs_input"
    if status in {408, 425, 429, 500, 502, 503, 504} or any(token in haystack for token in ("capacity", "rate limit", "temporarily unavailable", "server busy", "429")):
        return FaultFamily.CAPACITY, "capacity_or_transient"
    if any(token in haystack for token in ("stream disconnected", "stream closed", "connection reset", "broken pipe", "eof", "disconnect")):
        return FaultFamily.STREAM_DISCONNECTED, "stream_disconnected"
    return FaultFamily.UNKNOWN, "unknown"


@dataclass(frozen=True)
class FaultObservation:
    operation_kind: str
    run_id: str | None = None
    spec_key: str | None = None
    stage: str | None = None
    attempt: int = 0
    worker_id: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    source: str = "adapter"
    confidence: str = "observed"
    family: str = FaultFamily.UNKNOWN.value
    reason: str = "unknown"
    http_status: int | None = None
    code: str | None = None
    error_type: str | None = None
    message: str | None = None
    request_id: str | None = None
    cf_ray: str | None = None
    retry_after_seconds: float | None = None
    requested_model: str | None = None
    observed_model: str | None = None
    requested_effort: str | None = None
    observed_effort: str | None = None
    requested_service_tier: str | None = None
    observed_service_tier: str | None = None
    sdk_version: str | None = None
    runtime_version: str | None = None
    request_admission: str = "unknown"
    execution_outcome: str = "unknown"
    sdk_retry_count: int | None = None
    sdk_retry_coverage: str = "unknown"
    last_verified_progress: str | None = None
    route_scope: str = "unknown"
    fingerprint: str = ""
    evidence: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.fingerprint:
            object.__setattr__(self, "fingerprint", fault_fingerprint(self))

    def public(self) -> dict[str, object]:
        value = {"schema_version": "spec-runner-fault-observation/v1", **self.__dict__}
        value["evidence"] = list(self.evidence)
        return value


def fault_fingerprint(observation: FaultObservation) -> str:
    value = {
        "family": observation.family,
        "reason": observation.reason,
        "route_scope": observation.route_scope,
        "operation_kind": observation.operation_kind,
        "stage": observation.stage,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def observation_from_error(*, operation_kind: str, error: BaseException | Mapping[str, Any],
                           run_id: str | None = None, spec_key: str | None = None,
                           stage: str | None = None, attempt: int = 0,
                           worker_id: str | None = None, thread_id: str | None = None,
                           turn_id: str | None = None, requested_model: str | None = None,
                           requested_effort: str | None = None,
                           requested_service_tier: str | None = None,
                           sdk_version: str | None = None,
                           last_verified_progress: str | None = None) -> FaultObservation:
    if isinstance(error, Mapping):
        details = dict(error)
        message = _field(details, "message")
        code = _field(details, "code")
        error_type = _field(details, "error_type", "type")
        status_value = _field(details, "http_status", "http_status_code", "httpStatusCode")
        source = str(details.get("source") or "adapter")
        evidence = tuple(str(item) for item in details.get("evidence", []) if item)
        request_id = _field(details, "request_id", "requestId")
        cf_ray = _field(details, "cf_ray", "cfRay")
        request_admission = str(details.get("request_admission") or "unknown")
        execution_outcome = str(details.get("execution_outcome") or "unknown")
    else:
        details = getattr(error, "details", {}) or {}
        message = getattr(error, "message", None) or str(error)
        code = _field(error, "code") or _field(details, "code")
        error_type = _field(error, "type") or _field(details, "error_type", "exception_type") or type(error).__name__
        status_value = _field(error, "http_status_code", "httpStatusCode", "status_code") or _field(details, "http_status", "http_status_code", "httpStatusCode")
        source = str(details.get("source") or "adapter")
        evidence = tuple(str(item) for item in details.get("evidence", []) if item)
        request_id = _field(error, "request_id", "requestId") or _field(details, "request_id", "requestId")
        cf_ray = _field(error, "cf_ray", "cfRay") or _field(details, "cf_ray", "cfRay")
        request_admission = str(details.get("request_admission") or ("accepted" if details.get("turn_id") else "unknown"))
        execution_outcome = str(details.get("execution_outcome") or "unknown")
        thread_id = thread_id or details.get("thread_id")
        turn_id = turn_id or details.get("turn_id")
    try:
        http_status = int(status_value) if status_value is not None else None
    except (TypeError, ValueError):
        http_status = None
    observed_model = _text(_field(details, "observed_model", "actual_model", "model"), 120)
    observed_effort = _text(_field(details, "observed_effort", "actual_effort", "effort"), 120)
    observed_service_tier = _text(_field(details, "observed_service_tier", "actual_service_tier", "service_tier"), 120)
    runtime_version = _text(_field(details, "runtime_version"), 120)
    retry_count_value = _field(details, "sdk_retry_count", "retry_count", "sdk_retries")
    try:
        sdk_retry_count = int(retry_count_value) if retry_count_value is not None else None
    except (TypeError, ValueError):
        sdk_retry_count = None
    sdk_retry_coverage = str(_field(details, "sdk_retry_coverage", "retry_coverage") or "unknown")
    if sdk_retry_coverage not in {"observed", "bounded", "unknown"}:
        sdk_retry_coverage = "unknown"
    family, reason = classify_fault(code=code, error_type=error_type, message=message, http_status=http_status)
    return FaultObservation(
        operation_kind=operation_kind, run_id=run_id, spec_key=spec_key, stage=stage,
        attempt=attempt, worker_id=worker_id, thread_id=_text(thread_id), turn_id=_text(turn_id),
        source=source, confidence="fallback_text" if not details.get("structured", False) else "structured",
        family=family.value, reason=reason, http_status=http_status, code=_text(code, 120),
        error_type=_text(error_type, 120), message=_redact(message), request_id=_text(request_id, 120),
        cf_ray=_text(cf_ray, 120), retry_after_seconds=_field(details, "retry_after_seconds", "retryAfterSeconds"),
        requested_model=requested_model, requested_effort=requested_effort,
        observed_model=observed_model, observed_effort=observed_effort,
        requested_service_tier=requested_service_tier, observed_service_tier=observed_service_tier,
        sdk_version=sdk_version or _text(_field(details, "sdk_version"), 120),
        runtime_version=runtime_version,
        request_admission=request_admission if request_admission in {"accepted", "rejected", "unknown"} else "unknown",
        execution_outcome=execution_outcome if execution_outcome in {"completed", "failed", "unknown"} else "unknown",
        sdk_retry_count=sdk_retry_count, sdk_retry_coverage=sdk_retry_coverage,
        last_verified_progress=last_verified_progress, route_scope=_text(_field(details, "route_scope", "routeScope"), 120) or "unknown",
        evidence=evidence or ("error_text_fallback",),
    )


def observation_from_worker_result(*, operation_kind: str, result: Mapping[str, Any],
                                   run_id: str | None = None, stage: str | None = None,
                                   attempt: int = 0, requested_model: str | None = None,
                                   requested_effort: str | None = None) -> FaultObservation | None:
    """Turn a failed or interrupted worker receipt into one structured observation."""
    status = str(result.get("status") or "unknown")
    error = result.get("error")
    if not error and status not in {"failed", "interrupted", "cancelled"}:
        return None
    structured = result.get("fault_observation")
    error_payload: Mapping[str, Any] = structured if isinstance(structured, Mapping) else {
        "message": error or status,
        "source": "worker_result",
        "structured": False,
        "thread_id": result.get("thread_id"),
        "turn_id": result.get("turn_id"),
        "request_admission": "accepted" if result.get("turn_id") else "unknown",
        "execution_outcome": "failed" if status in {"failed", "interrupted", "cancelled"} else "unknown",
        "evidence": ["worker_result", "turn_identity" if result.get("turn_id") else "turn_identity_missing"],
    }
    observation = observation_from_error(
        operation_kind=operation_kind,
        error=error_payload,
        thread_id=str(result.get("thread_id")) if result.get("thread_id") else None,
        turn_id=str(result.get("turn_id")) if result.get("turn_id") else None,
        run_id=run_id, stage=stage, attempt=attempt,
        requested_model=requested_model, requested_effort=requested_effort,
    )
    return observation


@dataclass(frozen=True)
class RecoveryPolicy:
    same_thread_retries: int = 1
    capacity_retries: int = 2
    route_probes: int = 1
    clean_probes: int = 1
    migration_requests: int = 1
    max_no_progress: int = 3
    retry_delay_seconds: float = 5.0
    service_wait_delay_seconds: float = 60.0


@dataclass(frozen=True)
class RecoverySnapshot:
    run_id: str
    operation_kind: str
    stage: str
    generation: int = 0
    user_control: str | None = None
    owner_valid: bool = True
    active_execution: bool = False
    request_admission: str = "unknown"
    execution_outcome: str = "unknown"
    business_progress_verified: bool = False
    same_thread_attempts: int = 0
    capacity_attempts: int = 0
    route_probe_attempts: int = 0
    clean_probe_attempts: int = 0
    migration_attempts: int = 0
    no_progress_attempts: int = 0
    retry_deadline: str | None = None
    wait_deadline: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None


@dataclass(frozen=True)
class RecoveryDecision:
    action: RecoveryAction
    reason: str
    evidence: tuple[str, ...]
    preconditions: tuple[str, ...] = ()
    next_check_at: str | None = None
    remaining_budget: Mapping[str, int] = field(default_factory=dict)
    family: str = FaultFamily.UNKNOWN.value

    def public(self) -> dict[str, object]:
        return {"schema_version": "spec-runner-recovery-decision/v1", "action": self.action.value,
                "reason": self.reason, "evidence": list(self.evidence),
                "preconditions": list(self.preconditions), "next_check_at": self.next_check_at,
                "remaining_budget": dict(self.remaining_budget), "family": self.family}


def _future(now: datetime, seconds: float) -> str:
    from datetime import timedelta
    return (now.astimezone(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _retry_delay(observation: FaultObservation, default: float) -> float:
    """Prefer a valid provider Retry-After hint over the local fallback."""
    try:
        value = float(observation.retry_after_seconds) if observation.retry_after_seconds is not None else None
    except (TypeError, ValueError):
        value = None
    if value is None or value < 0 or value != value or value == float("inf"):
        return default
    return value


def decide_recovery(snapshot: RecoverySnapshot, observations: list[FaultObservation],
                    policy: RecoveryPolicy = RecoveryPolicy(), *, now: datetime | None = None) -> RecoveryDecision:
    """Return a deterministic, explainable recovery action."""
    current = now or datetime.now(timezone.utc)
    remaining = {
        "same_thread_retries": max(0, policy.same_thread_retries - snapshot.same_thread_attempts),
        "capacity_retries": max(0, policy.capacity_retries - snapshot.capacity_attempts),
        "route_probes": max(0, policy.route_probes - snapshot.route_probe_attempts),
        "clean_probes": max(0, policy.clean_probes - snapshot.clean_probe_attempts),
        "migration_requests": max(0, policy.migration_requests - snapshot.migration_attempts),
        "no_progress": max(0, policy.max_no_progress - snapshot.no_progress_attempts),
    }
    if snapshot.user_control == "cancel_requested":
        return RecoveryDecision(RecoveryAction.BLOCKED, "user_cancelled", ("durable_cancel_request",), ("do_not_create_worker",), family=FaultFamily.USER_CANCELLED.value, remaining_budget=remaining)
    if snapshot.user_control == "pause_requested":
        return RecoveryDecision(RecoveryAction.WAIT_FOR_CONFIG, "user_paused", ("durable_pause_request",), ("wait_for_resume",), family="control", remaining_budget=remaining)
    if not snapshot.owner_valid:
        return RecoveryDecision(RecoveryAction.BLOCKED, "owner_invalid", ("owner_readback_missing",), ("restore_single_owner",), remaining_budget=remaining)
    if snapshot.active_execution or snapshot.request_admission == "accepted" and snapshot.execution_outcome == "unknown":
        return RecoveryDecision(RecoveryAction.OBSERVE, "execution_outcome_unknown", ("active_or_unsettled_execution",), ("reconcile_before_retry",), remaining_budget=remaining)
    if snapshot.business_progress_verified or snapshot.execution_outcome == "completed":
        return RecoveryDecision(RecoveryAction.ADOPT_RESULT, "verified_business_result_available", ("completed_result_or_progress_receipt",), ("verify_artifacts_before_stage_advance",), remaining_budget=remaining)
    observation = observations[-1] if observations else FaultObservation(operation_kind=snapshot.operation_kind, stage=snapshot.stage)
    family = observation.family
    if family == FaultFamily.USER_CANCELLED.value:
        action = RecoveryAction.BLOCKED
    elif family == FaultFamily.NEEDS_INPUT.value:
        action = RecoveryAction.NEEDS_INPUT
    elif family == FaultFamily.AUTHORIZATION.value or family == FaultFamily.BILLING.value:
        action = RecoveryAction.WAIT_FOR_CONFIG
    elif family == FaultFamily.CAPACITY.value:
        action = RecoveryAction.WAIT_RETRY if remaining["capacity_retries"] else RecoveryAction.SERVICE_WAIT
    elif family == FaultFamily.FAST_NOT_CONFIGURED.value:
        action = RecoveryAction.RESUME_SAME_THREAD if remaining["same_thread_retries"] else RecoveryAction.WAIT_FOR_CONFIG
    elif family == FaultFamily.ROUTE_NOT_FOUND.value:
        action = RecoveryAction.USE_APPROVED_ROUTE if remaining["route_probes"] else RecoveryAction.WAIT_FOR_CONFIG
    elif family == FaultFamily.STREAM_DISCONNECTED.value:
        action = RecoveryAction.RESUME_SAME_THREAD if remaining["same_thread_retries"] else RecoveryAction.BLOCKED
    elif family == FaultFamily.ENCRYPTED_ITEM_MISMATCH.value:
        action = RecoveryAction.PROBE_CLEAN_CONTEXT if remaining["clean_probes"] else (RecoveryAction.REQUEST_CLEAN_MIGRATION if remaining["migration_requests"] else RecoveryAction.BLOCKED)
    else:
        action = RecoveryAction.RESUME_SAME_THREAD if remaining["same_thread_retries"] and remaining["no_progress"] else RecoveryAction.BLOCKED
    fallback_delay = policy.service_wait_delay_seconds if action == RecoveryAction.SERVICE_WAIT else policy.retry_delay_seconds
    delay = _retry_delay(observation, fallback_delay) if family == FaultFamily.CAPACITY.value else fallback_delay
    next_check = _future(current, delay) if action in {RecoveryAction.WAIT_RETRY, RecoveryAction.SERVICE_WAIT} else None
    return RecoveryDecision(action, f"fault_family:{family}", (observation.fingerprint, observation.reason),
                            ("reconcile_external_side_effects", "preserve_stage_budget"), next_check,
                            remaining, family)


def recovery_diagnostic(*, episode: Mapping[str, Any], observations: list[Mapping[str, Any]],
                       decisions: list[Mapping[str, Any]], execution_owner: Mapping[str, Any] | None = None,
                       run_state: str | None = None) -> dict[str, object]:
    """Build a read-only explanation of the current recovery frontier."""
    def body(item: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        value = item.get(key)
        return value if isinstance(value, Mapping) else item

    latest_observation = body(observations[-1], "observation") if observations else {}
    latest_decision = body(decisions[-1], "decision") if decisions else {}
    action = str(latest_decision.get("action") or episode.get("state") or "unknown")
    family = str(latest_observation.get("family") or latest_decision.get("family") or "unknown")
    reason = str(latest_observation.get("reason") or latest_decision.get("reason") or "unknown")
    route = {
        "scope": latest_observation.get("route_scope", "unknown"),
        "requested_model": latest_observation.get("requested_model"),
        "observed_model": latest_observation.get("observed_model"),
        "requested_effort": latest_observation.get("requested_effort"),
        "observed_effort": latest_observation.get("observed_effort"),
        "requested_service_tier": latest_observation.get("requested_service_tier"),
        "observed_service_tier": latest_observation.get("observed_service_tier"),
    }
    admission = str(latest_observation.get("request_admission", "unknown"))
    outcome = str(latest_observation.get("execution_outcome", "unknown"))
    unconfirmed = admission == "accepted" and outcome == "unknown"
    remaining = latest_decision.get("remaining_budget", {})
    if not isinstance(remaining, Mapping):
        remaining = {}
    counts = {
        "same_thread": int(episode.get("same_thread_attempts") or 0),
        "capacity": int(episode.get("capacity_attempts") or 0),
        "route_probe": int(episode.get("route_probe_attempts") or 0),
        "clean_probe": int(episode.get("clean_probe_attempts") or 0),
        "migration": int(episode.get("migration_attempts") or 0),
        "no_progress": int(episode.get("no_progress_attempts") or 0),
    }
    if action in {"observe", "adopt_result"}:
        next_condition = "reconcile the persisted execution and verify the business result"
    elif action in {"wait_retry", "service_wait"}:
        next_condition = "wait until next_check_at, then perform one bounded recovery check"
    elif action == "wait_for_config":
        next_condition = "the approved configuration or user control must change before retrying"
    elif action == "needs_input":
        next_condition = "the required durable business answer must be supplied"
    elif action == "blocked":
        next_condition = "an operator must resolve the recorded blocker; no worker may be created"
    else:
        next_condition = "the owning Runner handler must verify the preconditions before acting"
    cleanup_debt = run_state == "cleanup_pending" or bool(episode.get("cleanup_debt"))
    return {
        "schema_version": "spec-runner-recovery-diagnostic/v1",
        "incident": {
            "fingerprint": latest_observation.get("fingerprint"),
            "family": family,
            "reason": reason,
            "operation_kind": episode.get("operation_kind"),
            "stage": episode.get("stage"),
            "generation": episode.get("generation"),
            "first_observed_at": observations[0].get("observed_at") if observations else None,
            "last_observed_at": observations[-1].get("observed_at") if observations else None,
        },
        "fault_ownership": {
            "run_id": episode.get("run_id"),
            "worker_id": latest_observation.get("worker_id"),
            "source": latest_observation.get("source"),
            "confidence": latest_observation.get("confidence"),
            "evidence": list(latest_observation.get("evidence", [])) if isinstance(latest_observation.get("evidence"), list) else [],
        },
        "current_action": action,
        "route": route,
        "thread": {
            "thread_id": latest_observation.get("thread_id"),
            "turn_id": latest_observation.get("turn_id"),
            "request_admission": admission,
            "execution_outcome": outcome,
            "active_execution": unconfirmed,
            "unconfirmed_execution": unconfirmed,
        },
        "execution_owner": dict(execution_owner) if execution_owner else None,
        "attempts": counts,
        "sdk_retry": {
            "count": latest_observation.get("sdk_retry_count"),
            "coverage": latest_observation.get("sdk_retry_coverage", "unknown"),
            "meaning": "unknown when the SDK does not expose inner retry telemetry",
        },
        "budget": {
            "remaining": dict(remaining),
            "retry_deadline": episode.get("retry_deadline"),
            "wait_deadline": episode.get("wait_deadline"),
            "next_check_at": latest_decision.get("next_check_at"),
        },
        "last_verified_progress": episode.get("last_verified_progress") or latest_observation.get("last_verified_progress"),
        "next_recovery_condition": next_condition,
        "cleanup_debt": {"present": cleanup_debt, "run_state": run_state},
    }
