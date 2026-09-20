"""Advance deterministic controller work until an explicit decision boundary."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from control_db import ControlDB
from next_action import next_action


BOUNDARIES = frozenset({"needs_llm", "waiting_external", "blocked", "completed"})
TICKET_ADVANCE = {"planned": "ready", "ready": "implementing", "implementing": "verified", "verified": "merged", "merged": "closed"}


def _cursor(db, run_id):
    row = db.conn.execute("SELECT COALESCE(MAX(event_id), 0) FROM events WHERE run_id=?", (run_id,)).fetchone()
    return int(row[0])


def _result(db, run_id, boundary, action=None, reason=None, processed=None):
    if boundary not in BOUNDARIES:
        raise ValueError(f"invalid boundary: {boundary}")
    return {
        "run_id": run_id,
        "boundary": boundary,
        "reason": reason or boundary,
        "action": action,
        "processed_actions": processed or [],
        "state_version": _cursor(db, run_id),
        "event_cursor": _cursor(db, run_id),
        "evidence": [f"sqlite://events/{_cursor(db, run_id)}"],
    }


def _waiting_result(db, run_id, action, reason, processed):
    result = _result(db, run_id, "waiting_external", action, reason, processed)
    target = action.get("target", run_id)
    request_id = action.get("external_request_id") or f"wait:{run_id}:{action['kind']}:{target}"
    wake_condition = action.get("wake_condition") or "authoritative external state changes"
    next_safe = action.get("next_safe_check_at") or (
        datetime.now(timezone.utc) + timedelta(seconds=30)
    ).isoformat()
    db.record_external_wait(request_id, run_id, result["event_cursor"], wake_condition, next_safe, action.get("action_id"))
    result.update({
        "external_request_id": request_id,
        "event_cursor": result["event_cursor"],
        "wake_condition": wake_condition,
        "next_safe_check_at": next_safe,
    })
    return result


def _deterministic_action(db, run_id, action):
    kind = action["kind"]
    if kind in {"advance_spec", "advance_ticket"}:
        try:
            action_id = db.advance_local_action(run_id, kind, action["target"], action["next_status"])
        except Exception as exc:
            action_id = db.set_action(run_id, kind, action["target"])
            db.finish_action(action_id, "blocked", error=str(exc))
            return None, _result(db, run_id, "blocked", action, "state_transition_rejected")
        return {"action_id": action_id, "kind": kind, "target": action["target"]}, None
    return None, None


def _pending_local_action(db, action):
    action = dict(action)
    if action["kind"] == "advance_spec":
        action["next_status"] = "ready"
    elif action["kind"] == "advance_ticket":
        row = db.conn.execute("SELECT status FROM tickets WHERE ticket_id=?", (action["target"],)).fetchone()
        if row and row["status"] in TICKET_ADVANCE:
            action["next_status"] = TICKET_ADVANCE[row["status"]]
    return action


def advance(db: ControlDB, run_id: str, max_actions=32, *, include_recovery: bool = True):
    """Execute deterministic local transitions and stop at the first boundary."""
    if not isinstance(max_actions, int) or isinstance(max_actions, bool) or max_actions < 1:
        raise ValueError("max_actions must be a positive integer")
    processed = []
    from controller_recovery import lost_wakeup_candidate, reconcile_controller_interruption
    if include_recovery and lost_wakeup_candidate(db, run_id):
        reconcile_controller_interruption(db, run_id, reason="completed_spec_without_persisted_next_action")
    for _ in range(max_actions):
        action = next_action(db, run_id, include_recovery=include_recovery)
        # Legacy runs may not have completed the newer preflight phase.  Keep
        # deterministic local SPEC promotion available, but never infer a
        # semantic ticketing or external mutation from this compatibility path.
        if action.get("kind") == "run_preflight":
            spec = db.conn.execute("SELECT spec_id,status FROM specs WHERE run_id=? ORDER BY position LIMIT 1", (run_id,)).fetchone()
            if spec is not None and spec[1] == "planned":
                action = {"kind": "advance_spec", "target": spec[0], "next_status": "ready"}
            elif spec is not None and spec[1] == "ready":
                action = {"kind": "ticket_current_spec", "target": spec[0]}
            elif spec is not None and spec[1] in {"blocked", "cancelled"}:
                action = {"kind": "repair_spec", "target": spec[0], "reason": "spec_not_executable"}
            elif spec is None:
                action = {"kind": "final_release", "target": run_id}
        pending = db.conn.execute(
            "SELECT status FROM actions WHERE action_id=?", (action.get("action_id"),)
        ).fetchone() if action.get("action_id") else None
        if pending and pending["status"] in {"pending", "running"}:
            if action["kind"] in {"advance_spec", "advance_ticket"}:
                action = _pending_local_action(db, action)
                if "next_status" not in action:
                    return _result(db, run_id, "blocked", action, "local_action_reconciliation_required", processed)
                item, boundary = _deterministic_action(db, run_id, action)
                if boundary is not None:
                    boundary["processed_actions"] = processed
                    return boundary
                processed.append(item)
                continue
            return _waiting_result(db, run_id, action, "action_in_flight", processed)
        item, boundary = _deterministic_action(db, run_id, action)
        if boundary is not None:
            boundary["processed_actions"] = processed
            return boundary
        if item is not None:
            processed.append(item)
            continue
        kind = action["kind"]
        if kind in {"wait_spec", "wait_ticket", "wait_ticket_blocker", "wait_spec_dependency", "wait_ticket_receipt", "wait_external"}:
            reason = "schedule_resume" if kind == "wait_external" else "external_state_unchanged"
            return _waiting_result(db, run_id, action, reason, processed)
        if kind in {"repair_spec", "repair_ticket", "repair_queue", "repair_dependency", "repair_run"}:
            return _result(db, run_id, "blocked", action, action.get("reason", kind), processed)
        if kind == "final_release":
            specs = db.conn.execute("SELECT status FROM specs WHERE run_id=?", (run_id,)).fetchall()
            if all(row["status"] == "closed" for row in specs) and db.terminal_validation_satisfied(run_id):
                return _result(db, run_id, "completed", action, "terminal_frontier_empty", processed)
            return _result(db, run_id, "needs_llm", action, "terminal_validation_required", processed)
        return _result(db, run_id, "needs_llm", action, "semantic_or_external_operation", processed)
    return _result(db, run_id, "needs_llm", next_action(db, run_id), "advance_budget_exhausted", processed)
