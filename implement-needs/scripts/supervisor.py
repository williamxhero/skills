"""Independent persisted-run supervisor for interrupted controller turns.

The supervisor is deliberately a separate process boundary: it reads the
SQLite state left by a parent controller turn, claims one durable recovery
action, and reports both recovery detection and recovery execution.  It never
uses a request id, title, URL, or message as identity.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from control_db import ActionClaimConflict, ControlDB, UnsafeLeaseTakeover
from controller_recovery import active_action_invariant, reconcile_controller_interruption


TERMINAL_RESULTS = {"completed", "blocked", "user_stopped", "no_change", "cancelled"}


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def detect_interruption(db: ControlDB, run_id: str, *, now_value: datetime | None = None,
                        stale_after_seconds: int = 0) -> dict[str, Any]:
    """Classify the persisted state without creating an action."""
    if not isinstance(stale_after_seconds, int) or isinstance(stale_after_seconds, bool) or stale_after_seconds < 0:
        raise ValueError("stale_after_seconds must be a non-negative integer")
    state = active_action_invariant(db, run_id)
    run = db.conn.execute(
        "SELECT status,terminal_result,stop_reason,run_phase,updated_at FROM runs WHERE run_id=?", (run_id,)
    ).fetchone()
    if run is None:
        return {"detected": False, "classification": "missing_run", "run_id": run_id, "state": state}
    base = {"run_id": run_id, "state": state, "run_phase": run[3], "updated_at": run[4]}
    if run[1] in TERMINAL_RESULTS:
        return {**base, "detected": False, "classification": "explicit_stop" if run[1] == "user_stopped" else "terminal", "terminal_result": run[1]}
    waiting = db.conn.execute(
        "SELECT external_request_id,wake_condition,next_safe_check_at,status FROM external_waits WHERE run_id=? AND status='waiting' ORDER BY updated_at DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    if waiting is not None and state["state"] in {"active_without_action", "non_active_without_action"}:
        return {**base, "detected": False, "classification": "waiting_external", "wait": dict(waiting)}
    if state["state"] == "action_present":
        pending = state.get("pending_action")
        if pending and pending.get("kind") == "controller_interrupted":
            claim = db.conn.execute(
                "SELECT lease_expires_at FROM action_claims WHERE action_id=?", (pending["action_id"],)
            ).fetchone()
            expired = claim is not None and _parse(claim[0]) <= (now_value or datetime.now(timezone.utc))
            return {**base, "detected": True, "classification": "recovery_action_pending" if not expired else "recovery_lease_expired",
                    "reason": "existing_recovery_action", "recovery_required": True, "lease_expired": expired}
        return {**base, "detected": False, "classification": "action_present"}
    if run[0] != "active":
        return {**base, "detected": False, "classification": "not_active"}
    if state["state"] != "active_without_action":
        return {**base, "detected": False, "classification": "no_recovery_condition"}
    age = max(0.0, ((now_value or datetime.now(timezone.utc)) - _parse(run[4])).total_seconds())
    if stale_after_seconds and age < stale_after_seconds:
        return {**base, "detected": False, "classification": "within_grace", "age_seconds": age}
    reason = "completed_child_without_persisted_next_action" if state["run_phase"] in {"implementing", "verifying"} else "active_run_without_action"
    return {**base, "detected": True, "classification": "controller_interrupted", "reason": reason, "age_seconds": age,
            "recovery_required": True}


def _action_gate(db: ControlDB, run_id: str, action_id: int, owner_id: str) -> dict[str, Any]:
    version = db.business_version(run_id)
    target = str(action_id)
    return {
        "schema_version": 1,
        "expected": {"run_id": run_id, "target_id": target, "candidate_sha": "supervisor", "environment": "controller", "business_version": version},
        "actor": {"id": owner_id, "authorized": True},
        "source": {"kind": "persisted-supervisor-readback", "trust": "verified"},
        "readback": {"status": "verified", "run_id": run_id, "target_id": target, "candidate_sha": "supervisor", "environment": "controller"},
        "action_readback": True,
    }


def _finish_recovery_action(db: ControlDB, run_id: str, action: dict[str, Any], owner_id: str,
                            *, max_attempts: int) -> dict[str, Any]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    db.assert_action_effect_permitted(action["action_id"], owner_id)
    current = db.conn.execute("SELECT attempts FROM actions WHERE action_id=?", (action["action_id"],)).fetchone()
    attempts = int(current[0]) if current else 0
    if attempts >= max_attempts:
        db.finish_action(action["action_id"], "blocked", error="recovery_attempt_budget_exhausted")
        return {"status": "blocked", "reason": "recovery_attempt_budget_exhausted", "resume_action": action["kind"], "attempts": attempts}
    db.finish_action(
        action["action_id"], "succeeded",
        result={"recovery_detected": True, "recovery_executed": True, "owner_id": owner_id},
        gate=_action_gate(db, run_id, action["action_id"], owner_id),
    )
    return {"status": "executed", "action_id": action["action_id"], "attempts": attempts + 1}


def supervise(db: ControlDB, run_id: str, *, owner_id: str = "supervisor",
              lease_seconds: int = 60, stale_after_seconds: int = 0,
              max_attempts: int = 3, budget_seconds: float = 300.0, execute: bool = True,
              max_actions: int = 1) -> dict[str, Any]:
    """Detect, claim, and optionally execute one recovery frontier."""
    detection = detect_interruption(db, run_id, stale_after_seconds=stale_after_seconds)
    result: dict[str, Any] = {"run_id": run_id, "detection": detection,
                              "recovery_detected": bool(detection.get("detected")),
                              "recovery_executed": False, "execution": {"status": "not_run"}}
    if not detection.get("detected"):
        result["outcome"] = detection.get("classification")
        return result
    recovery = reconcile_controller_interruption(db, run_id, reason=detection["reason"])
    action = recovery.get("action")
    result["recovery"] = recovery
    if not action:
        result["execution"] = {"status": "blocked", "reason": "recovery_action_missing"}
        result["outcome"] = "blocked"
        return result
    if not isinstance(budget_seconds, (int, float)) or isinstance(budget_seconds, bool) or budget_seconds <= 0:
        raise ValueError("budget_seconds must be positive")
    age = float(detection.get("age_seconds", 0.0))
    if age >= budget_seconds:
        result["execution"] = {"status": "blocked", "reason": "recovery_time_budget_exhausted",
                                "action_id": action["action_id"], "resume_action": action["kind"]}
        result["outcome"] = "blocked"
        return result
    if not execute:
        result["execution"] = {"status": "detected_only", "action_id": action["action_id"]}
        result["outcome"] = "recovery_detected"
        return result
    try:
        claim = db.claim_action(action["action_id"], owner_id, lease_seconds, supports_fencing=True, outcome_reconciled=True)
    except (ActionClaimConflict, UnsafeLeaseTakeover) as exc:
        result["execution"] = {"status": "concurrent_owner", "reason": str(exc), "action_id": action["action_id"]}
        result["outcome"] = "owned_by_other_supervisor"
        return result
    result["claim"] = claim
    try:
        execution = _finish_recovery_action(db, run_id, action, owner_id, max_attempts=max_attempts)
        result["execution"] = execution
        if execution["status"] == "executed":
            from advance_runtime import advance
            frontier = advance(db, run_id, max_actions=max_actions, include_recovery=False)
            result["frontier"] = frontier
            result["recovery_executed"] = True
            result["resume_evidence"] = [
                f"sqlite://supervisor/{run_id}/action/{action['action_id']}",
                f"sqlite://frontier/{run_id}/{frontier['event_cursor']}",
            ]
            result["outcome"] = "recovery_completed"
        else:
            result["outcome"] = "blocked"
    except Exception as exc:
        result["execution"] = {"status": "failed", "action_id": action["action_id"], "error": str(exc)}
        result["outcome"] = "blocked"
    return result
