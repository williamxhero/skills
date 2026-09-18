"""Advance deterministic controller work until an explicit decision boundary."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from control_db import ControlDB
from next_action import next_action


BOUNDARIES = frozenset({"needs_llm", "waiting_external", "blocked", "completed"})


def _cursor(db):
    row = db.conn.execute("SELECT COALESCE(MAX(event_id), 0) FROM events").fetchone()
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
        "state_version": _cursor(db),
        "event_cursor": _cursor(db),
        "evidence": [f"sqlite://events/{_cursor(db)}"],
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
    if kind == "advance_spec":
        action_id = db.set_action(run_id, kind, action["target"])
        try:
            db.update_spec(action["target"], action["next_status"])
        except Exception as exc:
            db.finish_action(action_id, "blocked", error=str(exc))
            return None, _result(db, run_id, "blocked", action, "state_transition_rejected")
        db.finish_action(action_id, "succeeded", {"next_status": action["next_status"]})
        return {"action_id": action_id, "kind": kind, "target": action["target"]}, None
    if kind == "advance_ticket":
        action_id = db.set_action(run_id, kind, action["target"])
        try:
            db.update_ticket(action["target"], action["next_status"])
        except Exception as exc:
            db.finish_action(action_id, "blocked", error=str(exc))
            return None, _result(db, run_id, "blocked", action, "state_transition_rejected")
        db.finish_action(action_id, "succeeded", {"next_status": action["next_status"]})
        return {"action_id": action_id, "kind": kind, "target": action["target"]}, None
    return None, None


def advance(db: ControlDB, run_id: str, max_actions=32):
    """Execute deterministic local transitions and stop at the first boundary."""
    if not isinstance(max_actions, int) or isinstance(max_actions, bool) or max_actions < 1:
        raise ValueError("max_actions must be a positive integer")
    processed = []
    for _ in range(max_actions):
        action = next_action(db, run_id)
        pending = db.conn.execute(
            "SELECT status FROM actions WHERE action_id=?", (action.get("action_id"),)
        ).fetchone() if action.get("action_id") else None
        if pending and pending["status"] in {"pending", "running"}:
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
            if all(row["status"] == "closed" for row in specs):
                return _result(db, run_id, "completed", action, "terminal_frontier_empty", processed)
            return _result(db, run_id, "blocked", action, "terminal_evidence_required", processed)
        return _result(db, run_id, "needs_llm", action, "semantic_or_external_operation", processed)
    return _result(db, run_id, "needs_llm", next_action(db, run_id), "advance_budget_exhausted", processed)
