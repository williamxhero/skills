"""Cross-check planned, applied, and executed route evidence."""
from __future__ import annotations

import json
from typing import Any, Mapping

from route_summary import dispatch_route_gate, inherit_ticket_route, route_status


def _decode(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def verify_spec_route(db, spec_id: str, *, thread: Mapping[str, Any], execution: Mapping[str, Any] | None = None) -> dict[str, Any]:
    spec = db.conn.execute("SELECT route_summary,route_summary_digest,route_summary_status FROM specs WHERE spec_id=?", (spec_id,)).fetchone()
    if spec is None:
        return {"decision": "blocked", "reason": "spec_not_found"}
    summary = _decode(spec[0])
    applied = _decode(thread.get("route_readback"))
    identity = _decode(thread.get("identity_readback")) or dict(thread)
    result = dispatch_route_gate(summary=summary, applied=applied, identity=identity,
                                 expected={"model": summary.get("planned_model") if isinstance(summary, Mapping) else None,
                                           "effort": summary.get("planned_effort") if isinstance(summary, Mapping) else None})
    if result["decision"] != "allow":
        return result
    result["status"] = route_status(summary=summary, applied=applied, execution=execution)
    result["evidence"] = [f"sqlite://spec-route/{spec_id}", "applied-route-readback"]
    return result


def ticket_route(db, ticket_id: str) -> dict[str, Any]:
    row = db.conn.execute("SELECT t.ticket_id,t.spec_id,s.route_summary FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE t.ticket_id=?", (ticket_id,)).fetchone()
    if row is None:
        raise ValueError("ticket not found")
    summary = _decode(row[2])
    inherited = inherit_ticket_route(summary, {})
    return {"ticket_id": ticket_id, "spec_id": row[1], "route": inherited}
