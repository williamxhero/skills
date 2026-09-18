"""Render the next SQLite action as an explicit outer-workflow instruction."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from control_db import ControlDB
from next_action import next_action
from action_contracts import ActionContractError, contract_for

def render(db: ControlDB, run_id: str) -> dict:
    action=next_action(db,run_id); kind=action["kind"]
    contract = contract_for(kind)
    return {"run_id":run_id,"action":action,"instruction":contract.guidance,"action_contract":contract.public(),"receipt_required":True}

def main():
    p=argparse.ArgumentParser(); p.add_argument('--db',type=Path,required=True); p.add_argument('--run-id',required=True); a=p.parse_args(); db=ControlDB.read_only(a.db)
    try: print(json.dumps(render(db,a.run_id),ensure_ascii=False,sort_keys=True)); return 0
    except ActionContractError as exc:
        print(json.dumps({"decision":"reject","error":exc.code,"details":exc.details},ensure_ascii=False,sort_keys=True)); return 1
    finally: db.close()
if __name__=='__main__': raise SystemExit(main())
