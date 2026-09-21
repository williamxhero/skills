"""Outer-controller continuation and failure recovery.

This is the seam between persisted controller state and backend-specific
operations.  It turns a silent active/no-action run into a durable action before
any new external mutation is attempted.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from control_db import ActionConflict, ControlDB, now


class ControllerRecoveryError(RuntimeError):
    pass


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def active_action_invariant(db: ControlDB, run_id: str) -> dict[str, Any]:
    row = db.conn.execute(
        "SELECT status,current_action,recovery_action,terminal_result,updated_at,run_phase,business_version "
        "FROM runs WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None:
        raise ControllerRecoveryError("run_not_found")
    pending = db.conn.execute(
        "SELECT action_id,kind,target,status FROM actions WHERE run_id=? AND status IN ('pending','running') ORDER BY action_id LIMIT 1", (run_id,)
    ).fetchone()
    if row[3] is not None:
        state = "terminal"
    elif pending is not None or row[1] or row[2]:
        state = "action_present"
    else:
        state = "active_without_action" if row[0] == "active" else "non_active_without_action"
    return {"run_id": run_id, "state": state, "status": row[0],
            "current_action": row[1], "recovery_action": row[2],
            "terminal_result": row[3], "updated_at": row[4],
            "run_phase": row[5], "business_version": row[6],
            "pending_action": dict(pending) if pending else None}


def lost_wakeup_candidate(db: ControlDB, run_id: str) -> bool:
    """Recognize the observed SPEC-closed → next-SPEC-ready lost wake-up."""
    try:
        state = active_action_invariant(db, run_id)
    except ControllerRecoveryError as exc:
        if str(exc) == "run_not_found":
            return False
        raise
    if state["state"] != "active_without_action" or state["run_phase"] not in {"implementing", "verifying"}:
        return False
    closed = db.conn.execute(
        "SELECT 1 FROM specs WHERE run_id=? AND status='closed' LIMIT 1", (run_id,)
    ).fetchone()
    frontier = db.conn.execute(
        "SELECT status FROM specs WHERE run_id=? AND status NOT IN ('closed','cancelled') "
        "ORDER BY position LIMIT 1",
        (run_id,),
    ).fetchone()
    # A lost wake-up is only the boundary where the next SPEC is still planned.
    # Once the frontier is ready or already running, the controller has a live
    # semantic action and must let next_action render it instead of repeatedly
    # inserting controller_interrupted recovery.
    return closed is not None and frontier is not None and frontier["status"] == "planned"


def reconcile_controller_interruption(db: ControlDB, run_id: str, *, reason: str = "controller_turn_interrupted") -> dict[str, Any]:
    """Atomically materialize a recovery action for an active empty run."""
    state = active_action_invariant(db, run_id)
    if state["state"] == "terminal":
        return {"decision": "terminal", "action": None, "state": state}
    if state["pending_action"]:
        return {"decision": "existing_action", "action": state["pending_action"], "state": state}
    # A controller turn can persist the run marker and then be interrupted
    # before the action insert commits.  Treat that marker as an observed,
    # recoverable side effect only when no matching pending action exists; a
    # generic recovery intent must remain fail-closed until its owner reads it.
    if state["recovery_action"] == "controller_interrupted":
        key = f"{run_id}:controller_interrupted:{run_id}"
        existing = db.conn.execute(
            "SELECT * FROM actions WHERE idempotency_key=?", (key,)
        ).fetchone()
        if existing is None:
            with db.transaction():
                db.conn.execute(
                    "UPDATE runs SET current_action=NULL,recovery_action=NULL,updated_at=? WHERE run_id=?",
                    (now(), run_id),
                )
                db._business_event(run_id, "controller", run_id,
                                   "stale_recovery_intent_reconciled", {
                                       "recovery_action": "controller_interrupted",
                                       "reason": reason,
                                       "evidence": [f"sqlite://actions/{run_id}/missing"],
                                   })
            state = active_action_invariant(db, run_id)
    if state["current_action"] or state["recovery_action"]:
        return {"decision": "existing_intent", "action": {"kind": state["recovery_action"] or state["current_action"], "target": run_id}, "state": state}
    if state["status"] != "active":
        return {"decision": "not_resumable", "action": None, "state": state}
    key = f"{run_id}:controller_interrupted:{run_id}"
    with db.transaction():
        existing = db.conn.execute("SELECT * FROM actions WHERE idempotency_key=?", (key,)).fetchone()
        if existing:
            action = dict(existing)
        else:
            stamp = now()
            cur = db.conn.execute(
                "INSERT INTO actions(run_id,kind,target,status,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (run_id, "controller_interrupted", run_id, "pending", key, stamp, stamp),
            )
            action = dict(db.conn.execute("SELECT * FROM actions WHERE action_id=?", (cur.lastrowid,)).fetchone())
            db.conn.execute("UPDATE runs SET current_action=?,recovery_action=?,updated_at=? WHERE run_id=?",
                            (f"controller_interrupted:{run_id}", "controller_interrupted", stamp, run_id))
            db._business_event(run_id, "controller", run_id, "controller_interrupted", {
                "reason": reason, "action_id": action["action_id"], "recovery_action": "controller_interrupted",
            })
    return {"decision": "recovery_required", "action": action,
            "state": active_action_invariant(db, run_id),
            "evidence": [f"sqlite://controller-interrupted/{run_id}"]}


def stale_controller(db: ControlDB, run_id: str, *, now_value: datetime | None = None,
                     stale_after_seconds: int = 300) -> dict[str, Any]:
    if stale_after_seconds < 1:
        raise ValueError("stale_after_seconds must be positive")
    state = active_action_invariant(db, run_id)
    if state["state"] != "active_without_action":
        return {"stale": False, "reason": state["state"], "state": state}
    current = now_value or datetime.now(timezone.utc)
    age = (current - _parse(state["updated_at"])).total_seconds()
    return {"stale": age >= stale_after_seconds, "age_seconds": age,
            "reason": "active_without_action" if age >= stale_after_seconds else "within_grace",
            "state": state}


def watchdog(db: ControlDB, run_id: str, *, now_value: datetime | None = None,
             stale_after_seconds: int = 300) -> dict[str, Any]:
    result = stale_controller(db, run_id, now_value=now_value, stale_after_seconds=stale_after_seconds)
    if not result["stale"]:
        return {"decision": "no_action", **result}
    recovery = reconcile_controller_interruption(db, run_id, reason="stale_controller_watchdog")
    return {"decision": "recovery_requested", "detection": result, "recovery": recovery,
            "evidence": [f"sqlite://watchdog/{run_id}"]}


def complete_child_and_persist_next_action(db: ControlDB, run_id: str, *, child_kind: str,
                                           child_id: str, next_action: Mapping[str, Any] | None = None,
                                           result: Mapping[str, Any] | None = None,
                                           expected_version: int | None = None) -> dict[str, Any]:
    """Commit a terminal child observation and its continuation atomically.

    The completion boundary is deliberately idempotent.  A retried completion
    returns the original action and business version without appending another
    event, while a stale caller is rejected before either the child or run is
    changed.  The caller must have already persisted the child's terminal
    delivery state; this function owns only the indivisible continuation write.
    """
    if child_kind not in {"spec", "ticket"} or not child_id:
        raise ValueError("child identity is required")
    with db.transaction():
        table = "specs" if child_kind == "spec" else "tickets"
        db._check_version(run_id, expected_version)
        row = db.conn.execute(f"SELECT status FROM {table} WHERE {child_kind}_id=? AND run_id=?" if child_kind == "spec" else
                              f"SELECT t.status FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE t.ticket_id=? AND s.run_id=?",
                              (child_id, run_id)).fetchone()
        if row is None:
            raise ValueError("child not found")
        if row[0] not in {"closed", "cancelled"}:
            raise ControllerRecoveryError("child_not_terminal")
        if next_action is None:
            from next_action import next_action as compute_next_action
            next_action = compute_next_action(db, run_id)
        if not isinstance(next_action, Mapping) or not next_action.get("kind") or not next_action.get("target"):
            raise ControllerRecoveryError("next_action_missing")
        key = f"{run_id}:{next_action['kind']}:{next_action['target']}"
        existing = db.conn.execute("SELECT * FROM actions WHERE idempotency_key=?", (key,)).fetchone()
        if existing is None:
            active = db.conn.execute(
                "SELECT action_id,kind,target FROM actions WHERE run_id=? AND status IN ('pending','running') ORDER BY action_id LIMIT 1",
                (run_id,),
            ).fetchone()
            if active is not None:
                raise ActionConflict(f"action {active[0]} already owns run {run_id}: {active[1]}:{active[2]}")
            stamp = now()
            cur = db.conn.execute("INSERT INTO actions(run_id,kind,target,status,idempotency_key,result,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                                  (run_id, next_action["kind"], next_action["target"], "pending", key, json.dumps(dict(result or {}), ensure_ascii=False), stamp, stamp))
            action = dict(db.conn.execute("SELECT * FROM actions WHERE action_id=?", (cur.lastrowid,)).fetchone())
            version = db._business_event(run_id, child_kind, child_id, f"{child_kind}_completed_next_action_persisted", {
                "child_result": dict(result or {}), "next_action": dict(next_action), "action_id": action["action_id"],
            })
            db.conn.execute("UPDATE runs SET current_action=?,recovery_action=NULL,updated_at=? WHERE run_id=?",
                            (f"{next_action['kind']}:{next_action['target']}", now(), run_id))
            return {"child_id": child_id, "action": action, "next_action": dict(next_action),
                    "business_version": version, "created": True}
        action = dict(existing)
        if action["run_id"] != run_id:
            raise ControllerRecoveryError("action_run_mismatch")
        # The same idempotency key is the authoritative completion receipt.  Do
        # not emit a second business event or move the run backwards on retry.
        return {"child_id": child_id, "action": action, "next_action": dict(next_action),
                "business_version": db.business_version(run_id), "created": False}


def classify_controller_failure(*, error: Mapping[str, Any] | str | None = None,
                                completion: Mapping[str, Any] | None = None) -> str:
    from managed_recovery import classify_turn_outcome
    return classify_turn_outcome(completion=completion, error=error)


def persist_failure_action(db: ControlDB, run_id: str, classification: str, *, target: str | None = None,
                          evidence: list[str] | None = None) -> dict[str, Any]:
    actions = {"model_capacity": "recover_capacity", "stream_disconnected": "checkpoint_continue",
               "uncertain": "reconcile_side_effects", "blocked": "repair_identity",
               "terminal_failure": "repair_controller"}
    kind = actions.get(classification, "repair_controller")
    target = target or run_id
    key = f"{run_id}:{kind}:{target}"
    with db.transaction():
        existing = db.conn.execute("SELECT * FROM actions WHERE idempotency_key=?", (key,)).fetchone()
        if existing:
            return {"action_id": existing["action_id"], "kind": kind, "target": target,
                    "classification": classification, "changed": False,
                    "evidence": evidence or [f"controller://failure/{classification}"]}
        active = db.conn.execute("SELECT action_id,kind,target FROM actions WHERE run_id=? AND status IN ('pending','running') LIMIT 1", (run_id,)).fetchone()
        if active:
            raise ActionConflict(f"action {active[0]} already owns run {run_id}: {active[1]}:{active[2]}")
        stamp = now()
        cur = db.conn.execute("INSERT INTO actions(run_id,kind,target,status,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (run_id, kind, target, "pending", key, stamp, stamp))
        db.conn.execute("UPDATE runs SET current_action=?,recovery_action=?,updated_at=? WHERE run_id=?", (f"{kind}:{target}", kind, stamp, run_id))
        db._business_event(run_id, "controller", run_id, "controller_failure_action_persisted", {"classification": classification, "action_id": cur.lastrowid, "evidence": evidence or []})
        return {"action_id": cur.lastrowid, "kind": kind, "target": target,
                "classification": classification, "changed": True,
                "evidence": evidence or [f"controller://failure/{classification}"]}
