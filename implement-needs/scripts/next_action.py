"""Compute the single safe next action from the SQLite run state."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from control_db import ControlDB
from dependencies import dependency_status

TERMINAL_SPEC={"closed","cancelled"}

def next_action(db: ControlDB, run_id: str) -> dict:
    pending=db.conn.execute("SELECT kind,target,action_id FROM actions WHERE run_id=? AND status IN ('pending','running') ORDER BY action_id LIMIT 1",(run_id,)).fetchone()
    if pending: return {"kind":pending[0],"target":pending[1],"action_id":pending[2]}
    specs=db.conn.execute("SELECT * FROM specs WHERE run_id=? ORDER BY position",(run_id,)).fetchall()
    for spec in specs:
        if spec["status"] in TERMINAL_SPEC: continue
        blockers=json.loads(spec["blocked_by"])
        for blocker in blockers:
            blocker_row=db.conn.execute("SELECT position FROM specs WHERE spec_id=?",(blocker,)).fetchone()
            if blocker_row is None:
                return {"kind":"repair_dependency","target":spec["spec_id"],"blocker":blocker,"reason":"unknown_blocker"}
            if blocker_row[0] >= spec["position"]:
                return {"kind":"repair_dependency","target":spec["spec_id"],"blocker":blocker,"reason":"forward_dependency"}
            delivery=dependency_status(db,"spec",spec["spec_id"],"spec",blocker)
            if not delivery["satisfied"]:
                return {"kind":"wait_spec_dependency","target":spec["spec_id"],"blocker":blocker,"reason":delivery["code"]}
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
