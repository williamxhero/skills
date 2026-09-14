"""Compute the single safe next action from the SQLite run state."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from control_db import ControlDB

TERMINAL_SPEC={"closed","cancelled"}

def next_action(db: ControlDB, run_id: str) -> dict:
    pending=db.conn.execute("SELECT kind,target,action_id FROM actions WHERE run_id=? AND status IN ('pending','running') ORDER BY action_id LIMIT 1",(run_id,)).fetchone()
    if pending: return {"kind":pending[0],"target":pending[1],"action_id":pending[2]}
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
    return {"kind":"final_release","target":run_id}

def main():
    p=argparse.ArgumentParser(); p.add_argument('--db',type=Path,required=True); p.add_argument('--run-id',required=True); a=p.parse_args(); db=ControlDB(a.db)
    try: print(json.dumps(next_action(db,a.run_id),ensure_ascii=False,sort_keys=True)); return 0
    finally: db.close()
if __name__=='__main__': raise SystemExit(main())
