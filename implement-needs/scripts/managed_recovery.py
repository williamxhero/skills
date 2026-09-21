"""Managed turn recovery protocol for the Implement Needs controller.

This module is deliberately backend-neutral.  The task backend owns protocol
normalisation; this module owns the durable decision made from formal identity,
turn and side-effect readbacks.  A message, request id, title token, or thread
URL is never accepted as identity evidence.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


COMPLETED = "completed"
MODEL_CAPACITY = "model_capacity"
STREAM_DISCONNECTED = "stream_disconnected"
TERMINAL_FAILURE = "terminal_failure"
UNCERTAIN = "uncertain"
NO_PROGRESS = "no_progress"
BLOCKED = "blocked"

DEFAULT_BUDGETS = {
    STREAM_DISCONNECTED: 2,
    MODEL_CAPACITY: 1,
    UNCERTAIN: 0,
}


class RecoveryError(ValueError):
    """A recovery request is incomplete or would violate an evidence gate."""


@dataclass(frozen=True)
class TurnIdentity:
    task_id: str
    run_id: str
    attempt_id: str
    formal_thread_id: str
    host_id: str
    turn_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "formal_thread_id": self.formal_thread_id,
            "host_id": self.host_id,
            "turn_id": self.turn_id,
        }


@dataclass(frozen=True)
class RecoveryDecision:
    failure_class: str
    action: str
    next_action: str
    reason: str
    budget: int
    evidence: tuple[str, ...]


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecoveryError(f"{field} must be a non-empty string")
    return value.strip()


def validate_identity(identity: Mapping[str, Any]) -> TurnIdentity:
    if not isinstance(identity, Mapping):
        raise RecoveryError("formal turn identity is required")
    values = {field: _nonempty(identity.get(field), field) for field in TurnIdentity.__dataclass_fields__}
    return TurnIdentity(**values)


def _error_text(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in ("message", "detail", "reason", "error", "code", "type"):
            if value.get(key) is not None:
                return str(value[key]).lower()
        return json.dumps(dict(value), sort_keys=True).lower()
    return str(value or "").lower()


def _has_payload(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_has_payload(value.get(field)) for field in ("text", "output", "message", "result", "content"))
    if isinstance(value, list):
        return any(_has_payload(item) for item in value)
    return value is not None and value is not False


def has_useful_turn_output(*, completion: Mapping[str, Any] | None = None,
                           history_readback: Mapping[str, Any] | None = None,
                           turn_id: str | None = None) -> bool:
    """Require persisted work output, not a terminal status alone."""
    candidates: list[Mapping[str, Any]] = []
    if isinstance(completion, Mapping):
        turn = completion.get("turn")
        candidates.append(turn if isinstance(turn, Mapping) else completion)
    history = history_readback if isinstance(history_readback, Mapping) else {}
    turns = history.get("turns")
    if isinstance(turns, list):
        candidates.extend(
            item for item in turns
            if isinstance(item, Mapping) and (turn_id is None or item.get("id") == turn_id)
        )
    for candidate in candidates:
        if any(_has_payload(candidate.get(field)) for field in ("output", "message", "result")):
            return True
        items = candidate.get("items")
        if isinstance(items, list) and any(
            isinstance(item, Mapping)
            and item.get("type") not in {"userMessage", "reasoning"}
            and _has_payload(item)
            for item in items
        ):
            return True
    return False


def empty_completed_turn_streak(*, history_readback: Mapping[str, Any] | None = None,
                                turn_id: str | None = None) -> int:
    """Return the trailing streak of completed turns with no persisted work.

    History is treated as evidence only when it contains the current turn. The
    normalized task backends expose turns oldest-first; reversing the sequence
    also keeps this safe for the newest-first native readback shape.
    """
    history = history_readback if isinstance(history_readback, Mapping) else {}
    turns = history.get("turns")
    if not isinstance(turns, list) or not turns:
        return 0
    rows = [item for item in turns if isinstance(item, Mapping)]
    if turn_id is not None and not any(item.get("id") == turn_id for item in rows):
        return 0
    streak = 0
    for item in reversed(rows):
        status = item.get("status")
        if status not in {"completed", "succeeded", "success"}:
            break
        if has_useful_turn_output(history_readback={"turns": [item]}, turn_id=item.get("id")):
            break
        streak += 1
    return streak


def classify_turn_outcome(*, completion: Mapping[str, Any] | None = None,
                          error: Mapping[str, Any] | str | None = None,
                          transport_error: Mapping[str, Any] | str | None = None,
                          history_readback: Mapping[str, Any] | None = None,
                          turn_id: str | None = None) -> str:
    """Classify structured fields first, with stable text only as fallback."""
    completion = completion if isinstance(completion, Mapping) else {}
    structured = error if isinstance(error, Mapping) else (
        transport_error if isinstance(transport_error, Mapping) else completion.get("error")
    )
    if isinstance(structured, Mapping):
        code = str(structured.get("code") or structured.get("type") or "").lower()
        if code in {"model_capacity", "capacity", "model_at_capacity"}:
            return MODEL_CAPACITY
        if code in {"identity_mismatch", "project_not_found", "unauthorized", "forbidden", "authorization_failed"}:
            return BLOCKED
        if code in {"stream_disconnected", "transport_disconnected", "connection_lost"}:
            return STREAM_DISCONNECTED
        if code in {"terminal_failure", "failed"}:
            return TERMINAL_FAILURE
    text = " ".join((_error_text(error), _error_text(transport_error), _error_text(completion.get("status"))))
    if "selected model is at capacity" in text or "model at capacity" in text:
        return MODEL_CAPACITY
    if "identity" in text or "project" in text and "not found" in text or "unauthori" in text:
        return BLOCKED
    if "stream disconnected" in text or "connection" in text and "lost" in text:
        return STREAM_DISCONNECTED
    if completion.get("status") in {"completed", "succeeded", "success"} and not error:
        if has_useful_turn_output(completion=completion, history_readback=history_readback, turn_id=turn_id):
            return COMPLETED
        if empty_completed_turn_streak(history_readback=history_readback, turn_id=turn_id) >= 3:
            return NO_PROGRESS
        return UNCERTAIN
    if error or transport_error or completion.get("status") in {"failed", "error"}:
        return TERMINAL_FAILURE
    return UNCERTAIN


def checkpoint(*, identity: Mapping[str, Any], previous_turn_id: str,
               failure_class: str, durable_state: Mapping[str, Any],
               receipts: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Build a deterministic checkpoint that can be safely re-read after restart."""
    formal = validate_identity({**identity, "turn_id": previous_turn_id})
    if failure_class not in {STREAM_DISCONNECTED, UNCERTAIN, MODEL_CAPACITY, TERMINAL_FAILURE}:
        raise RecoveryError("checkpoint requires a failure class")
    if not isinstance(durable_state, Mapping):
        raise RecoveryError("durable_state is required")
    receipt_list = list(receipts or [])
    return {
        "identity": formal.as_dict(),
        "previous_turn_id": formal.turn_id,
        "failure_class": failure_class,
        "durable_state": dict(durable_state),
        "verified_receipts": [dict(item) for item in receipt_list],
        "checkpoint_digest": hashlib.sha256(json.dumps({
            "identity": formal.as_dict(), "failure_class": failure_class,
            "durable_state": dict(durable_state),
            "verified_receipts": [dict(item) for item in receipt_list],
        }, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
    }


def build_recovery_message(*, identity: Mapping[str, Any], previous_turn_id: str,
                           failure_class: str, checkpoint_data: Mapping[str, Any]) -> str:
    """Create the only message shape permitted for a same-thread recovery."""
    formal = validate_identity({**identity, "turn_id": previous_turn_id})
    if failure_class not in {STREAM_DISCONNECTED, UNCERTAIN}:
        raise RecoveryError("same-thread recovery is only valid for transport/uncertain failures")
    if not isinstance(checkpoint_data, Mapping) or checkpoint_data.get("checkpoint_digest") is None:
        raise RecoveryError("checkpoint digest is required")
    envelope = {
        "action": "continue",
        "task_id": formal.task_id,
        "run_id": formal.run_id,
        "attempt_id": formal.attempt_id,
        "formal_thread_id": formal.formal_thread_id,
        "host_id": formal.host_id,
        "previous_turn_id": formal.turn_id,
        "failure_class": failure_class,
        "checkpoint": dict(checkpoint_data),
        "replay_policy": "continue_from_verified_checkpoint; do_not_repeat_verified_receipts",
    }
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def reconcile_side_effects(*, operation_intents: list[Mapping[str, Any]],
                           readbacks: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Resolve known intents by their original idempotency key and readback."""
    if not isinstance(operation_intents, list):
        raise RecoveryError("operation_intents must be a list")
    resolved, unknown = [], []
    for intent in operation_intents:
        key = _nonempty(intent.get("idempotency_key"), "idempotency_key")
        readback = readbacks.get(key)
        if not isinstance(readback, Mapping) or readback.get("status") not in {"verified", "not_found"}:
            unknown.append(key)
            continue
        resolved.append({"idempotency_key": key, "status": readback["status"], "evidence": list(readback.get("evidence", []))})
    return {"status": "blocked" if unknown else "reconciled", "resolved": resolved,
            "unknown_idempotency_keys": unknown, "next_action": "repair" if unknown else "continue"}


def decide_recovery(failure_class: str, *, attempts: int = 0, budget: int | None = None,
                    thread_readback: Mapping[str, Any] | None = None,
                    history_readback: Mapping[str, Any] | None = None,
                    side_effects: Mapping[str, Any] | None = None,
                    old_attempt_archived: bool = False,
                    fallback_route_verified: bool = False) -> RecoveryDecision:
    """Choose one bounded action only after the supplied readbacks are checked."""
    budget = DEFAULT_BUDGETS.get(failure_class, 0) if budget is None else budget
    if failure_class == COMPLETED:
        return RecoveryDecision(COMPLETED, "accept_completed", "continue_controller", "terminal completion verified", 0, ("turn_completed",))
    if failure_class == NO_PROGRESS:
        return RecoveryDecision(NO_PROGRESS, "repair", "blocked", "repeated empty completion made no progress", 0, ("repeated_empty_completion", "no_progress"))
    if failure_class == MODEL_CAPACITY:
        if attempts >= budget:
            return RecoveryDecision(BLOCKED, "repair", "blocked", "fallback budget exhausted", budget, ("capacity_budget_exhausted",))
        if not old_attempt_archived:
            return RecoveryDecision(MODEL_CAPACITY, "archive_failed_attempt", "archive_before_replacement", "failed attempt must be archived first", budget, ("archive_required",))
        if not fallback_route_verified:
            return RecoveryDecision(BLOCKED, "repair", "blocked", "fallback route lacks applied-route readback", budget, ("route_readback_missing",))
        return RecoveryDecision(MODEL_CAPACITY, "create_replacement_attempt", "send_checkpointed_assignment", "verified fallback is available", budget, ("archive_readback", "fallback_route_readback"))
    if failure_class in {STREAM_DISCONNECTED, UNCERTAIN}:
        if attempts >= budget:
            return RecoveryDecision(BLOCKED, "repair", "blocked", "stream recovery budget exhausted", budget, ("recovery_budget_exhausted",))
        history = history_readback or {}
        if (history.get("turn_completed") or history.get("terminal") == "completed") and has_useful_turn_output(
            completion={"status": "completed"}, history_readback=history
        ):
            return RecoveryDecision(COMPLETED, "verify_completed_after_reconnect", "continue_controller", "reconnect found a completed original turn", budget, ("history_readback",))
        thread = thread_readback or {}
        if thread.get("formal_thread_id") != thread.get("requested_formal_thread_id") or not thread.get("host_id"):
            return RecoveryDecision(BLOCKED, "repair", "blocked", "formal thread identity readback is incomplete", budget, ("identity_readback_missing",))
        if thread.get("archived") or thread.get("lifecycle") in {"failed", "archived", "closed"}:
            return RecoveryDecision(BLOCKED, "repair", "blocked", "thread is not recoverable", budget, ("thread_not_recoverable",))
        if side_effects and side_effects.get("status") != "reconciled":
            return RecoveryDecision(UNCERTAIN, "reconcile_side_effects", "reconcile_before_continue", "side effects are not reconciled", budget, ("side_effect_readback_required",))
        return RecoveryDecision(STREAM_DISCONNECTED, "send_checkpointed_continue", "verify_recovery_turn", "original turn has no terminal evidence and thread is recoverable", budget, ("identity_readback", "history_readback"))
    return RecoveryDecision(BLOCKED, "repair", "blocked", "failure class requires repair", budget, ("terminal_failure",))


def replacement_attempt_allowed(*, old_attempt_archived: bool,
                                archive_readback: Mapping[str, Any] | None,
                                route_readback: Mapping[str, Any] | None,
                                new_identity: Mapping[str, Any] | None) -> bool:
    """Gate replacement creation and ensure it is a genuinely new attempt."""
    if not old_attempt_archived or not isinstance(archive_readback, Mapping) or archive_readback.get("archived") is not True:
        return False
    if not isinstance(route_readback, Mapping) or not route_readback.get("model") or not route_readback.get("effort"):
        return False
    if not isinstance(new_identity, Mapping):
        return False
    return all(new_identity.get(field) for field in ("task_id", "run_id", "attempt_id", "formal_thread_id", "host_id"))


def assert_no_replay(*, original_turn_status: str, assignment_idempotency_key: str) -> None:
    if original_turn_status in {COMPLETED, "succeeded", "terminal"}:
        raise RecoveryError(f"completed turn cannot be replayed: {assignment_idempotency_key}")


def recover_capacity_attempt(*, archive, archive_readback, fallback_route_readback,
                             create_replacement, new_identity: Mapping[str, Any]) -> dict[str, Any]:
    """Run the capacity replacement gate in the only safe order.

    Callers provide backend-specific operations.  The replacement callback is
    never invoked until archive and applied-route readbacks are both verified.
    """
    archive_result = archive()
    archive_state = archive_readback()
    route = fallback_route_readback()
    if not replacement_attempt_allowed(old_attempt_archived=True,
                                       archive_readback=archive_state,
                                       route_readback=route,
                                       new_identity=new_identity):
        raise RecoveryError("replacement attempt requires archive and applied-route readbacks")
    replacement = create_replacement(new_identity, route)
    return {"archive": archive_result, "archive_readback": archive_state,
            "route_readback": route, "replacement": replacement,
            "evidence": ["archive_operation", "archive_readback", "fallback_route_readback", "replacement_created"]}


def recover_stream_disconnect(db, backend, *, run_id: str, identity: Mapping[str, Any],
                              previous_turn_id: str, failure_class: str,
                              checkpoint_data: Mapping[str, Any],
                              thread_readback: Mapping[str, Any],
                              history_readback: Mapping[str, Any] | None = None,
                              side_effects: Mapping[str, Any] | None = None,
                              timeout: float | None = None) -> dict[str, Any]:
    """Continue one recoverable thread after reconciliation, never replaying assignment."""
    decision = decide_recovery(failure_class, thread_readback=thread_readback,
                               history_readback=history_readback, side_effects=side_effects)
    if decision.action == "verify_completed_after_reconnect":
        return {"status": COMPLETED, "action": decision.action, "evidence": list(decision.evidence)}
    if decision.action == "reconcile_side_effects":
        return {"status": UNCERTAIN, "action": decision.action, "evidence": list(decision.evidence)}
    if decision.action != "send_checkpointed_continue":
        raise RecoveryError(decision.reason)
    message = build_recovery_message(identity=identity, previous_turn_id=previous_turn_id,
                                     failure_class=failure_class, checkpoint_data=checkpoint_data)
    result = execute_managed_turn(db, backend, run_id=run_id, identity=identity,
                                  message=message, previous_turn_id=previous_turn_id,
                                  checkpoint_data=checkpoint_data, timeout=timeout)
    return {"action": decision.action, "message": message, **result}


def execute_managed_turn(db, backend, *, run_id: str, identity: Mapping[str, Any],
                         message: str, previous_turn_id: str | None = None,
                         checkpoint_data: Mapping[str, Any] | None = None,
                         timeout: float | None = None) -> dict[str, Any]:
    """Persist intent before send and retain a turn even when completion is lost.

    The adapter's send response is the only place a new turn id is accepted.
    Once it exists, every transport failure is recorded against that turn and
    becomes reconciliation input; the original assignment is never replayed.
    """
    from task_backend import (
        _first, _mapping, read_persisted_history, send_assignment,
        wait_for_turn_completion,
    )

    for field in ("task_id", "attempt_id", "formal_thread_id", "host_id"):
        _nonempty(identity.get(field), field)
    _nonempty(run_id, "run_id")
    _nonempty(message, "message")
    parameters = {
        "task_id": identity["task_id"], "attempt_id": identity["attempt_id"],
        "formal_thread_id": identity["formal_thread_id"], "host_id": identity["host_id"],
        "message_digest": hashlib.sha256(message.encode("utf-8")).hexdigest(),
        "previous_turn_id": previous_turn_id,
    }
    intent_result = db.create_operation_intent(run_id, "turn_start", identity["formal_thread_id"], parameters)
    intent = intent_result["intent"]
    if intent["status"] == "succeeded":
        return {"status": "already_succeeded", "intent": intent}
    if intent["status"] == "outcome_unknown":
        raise RecoveryError("turn intent has unknown outcome; reconcile before retry")
    if intent["status"] == "prepared":
        db.start_operation_intent(intent["intent_id"], "managed-recovery")
    try:
        sent = _mapping(send_assignment(backend, identity["formal_thread_id"], identity["host_id"], message))
        turn_id = _first(sent, "turn_id", "turnId")
        if not isinstance(turn_id, str) or not turn_id.strip():
            raise RecoveryError("managed turn response has no formal turn id")
        turn_identity = {**dict(identity), "turn_id": turn_id}
        db.record_managed_turn(run_id, turn_identity, UNCERTAIN, previous_turn_id=previous_turn_id,
                               operation_intent_id=intent["intent_id"], checkpoint=checkpoint_data or {}, status="started")
        completion = wait_for_turn_completion(backend, identity["formal_thread_id"], identity["host_id"], turn_id, timeout)
        history = read_persisted_history(backend, identity["formal_thread_id"], identity["host_id"], turn_id)
        params = completion.get("params", completion)
        classified = classify_turn_outcome(
            completion=params, history_readback=history, turn_id=turn_id
        )
        if classified == COMPLETED:
            db.update_managed_turn(run_id, identity["formal_thread_id"], turn_id, failure_class=COMPLETED, status="completed", terminal_event=completion, history_readback=history, output_evidence=["turn_completion"], side_effect_evidence=["persisted_history"])
            db.record_intent_outcome(intent["intent_id"], "succeeded", {"status": "verified", "turn_id": turn_id}, {"status": "verified", "turn_id": turn_id, "history": history})
        else:
            db.update_managed_turn(run_id, identity["formal_thread_id"], turn_id, failure_class=classified, status="failed", terminal_event=completion, history_readback=history)
            db.record_intent_outcome(intent["intent_id"], "failed", {"error": classified, "turn_id": turn_id}, {"status": "observed", "turn_id": turn_id})
        return {"status": classified, "turn_id": turn_id, "completion": completion, "history": history, "intent_id": intent["intent_id"]}
    except Exception as exc:
        # If a turn id was received, the started receipt remains durable.  If it
        # was not, the intent itself is still marked unknown and reconciliation
        # must inspect the backend before another mutation.
        if intent["status"] in {"executing", "prepared"}:
            db.mark_operation_unknown(intent["intent_id"], str(exc), ["managed_turn:transport_failure"])
        raise
