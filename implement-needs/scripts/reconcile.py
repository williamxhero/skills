"""Audit SQLite control state before another action."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from control_db import ControlDB

def audit(db, run_id):
    errors=[]
    if not db.conn.execute("SELECT 1 FROM runs WHERE run_id=?",(run_id,)).fetchone(): return ["run_not_found"]
    if db.conn.execute("SELECT COUNT(*) FROM specs WHERE run_id=? AND status IN ('implementing','handoff_received','verifying','ready_to_merge')",(run_id,)).fetchone()[0] > 1: errors.append("multiple_active_specs")
    if db.conn.execute("SELECT COUNT(*) FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE s.run_id=? AND t.status='closed' AND (t.commits='[]' OR t.tests='[]')",(run_id,)).fetchone()[0]: errors.append("closed_ticket_missing_evidence")
    if db.conn.execute("SELECT COUNT(*) FROM threads WHERE run_id=? AND lifecycle='created' AND next_action IS NULL",(run_id,)).fetchone()[0]: errors.append("unassigned_created_thread")
    if db.conn.execute("SELECT COUNT(*) FROM actions WHERE run_id=? AND status IN ('pending','running')",(run_id,)).fetchone()[0] > 1: errors.append("multiple_pending_actions")
    return errors

def main():
    p=argparse.ArgumentParser(); p.add_argument('--db',type=Path,required=True); p.add_argument('--run-id',required=True); a=p.parse_args(); db=ControlDB(a.db)
    try:
        errors=audit(db,a.run_id); print(json.dumps({'run_id':a.run_id,'decision':'allow' if not errors else 'repair','errors':errors},ensure_ascii=False)); return 0 if not errors else 1
    finally: db.close()
if __name__=='__main__': raise SystemExit(main())
