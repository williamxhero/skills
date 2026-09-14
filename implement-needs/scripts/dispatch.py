"""Render the next SQLite action as an explicit outer-workflow instruction."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from control_db import ControlDB
from next_action import next_action

INSTRUCTIONS={
    "run_grill":"Invoke Matt's grilling skill, auto-accept viable recommendations, and record the structured Grill receipt.",
    "publish_spec":"Invoke Matt's to-spec skill for the selected SPEC; record published IDs, acceptance criteria, and Parent readbacks.",
    "ticket_current_spec":"Invoke Matt's to-tickets skill for this SPEC only; record the ticket graph and Parent readbacks.",
    "dispatch_spec":"Invoke Matt's implement-spec skill for this whole SPEC; create one implementation task and record its route/readback.",
    "wait_spec":"Wait for the current SPEC task, then record its handoff or blocker receipt.",
    "verify_spec":"Independently verify the SPEC handoff, commits, tests, PR, and repository state.",
    "merge_spec":"Merge the verified SPEC PR and record the merge revision.",
    "close_spec":"Close the SPEC tickets only after commit and test evidence is present, then archive its thread.",
    "repair_spec":"Invoke IN: Unblock Development for the exact blocked action and record the repair receipt.",
    "final_release":"Run final tests, package/deploy when configured, then invoke IN: Commit n Push and record all readbacks.",
}

def render(db: ControlDB, run_id: str) -> dict:
    action=next_action(db,run_id); kind=action["kind"]
    return {"run_id":run_id,"action":action,"instruction":INSTRUCTIONS.get(kind,"Record the external result and readback for this action."),"receipt_required":True}

def main():
    p=argparse.ArgumentParser(); p.add_argument('--db',type=Path,required=True); p.add_argument('--run-id',required=True); a=p.parse_args(); db=ControlDB(a.db)
    try: print(json.dumps(render(db,a.run_id),ensure_ascii=False,sort_keys=True)); return 0
    finally: db.close()
if __name__=='__main__': raise SystemExit(main())
