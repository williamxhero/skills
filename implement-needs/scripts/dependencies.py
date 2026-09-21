"""Evaluate dependency delivery without equating terminal failure with success."""
from __future__ import annotations

import json
import re

from delivery_receipts import project


ENTITY_TABLES = {"spec": "specs", "ticket": "tickets"}
_TICKET_ALIAS_RE = re.compile(r"^(?P<spec>[^/]+)/(?P<ordinal>[0-9]{2})$")
_ACTIVE_TICKET_STATUSES = {"planned", "ready", "implementing", "verified", "merged"}


def _decode_blockers(value):
    try:
        blockers = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(blockers, list) or any(not isinstance(item, str) or not item.strip() for item in blockers):
        return None
    return blockers


def resolve_ticket_alias(db, dependent_ticket_id, blocker_id):
    """Resolve a SPEC-local ticket alias to one run-owned ticket.

    ``#95/01`` means the first ticket registered under SPEC ``#95``.  The
    alias is only valid for a dependent ticket in that same SPEC.  Malformed,
    unknown, cross-SPEC, and ambiguous aliases fail closed.
    """
    dependent = db.conn.execute(
        "SELECT t.spec_id,s.run_id FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE t.ticket_id=?",
        (dependent_ticket_id,),
    ).fetchone()
    if dependent is None:
        raise ValueError("unknown dependent ticket")
    match = _TICKET_ALIAS_RE.fullmatch(blocker_id) if isinstance(blocker_id, str) else None
    if match is None:
        raise ValueError(f"malformed ticket alias: {blocker_id}")
    alias_spec = match.group("spec")
    ordinal = int(match.group("ordinal"))
    if ordinal < 1 or alias_spec != dependent["spec_id"]:
        raise ValueError(f"cross-SPEC or invalid ticket alias: {blocker_id}")
    rows = db.conn.execute(
        "SELECT t.ticket_id,t.queue_position FROM tickets t WHERE t.spec_id=? AND "
        "EXISTS (SELECT 1 FROM specs s WHERE s.spec_id=t.spec_id AND s.run_id=?) "
        "ORDER BY t.queue_position,t.ticket_id",
        (alias_spec, dependent["run_id"]),
    ).fetchall()
    if ordinal > len(rows):
        raise ValueError(f"unknown ticket alias: {blocker_id}")
    positions = [row["queue_position"] for row in rows]
    if len(positions) != len(set(positions)):
        raise ValueError(f"ambiguous ticket alias: {blocker_id}")
    return rows[ordinal - 1]["ticket_id"]


def dependency_status(db, dependent_type, dependent_id, blocker_type, blocker_id):
    """Return a deterministic delivery/waiver decision for one dependency edge."""
    if blocker_type not in ENTITY_TABLES:
        return {"satisfied": False, "code": "unsupported_blocker_type"}
    waiver = db.conn.execute(
        "SELECT * FROM dependency_waivers WHERE dependent_type=? AND dependent_id=? AND blocker_type=? AND blocker_id=?",
        (dependent_type, dependent_id, blocker_type, blocker_id),
    ).fetchone()
    if waiver:
        return {"satisfied": True, "code": "waived", "waiver_id": waiver["waiver_id"]}
    if blocker_type == "spec":
        row = db.conn.execute("SELECT status,run_id FROM specs WHERE spec_id=?", (blocker_id,)).fetchone()
    else:
        row = db.conn.execute(
            "SELECT t.status,s.run_id FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE t.ticket_id=?",
            (blocker_id,),
        ).fetchone()
    if not row:
        return {"satisfied": False, "code": "unknown_blocker"}
    if row["status"] != "closed":
        return {"satisfied": False, "code": "blocker_not_delivered", "status": row["status"]}
    proof = db.conn.execute(
        "SELECT proof_id FROM delivery_proofs WHERE entity_type=? AND entity_id=? AND artifact_type='delivery'",
        (blocker_type, blocker_id),
    ).fetchone()
    if not proof:
        proof = db.conn.execute(
            "SELECT evidence_id FROM evidence_refs WHERE entity_type=? AND entity_id=? AND evidence_kind='delivery_proof' LIMIT 1",
            (blocker_type, blocker_id),
        ).fetchone()
    if not proof:
        receipt = project(db, row["run_id"], blocker_type, blocker_id)
        if receipt["decision"] == "allow":
            return {"satisfied": True, "code": "delivered", "receipt_id": receipt["receipt"]["receipt_id"]}
        return {"satisfied": False, "code": "delivery_proof_missing"}
    return {"satisfied": True, "code": "delivered", "proof_id": proof["proof_id"]}


def validation_errors(db, run_id):
    """Find every persisted dependency that cannot safely be treated as satisfied."""
    errors = []
    specs = db.conn.execute("SELECT spec_id,position,blocked_by FROM specs WHERE run_id=?", (run_id,)).fetchall()
    spec_positions = {row["spec_id"]: row["position"] for row in specs}
    for spec in specs:
        blockers = _decode_blockers(spec["blocked_by"])
        if blockers is None:
            errors.append({"dependent": spec["spec_id"], "blocker": None, "code": "dependency_list_invalid"})
            continue
        for blocker_id in blockers:
            if blocker_id not in spec_positions:
                errors.append({"dependent": spec["spec_id"], "blocker": blocker_id, "code": "unknown_blocker"})
            elif spec_positions[blocker_id] >= spec["position"]:
                errors.append({"dependent": spec["spec_id"], "blocker": blocker_id, "code": "forward_dependency"})
            else:
                status = dependency_status(db, "spec", spec["spec_id"], "spec", blocker_id)
                if not status["satisfied"]:
                    errors.append({"dependent": spec["spec_id"], "blocker": blocker_id, "code": status["code"]})
    tickets = db.conn.execute(
        "SELECT t.ticket_id,t.spec_id,t.queue_position,t.blocked_by,s.run_id "
        "FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE s.run_id=?",
        (run_id,),
    ).fetchall()
    ticket_by_id = {ticket["ticket_id"]: ticket for ticket in tickets}
    for ticket in tickets:
        blockers = _decode_blockers(ticket["blocked_by"])
        if blockers is None:
            errors.append({"dependent": ticket["ticket_id"], "blocker": None, "code": "dependency_list_invalid"})
            continue
        for blocker_id in blockers:
            blocker = ticket_by_id.get(blocker_id)
            if blocker is None:
                outside_run = db.conn.execute(
                    "SELECT t.spec_id,s.run_id FROM tickets t JOIN specs s ON s.spec_id=t.spec_id "
                    "WHERE t.ticket_id=?",
                    (blocker_id,),
                ).fetchone()
                errors.append({
                    "dependent": ticket["ticket_id"],
                    "blocker": blocker_id,
                    "code": "cross_run_dependency" if outside_run is not None else "unknown_blocker",
                })
                continue
            status = dependency_status(db, "ticket", ticket["ticket_id"], "ticket", blocker_id)
            if blocker["spec_id"] != ticket["spec_id"]:
                if status["satisfied"]:
                    continue
                errors.append({
                    "dependent": ticket["ticket_id"],
                    "blocker": blocker_id,
                    "code": "cross_spec_dependency",
                })
                continue
            if (
                ticket["queue_position"] is None
                or blocker["queue_position"] is None
                or blocker["queue_position"] >= ticket["queue_position"]
            ):
                errors.append({
                    "dependent": ticket["ticket_id"],
                    "blocker": blocker_id,
                    "code": "forward_dependency",
                })
                continue
            if not status["satisfied"]:
                # A same-SPEC edge to an earlier queue item is the normal
                # sequential work graph.  Its undelivered state is handled by
                # the planner when it reaches that earlier ticket; reporting
                # it here would turn an ordinary queue wait into repair.
                if (
                    status["code"] == "blocker_not_delivered"
                    and status.get("status") in _ACTIVE_TICKET_STATUSES
                ):
                    continue
                errors.append({"dependent": ticket["ticket_id"], "blocker": blocker_id, "code": status["code"]})
    return errors
