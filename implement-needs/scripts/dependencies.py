"""Evaluate dependency delivery without equating terminal failure with success."""
from __future__ import annotations

import json

from delivery_receipts import project


ENTITY_TABLES = {"spec": "specs", "ticket": "tickets"}


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
        for blocker_id in json.loads(spec["blocked_by"] or "[]"):
            if blocker_id not in spec_positions:
                errors.append({"dependent": spec["spec_id"], "dependent_type": "spec", "blocker": blocker_id, "blocker_type": "spec", "code": "unknown_blocker"})
            elif spec_positions[blocker_id] >= spec["position"]:
                errors.append({"dependent": spec["spec_id"], "dependent_type": "spec", "blocker": blocker_id, "blocker_type": "spec", "code": "forward_dependency"})
            else:
                status = dependency_status(db, "spec", spec["spec_id"], "spec", blocker_id)
                if not status["satisfied"]:
                    errors.append({"dependent": spec["spec_id"], "dependent_type": "spec", "blocker": blocker_id, "blocker_type": "spec", "code": status["code"]})
    tickets = db.conn.execute(
        "SELECT t.ticket_id,t.blocked_by FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE s.run_id=?", (run_id,)
    ).fetchall()
    for ticket in tickets:
        for blocker_id in json.loads(ticket["blocked_by"] or "[]"):
            status = dependency_status(db, "ticket", ticket["ticket_id"], "ticket", blocker_id)
            if not status["satisfied"]:
                errors.append({"dependent": ticket["ticket_id"], "dependent_type": "ticket", "blocker": blocker_id, "blocker_type": "ticket", "code": status["code"]})
    return errors
