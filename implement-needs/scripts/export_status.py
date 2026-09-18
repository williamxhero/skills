"""Export a read-only snapshot from SQLite."""
import argparse
import json
from pathlib import Path

from control_db import ControlDB

p=argparse.ArgumentParser(); p.add_argument('--db',type=Path,required=True); p.add_argument('--run-id',required=True); p.add_argument('--output',type=Path); a=p.parse_args(); db=ControlDB.read_only(a.db)
try:
    raw=json.dumps(db.snapshot(a.run_id),ensure_ascii=False,indent=2,sort_keys=True)+'\n'
    if a.output: a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(raw,encoding='utf-8')
    else: print(raw,end='')
finally: db.close()
