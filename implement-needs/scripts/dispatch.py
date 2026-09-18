"""Render the next SQLite action as an explicit outer-workflow instruction."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from control_db import ControlDB
from next_action import next_action

INSTRUCTIONS={
    "run_grill":"Invoke Matt's grilling skill, auto-accept viable recommendations, and record the structured Grill receipt.",
    "publish_spec":"Invoke Matt's to-spec skill for the selected SPEC; record published IDs, acceptance criteria, and Parent readbacks.",
    "ticket_current_spec":"Invoke Matt's to-tickets skill for this SPEC only; record the ticket graph and Parent readbacks.",
    "dispatch_spec":"Invoke Matt's implement-spec skill for this whole SPEC; select a backend, reconcile inventory, register one managed task, and record formal identity and route readbacks before assignment.",
    "wait_spec":"Wait for the current SPEC task, then record its handoff or blocker receipt.",
    "verify_spec":"Independently verify the SPEC handoff, commits, tests, PR, and repository state.",
    "merge_spec":"Merge the verified SPEC PR and record the merge revision.",
    "close_spec":"Close the SPEC tickets only after commit and test evidence is present, then archive its thread.",
    "repair_spec":"Invoke IN: Unblock Development for the exact blocked action and record the repair receipt.",
    "final_release":"Run final tests, package/deploy when configured, then invoke IN: Commit n Push and record all readbacks.",
    "advance_ticket":"Advance the next ticket in the single-ticket line to ready and record the transition.",
    "dispatch_ticket":"Continue the exact controller task for this ticket; no child writer, branch, worktree, or PR task may be created.",
    "wait_ticket":"Wait for the current ticket work in the designated controller task and record its evidence.",
    "wait_ticket_blocker":"Wait for the earlier queue ticket or its SPEC blocker; do not skip ahead in the single-ticket line.",
    "verify_ticket":"Independently verify this ticket's commit and test evidence before merge.",
    "merge_ticket":"Merge the verified ticket change and record the merge revision.",
    "close_ticket":"Close this ticket only after commit and test evidence is persisted, then advance the line.",
    "repair_ticket":"Invoke IN: Unblock Development for this exact ticket action and resume the same controller task.",
    "repair_queue":"Repair the single-ticket-line controller identity or import the exact externally read-back ticket ledger before any implementation action.",
}

def render(db: ControlDB, run_id: str) -> dict:
    action=next_action(db,run_id); kind=action["kind"]
    return {"run_id":run_id,"action":action,"instruction":INSTRUCTIONS.get(kind,"Record the external result and readback for this action."),"receipt_required":True}

def main():
    p=argparse.ArgumentParser(); p.add_argument('--db',type=Path,required=True); p.add_argument('--run-id',required=True); a=p.parse_args(); db=ControlDB(a.db)
    try: print(json.dumps(render(db,a.run_id),ensure_ascii=False,sort_keys=True)); return 0
    finally: db.close()
if __name__=='__main__': raise SystemExit(main())
