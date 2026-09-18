"""Compute the single safe next action from the SQLite run state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from control_db import ControlDB

TERMINAL_SPEC={"closed","cancelled"}


def _effective_ticket_blockers(ticket, positions):
    """Return blockers that can gate a ticket in an explicit global queue.

    The GitHub ledger keeps every declared ``blocked_by`` edge as business
    metadata.  A single-ticket line adds a second, authoritative ordering
    constraint.  A declaration pointing to a later queue position is a
    forward reference: enforcing it while walking the line would deadlock the
    queue (for example A0 tickets may refer to later Genome/Memory work).
    Earlier declared blockers remain gates, and the immediately preceding
    queue item is always a gate even when GitHub did not declare an edge.
    """
    current = ticket["queue_position"]
    declared = json.loads(ticket["blocked_by"] or "[]")
    effective = [
        blocker for blocker in declared
        if positions.get(blocker) is None or positions[blocker] < current
    ]
    if current > 1:
        predecessor = next(
            (ticket_id for ticket_id, position in positions.items() if position == current - 1),
            None,
        )
        if predecessor is not None and predecessor not in effective:
            effective.append(predecessor)
    return effective


def _phase_action(db: ControlDB, run_id: str, run) -> dict | None:
    actions = {
        "initialized": "run_preflight",
        "preflight_passed": "run_grill",
        "grilling": "run_planning",
        "final_verification": "advance_release",
        "release": "advance_synchronization",
        "synchronization": "complete_run",
    }
    if run[3] in actions:
        return {"kind": actions[run[3]], "target": run_id, "phase": run[3]}
    if run[3] == "planning":
        spec_count = db.conn.execute("SELECT COUNT(*) FROM specs WHERE run_id=?", (run_id,)).fetchone()[0]
        if spec_count == 0:
            return {"kind": "confirm_no_change", "target": run_id, "phase": "planning"}
        return {"kind": "enter_implementation", "target": run_id, "phase": "planning"}
    return None

def next_action(db: ControlDB, run_id: str) -> dict:
    pending=db.conn.execute("SELECT kind,target,action_id FROM actions WHERE run_id=? AND status IN ('pending','running') ORDER BY action_id LIMIT 1",(run_id,)).fetchone()
    if pending: return {"kind":pending[0],"target":pending[1],"action_id":pending[2]}
    run=db.conn.execute("SELECT execution_mode,controller_task_id,queue_definition,run_phase,terminal_result FROM runs WHERE run_id=?",(run_id,)).fetchone()
    if run is None: return {"kind":"repair_run","target":run_id,"reason":"run_not_found"}
    if run[4] is not None:
        return {"kind":"terminal","target":run_id,"result":run[4],"phase":run[3]}
    if run[0] == "single-ticket-line":
        if not run[1]:
            return {"kind":"repair_queue","target":run_id,"reason":"controller_task_id_missing"}
        try:
            queue_definition=json.loads(run[2] or "[]")
        except (TypeError, json.JSONDecodeError):
            return {"kind":"repair_queue","target":run_id,"reason":"queue_definition_invalid"}
        if not isinstance(queue_definition, list) or any(
            not isinstance(item, str) or not item.strip() for item in queue_definition
        ) or len(set(queue_definition)) != len(queue_definition):
            return {"kind":"repair_queue","target":run_id,"reason":"queue_definition_invalid"}
        tickets=db.conn.execute(
            "SELECT t.*,s.position AS spec_position FROM tickets t JOIN specs s ON s.spec_id=t.spec_id "
            "WHERE s.run_id=? ORDER BY t.queue_position IS NULL,t.queue_position,s.position,t.ticket_id",
            (run_id,),
        ).fetchall()
        if queue_definition:
            ticket_ids=[ticket["ticket_id"] for ticket in tickets]
            if len(ticket_ids) != len(queue_definition) or set(ticket_ids) != set(queue_definition):
                return {"kind":"repair_queue","target":run_id,"reason":"ticket_ledger_incomplete",
                        "expected_ticket_count":len(queue_definition),"actual_ticket_count":len(ticket_ids)}
        phase_action = _phase_action(db, run_id, run)
        if phase_action is not None:
            return phase_action
        terminal={"closed","cancelled"}
        positions = {
            ticket["ticket_id"]: ticket["queue_position"]
            for ticket in tickets
            if ticket["queue_position"] is not None
        }
        for ticket in tickets:
            if ticket["status"] in terminal:
                continue
            blockers=_effective_ticket_blockers(ticket, positions)
            rows=[db.conn.execute("SELECT status FROM tickets WHERE ticket_id=?",(blocker,)).fetchone() for blocker in blockers]
            if any(row is None or row[0] not in terminal for row in rows):
                return {"kind":"wait_ticket_blocker","target":ticket["ticket_id"],"controller_task_id":run[1]}
            spec=db.conn.execute("SELECT blocked_by FROM specs WHERE spec_id=?",(ticket["spec_id"],)).fetchone()
            spec_blockers=json.loads(spec[0] or "[]") if spec else []
            spec_rows=[db.conn.execute("SELECT status FROM specs WHERE spec_id=?",(blocker,)).fetchone() for blocker in spec_blockers]
            if any(row is None or row[0] not in TERMINAL_SPEC for row in spec_rows):
                return {"kind":"wait_ticket_blocker","target":ticket["ticket_id"],"controller_task_id":run[1]}
            status=ticket["status"]
            target=ticket["ticket_id"]
            if status == "planned": return {"kind":"advance_ticket","target":target,"next_status":"ready","controller_task_id":run[1]}
            if status == "ready": return {"kind":"dispatch_ticket","target":target,"controller_task_id":run[1]}
            if status == "implementing": return {"kind":"wait_ticket","target":target,"controller_task_id":run[1]}
            if status == "verified": return {"kind":"merge_ticket","target":target,"controller_task_id":run[1]}
            if status == "merged": return {"kind":"close_ticket","target":target,"controller_task_id":run[1]}
            if status == "blocked": return {"kind":"repair_ticket","target":target,"controller_task_id":run[1]}
        return {"kind":"final_verification","target":run_id,"controller_task_id":run[1]}
    phase_action = _phase_action(db, run_id, run)
    if phase_action is not None:
        return phase_action
    specs=db.conn.execute("SELECT * FROM specs WHERE run_id=? ORDER BY position",(run_id,)).fetchall()
    for spec in specs:
        if spec["status"] in TERMINAL_SPEC: continue
        blockers=json.loads(spec["blocked_by"])
        if any(db.conn.execute("SELECT status FROM specs WHERE spec_id=?",(x,)).fetchone()[0] not in TERMINAL_SPEC for x in blockers): continue
        status=spec["status"]
        if status=="planned": return {"kind":"advance_spec","target":spec["spec_id"],"next_status":"ready"}
        if status=="ready": return {"kind":"ticket_current_spec","target":spec["spec_id"]}
        if status=="ticketing": return {"kind":"wait_ticket_receipt","target":spec["spec_id"]}
        if status=="tickets_ready": return {"kind":"dispatch_spec","target":spec["spec_id"]}
        if status in {"implementing","handoff_received"}: return {"kind":"wait_spec","target":spec["spec_id"]}
        if status=="verifying": return {"kind":"verify_spec","target":spec["spec_id"]}
        if status=="ready_to_merge": return {"kind":"merge_spec","target":spec["spec_id"]}
        if status=="merged": return {"kind":"close_spec","target":spec["spec_id"]}
        if status=="blocked": return {"kind":"repair_spec","target":spec["spec_id"]}
    return {"kind":"final_verification","target":run_id}

def main():
    p=argparse.ArgumentParser(); p.add_argument('--db',type=Path,required=True); p.add_argument('--run-id',required=True); a=p.parse_args(); db=ControlDB.read_only(a.db)
    try: print(json.dumps(next_action(db,a.run_id),ensure_ascii=False,sort_keys=True)); return 0
    finally: db.close()
if __name__=='__main__': raise SystemExit(main())
