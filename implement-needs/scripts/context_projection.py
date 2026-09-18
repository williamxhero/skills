"""Small, version-bound context projections for controller workers."""
from __future__ import annotations

import json
import math
from urllib.parse import quote, unquote


PHASE_PROJECTIONS = frozenset({
    "startup", "planning", "implementation", "verification", "recovery", "release",
})
ENTITY_TYPES = frozenset({"run", "spec", "ticket", "intent"})


class ContextProjectionError(ValueError):
    def __init__(self, code, details=None):
        self.code = code
        self.details = details or {}
        super().__init__(code)


def _json(value, *, field):
    try:
        result = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        raise ContextProjectionError("context_corrupt", {"field": field}) from None
    return result


def _evidence(db, run_id, entity_type, entity_id):
    rows = db.conn.execute(
        "SELECT evidence_id,evidence_kind,payload FROM evidence_refs "
        "WHERE run_id=? AND entity_type=? AND entity_id=? ORDER BY evidence_id",
        (run_id, entity_type, entity_id),
    ).fetchall()
    return [
        {"evidence_id": row[0], "kind": row[1], "pointer": f"evidence://{run_id}/{entity_type}/{quote(entity_id, safe='')}"}
        for row in rows
    ]


def _pointer(entity_type, entity_id):
    return f"history://{entity_type}/{quote(entity_id, safe='')}"


def _run_context(db, run_id, phase, entity_type, entity_id):
    row = db.conn.execute(
        "SELECT run_id,initiative,requirement,status,current_action,run_phase,terminal_result,stop_reason "
        "FROM runs WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None:
        raise ContextProjectionError("context_entity_missing", {"entity_type": "run", "entity_id": run_id})
    if entity_type != "run" or entity_id != run_id:
        raise ContextProjectionError("context_entity_mismatch", {"expected": {"entity_type": "run", "entity_id": run_id}})
    return {
        "status": row[3],
        "run_phase": row[5],
        "current_action": row[4],
        "terminal_result": row[6],
        "stop_reason": row[7],
    }


def _spec_context(db, run_id, phase, entity_id):
    row = db.conn.execute(
        "SELECT spec_id,title,status,position,blocked_by,acceptance FROM specs WHERE run_id=? AND spec_id=?",
        (run_id, entity_id),
    ).fetchone()
    if row is None:
        raise ContextProjectionError("context_entity_missing", {"entity_type": "spec", "entity_id": entity_id})
    blocked_by = _json(row[4], field="spec.blocked_by")
    deps = []
    for dep in blocked_by:
        item = db.conn.execute("SELECT spec_id,title,status,position FROM specs WHERE run_id=? AND spec_id=?", (run_id, dep)).fetchone()
        if item is None:
            raise ContextProjectionError("context_dependency_missing", {"spec_id": entity_id, "dependency": dep})
        deps.append({"spec_id": item[0], "title": item[1], "status": item[2], "position": item[3], "pointer": _pointer("spec", item[0])})
    evidence = _evidence(db, run_id, "spec", entity_id)
    if row[2] in {"closed", "cancelled"}:
        return {
            "status": row[2],
            "delivery_summary": {"spec_id": row[0], "title": row[1], "position": row[3]},
            "history_pointer": _pointer("spec", entity_id),
            "evidence_pointers": evidence,
        }
    decisions = [
        {"subject": item[0], "selected": item[1], "decision_id": item[2], "evidence_count": len(_json(item[3], field="decision.evidence"))}
        for item in db.conn.execute("SELECT subject,selected,decision_id,evidence FROM decisions WHERE run_id=? ORDER BY decision_id", (run_id,))
    ]
    return {
        "status": row[2],
        "spec": {"spec_id": row[0], "title": row[1], "position": row[3], "acceptance": _json(row[5], field="spec.acceptance")},
        "direct_dependencies": deps,
        "decisions": decisions,
        "evidence_summary": evidence,
    }


def _ticket_context(db, run_id, phase, entity_id):
    row = db.conn.execute(
        "SELECT t.ticket_id,t.title,t.status,t.queue_position,t.blocked_by,t.commits,t.tests,t.acceptance,s.spec_id "
        "FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE s.run_id=? AND t.ticket_id=?",
        (run_id, entity_id),
    ).fetchone()
    if row is None:
        raise ContextProjectionError("context_entity_missing", {"entity_type": "ticket", "entity_id": entity_id})
    blockers = _json(row[4], field="ticket.blocked_by")
    if row[2] in {"closed", "cancelled"}:
        return {
            "status": row[2],
            "delivery_summary": {"ticket_id": row[0], "title": row[1], "spec_id": row[8], "queue_position": row[3]},
            "evidence": {"commits": _json(row[5], field="ticket.commits"), "tests": _json(row[6], field="ticket.tests"), "acceptance": _json(row[7], field="ticket.acceptance")},
            "history_pointer": _pointer("ticket", entity_id),
            "evidence_pointers": _evidence(db, run_id, "ticket", entity_id),
        }
    blocker_rows = []
    for blocker in blockers:
        item = db.conn.execute("SELECT ticket_id,title,status,queue_position FROM tickets WHERE ticket_id=?", (blocker,)).fetchone()
        if item is None:
            raise ContextProjectionError("context_dependency_missing", {"ticket_id": entity_id, "dependency": blocker})
        blocker_rows.append({"ticket_id": item[0], "title": item[1], "status": item[2], "queue_position": item[3], "pointer": _pointer("ticket", item[0])})
    return {
        "status": row[2],
        "ticket": {"ticket_id": row[0], "title": row[1], "spec_id": row[8], "queue_position": row[3]},
        "direct_blockers": blocker_rows,
        "evidence_summary": _evidence(db, run_id, "ticket", entity_id),
    }


def build_context(db, run_id, phase, entity_type, entity_id):
    if phase not in PHASE_PROJECTIONS:
        raise ContextProjectionError("context_phase_unknown", {"phase": phase})
    if entity_type not in ENTITY_TYPES or not isinstance(entity_id, str) or not entity_id.strip():
        raise ContextProjectionError("context_entity_invalid", {"entity_type": entity_type, "entity_id": entity_id})
    version = db.business_version(run_id)
    if entity_type == "run":
        body = _run_context(db, run_id, phase, entity_type, entity_id)
    elif entity_type == "spec":
        body = _spec_context(db, run_id, phase, entity_id)
    elif entity_type == "ticket":
        body = _ticket_context(db, run_id, phase, entity_id)
    else:
        row = db.conn.execute("SELECT intent_id,status,target,target_state FROM operation_intents WHERE run_id=? AND intent_id=?", (run_id, entity_id)).fetchone()
        if row is None:
            raise ContextProjectionError("context_entity_missing", {"entity_type": entity_type, "entity_id": entity_id})
        body = {"intent": {"intent_id": row[0], "status": row[1], "target": row[2], "target_state": row[3]}, "evidence_summary": _evidence(db, run_id, "intent", entity_id)}
    return {
        "run_id": run_id,
        "phase": phase,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "read_business_version": version,
        "projection": body,
    }


def read_history(db, pointer):
    if not isinstance(pointer, str) or not pointer.startswith("history://"):
        raise ContextProjectionError("history_pointer_invalid")
    parts = pointer[len("history://"):].split("/", 1)
    if len(parts) != 2 or parts[0] not in {"run", "spec", "ticket", "intent"} or not parts[1]:
        raise ContextProjectionError("history_pointer_invalid", {"pointer": pointer})
    entity_type, encoded_id = parts
    entity_id = unquote(encoded_id)
    if entity_type == "run":
        row = db.conn.execute("SELECT * FROM runs WHERE run_id=?", (entity_id,)).fetchone()
    elif entity_type == "spec":
        row = db.conn.execute("SELECT * FROM specs WHERE spec_id=?", (entity_id,)).fetchone()
    elif entity_type == "ticket":
        row = db.conn.execute("SELECT * FROM tickets WHERE ticket_id=?", (entity_id,)).fetchone()
    else:
        row = db.conn.execute("SELECT * FROM operation_intents WHERE intent_id=?", (entity_id,)).fetchone()
    if row is None:
        raise ContextProjectionError("history_unreachable", {"pointer": pointer})
    return {"pointer": pointer, "entity_type": entity_type, "entity_id": entity_id, "record": dict(row)}


def measure_context(context, *, latency_ms=None, observed_tokens=None, fee=None, refresh_count=0, rejection_count=0):
    """Measure transport cost while keeping unavailable provider data explicit."""
    if not isinstance(context, dict) or "projection" not in context:
        raise ContextProjectionError("context_envelope_invalid")
    encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "payload_bytes": len(encoded),
        "token_estimate": max(1, math.ceil(len(encoded) / 4)),
        "observed_tokens": observed_tokens,
        "fee": fee,
        "latency_ms": latency_ms,
        "refresh_count": refresh_count,
        "rejection_count": rejection_count,
        "coverage": {
            "tokens": "observed" if observed_tokens is not None else "estimated",
            "fees": "observed" if fee is not None else "unknown",
            "latency": "observed" if latency_ms is not None else "unknown",
        },
    }
