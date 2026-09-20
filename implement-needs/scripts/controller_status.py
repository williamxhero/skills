"""Compact status projection for SPEC entry and controller recovery."""
from __future__ import annotations

import json
from typing import Any

from controller_recovery import active_action_invariant
from route_visibility import verify_spec_route


def spec_status(db, run_id: str, spec_id: str, *, thread_id: str | None = None,
                execution: dict[str, Any] | None = None) -> dict[str, Any]:
    run = db.conn.execute("SELECT run_phase,status FROM runs WHERE run_id=?", (run_id,)).fetchone()
    spec = db.conn.execute("SELECT * FROM specs WHERE spec_id=? AND run_id=?", (spec_id, run_id)).fetchone()
    if run is None or spec is None:
        return {"decision": "blocked", "reason": "status_identity_missing", "run_id": run_id, "spec_id": spec_id}
    result: dict[str, Any] = {"run_id": run_id, "spec_id": spec_id, "run_phase": run[0],
                              "run_status": run[1], "spec_status": spec["status"],
                              "controller": active_action_invariant(db, run_id), "route": None}
    if thread_id:
        thread = db.conn.execute("SELECT * FROM threads WHERE thread_id=? AND run_id=?", (thread_id, run_id)).fetchone()
        if thread is None:
            return {**result, "decision": "blocked", "reason": "thread_identity_missing"}
        route = verify_spec_route(db, spec_id, thread=dict(thread), execution=execution)
        result["route"] = route.get("status") if route.get("decision") == "allow" else route
        result["decision"] = route.get("decision")
        if route.get("decision") != "allow":
            result["reason"] = route.get("reason")
    else:
        result["decision"] = "allow" if spec["route_summary_status"] == "verified" else "blocked"
        if result["decision"] != "allow":
            result["reason"] = "route_summary_missing"
    return result
