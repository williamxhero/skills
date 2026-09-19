"""Versioned snapshots and phase-minimal recovery context."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from control_db import ControlDB


def _decode(value: str, default: Any):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def build_snapshot(db: ControlDB, run_id: str) -> dict[str, Any]:
    state = db.snapshot(run_id)
    if not state["run"]:
        raise ValueError("unknown run")
    specs = state["specs"]
    tickets = state["tickets"]
    proofs = [dict(row) for row in db.conn.execute(
        "SELECT * FROM delivery_proofs WHERE entity_id IN (SELECT spec_id FROM specs WHERE run_id=?) "
        "OR entity_id IN (SELECT ticket_id FROM tickets WHERE spec_id IN (SELECT spec_id FROM specs WHERE run_id=?)) "
        "ORDER BY proof_id", (run_id, run_id)
    )]
    specs_by_id = {spec["spec_id"]: spec for spec in specs}
    dependencies = []
    for spec in specs:
        for blocker in _decode(spec["blocked_by"], []):
            upstream = specs_by_id.get(blocker)
            dependencies.append({
                "dependent_spec_id": spec["spec_id"],
                "upstream_spec_id": blocker,
                "upstream_status": upstream["status"] if upstream else "unknown",
                "upstream_acceptance": _decode(upstream["acceptance"], []) if upstream else [],
                "delivery_proofs": [proof for proof in proofs if proof["entity_id"] == blocker],
            })
    return {
        "run": state["run"],
        "specs": specs,
        "tickets": tickets,
        "threads": state["threads"],
        "actions": state["actions"],
        "acceptance": [item for spec in specs for item in _decode(spec["acceptance"], [])],
        "direct_dependencies": dependencies,
        "decisions": [dict(row) for row in db.conn.execute("SELECT * FROM decisions WHERE run_id=? ORDER BY decision_id", (run_id,))],
        "evidence": proofs,
    }


def assemble_context(db: ControlDB, run_id: str, phase: str) -> dict[str, Any]:
    if not isinstance(phase, str) or not phase.strip():
        raise ValueError("phase is required")
    stored = db.read_snapshot(run_id)
    if stored is None:
        payload = build_snapshot(db, run_id)
        saved = db.save_snapshot(run_id, payload, db.event_cursor(run_id))
        stored = db.read_snapshot(run_id) or saved
    return compact_context_from_snapshot(db, run_id, phase, stored)


def compact_context_from_snapshot(db: ControlDB, run_id: str, phase: str, stored: dict[str, Any]) -> dict[str, Any]:
    """Build the compact envelope from an already persisted snapshot."""
    cursor = int(stored["event_cursor"])
    return {
        "run_id": run_id,
        "phase": phase,
        "state_version": int(stored["state_version"]),
        "business_version": int(stored["state_version"]),
        "event_cursor": cursor,
        "acceptance": stored["payload"].get("acceptance", []),
        "direct_dependencies": stored["payload"].get("direct_dependencies", []),
        "decisions": stored["payload"].get("decisions", []),
        "worktree": stored["payload"].get("worktree", {}),
        "version": stored["payload"].get("version", {}),
        "evidence": stored["payload"].get("evidence", []),
        "snapshot_pointer": f"snapshot://run/{quote(run_id, safe='')}",
        "events": db.events_since(run_id, cursor),
        "unresolved_exceptions": db.unresolved_exceptions(run_id),
    }


def refresh_context(db: ControlDB, run_id: str, phase: str) -> dict[str, Any]:
    payload = build_snapshot(db, run_id)
    db.save_snapshot(run_id, payload, db.event_cursor(run_id))
    return assemble_context(db, run_id, phase)


def advance_snapshot(db: ControlDB, run_id: str, payload: dict[str, Any], expected_event_cursor: int) -> dict[str, Any]:
    return db.save_snapshot(run_id, payload, expected_event_cursor)
