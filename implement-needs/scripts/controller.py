"""Small, resumable command-line controller for an Implement Needs run."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from control_db import ControlDB

def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,required=True)
    sub=parser.add_subparsers(dest="command",required=True)
    init=sub.add_parser("init"); init.add_argument("--run-id",required=True); init.add_argument("--initiative",required=True); init.add_argument("--requirement",required=True)
    spec=sub.add_parser("add-spec"); spec.add_argument("--run-id",required=True); spec.add_argument("--spec-id",required=True); spec.add_argument("--title",required=True); spec.add_argument("--position",type=int,required=True); spec.add_argument("--blocked-by",default="[]"); spec.add_argument("--acceptance",default="[]")
    ticket=sub.add_parser("add-ticket"); ticket.add_argument("--spec-id",required=True); ticket.add_argument("--ticket-id",required=True); ticket.add_argument("--title",required=True); ticket.add_argument("--blocked-by",default="[]"); ticket.add_argument("--issue-url")
    thread=sub.add_parser("register-thread"); thread.add_argument("--run-id",required=True); thread.add_argument("--thread-id",required=True); thread.add_argument("--kind",required=True); thread.add_argument("--spec-id")
    thread_state=sub.add_parser("thread-state"); thread_state.add_argument("--run-id",required=True); thread_state.add_argument("--thread-id",required=True); thread_state.add_argument("--lifecycle",required=True); thread_state.add_argument("--outcome",default="unknown"); thread_state.add_argument("--next-action"); thread_state.add_argument("--archive-operation"); thread_state.add_argument("--archive-readback")
    spec_state=sub.add_parser("spec-state"); spec_state.add_argument("--spec-id",required=True); spec_state.add_argument("--status",required=True)
    ticket_state=sub.add_parser("ticket-state"); ticket_state.add_argument("--ticket-id",required=True); ticket_state.add_argument("--status",required=True); ticket_state.add_argument("--commits"); ticket_state.add_argument("--tests")
    observation=sub.add_parser("record-observation"); observation.add_argument("--run-id",required=True); observation.add_argument("--entity-type",required=True); observation.add_argument("--entity-id",required=True); observation.add_argument("--operation",required=True); observation.add_argument("--status",required=True); observation.add_argument("--evidence",default="[]"); observation.add_argument("--observation-key"); observation.add_argument("--phase"); observation.add_argument("--scope",default="run"); observation.add_argument("--unit",default="count"); observation.add_argument("--started-at"); observation.add_argument("--ended-at"); observation.add_argument("--duration-ms",type=float); observation.add_argument("--source",default="controller"); observation.add_argument("--usage",default="{}"); observation.add_argument("--metadata",default="{}")
    action=sub.add_parser("action"); action.add_argument("--run-id",required=True); action.add_argument("--kind",required=True); action.add_argument("--target",required=True)
    finish=sub.add_parser("finish-action"); finish.add_argument("--action-id",type=int,required=True); finish.add_argument("--status",choices=("succeeded","failed","blocked","cancelled"),required=True); finish.add_argument("--result")
    snap=sub.add_parser("snapshot"); snap.add_argument("--run-id",required=True)
    metrics=sub.add_parser("metrics"); metrics.add_argument("--run-id",required=True); metrics.add_argument("--baseline-version",default="runtime-observations-v1")
    nxt=sub.add_parser("next-action"); nxt.add_argument("--run-id",required=True)
    args=parser.parse_args(); db=ControlDB(args.db)
    try:
        if args.command=="init": db.create_run(args.run_id,args.initiative,args.requirement); result={"run_id":args.run_id,"status":"active"}
        elif args.command=="add-spec": db.add_spec(args.run_id,args.spec_id,args.title,args.position,json.loads(args.blocked_by),json.loads(args.acceptance)); result={"spec_id":args.spec_id}
        elif args.command=="add-ticket": db.add_ticket(args.spec_id,args.ticket_id,args.title,json.loads(args.blocked_by),args.issue_url); result={"ticket_id":args.ticket_id}
        elif args.command=="register-thread": db.add_thread(args.run_id,args.thread_id,args.kind,args.spec_id); result={"thread_id":args.thread_id,"lifecycle":"created"}
        elif args.command=="thread-state": db.update_thread(args.run_id,args.thread_id,args.lifecycle,args.outcome,args.next_action,args.archive_operation,args.archive_readback); result={"thread_id":args.thread_id,"lifecycle":args.lifecycle}
        elif args.command=="spec-state": db.update_spec(args.spec_id,args.status); result={"spec_id":args.spec_id,"status":args.status}
        elif args.command=="ticket-state": db.update_ticket(args.ticket_id,args.status,json.loads(args.commits) if args.commits else None,json.loads(args.tests) if args.tests else None); result={"ticket_id":args.ticket_id,"status":args.status}
        elif args.command=="record-observation":
            if args.observation_key or args.phase:
                metadata=json.loads(args.metadata)
                if args.evidence != "[]":
                    metadata["evidence"]=json.loads(args.evidence)
                metadata.setdefault("operation",args.operation)
                result=db.record_runtime_observation(args.run_id,args.observation_key or f"{args.run_id}:{args.entity_type}:{args.entity_id}:{args.operation}",args.entity_type,args.entity_id,args.phase or args.operation,args.status,args.scope,args.unit,args.started_at,args.ended_at,args.duration_ms,args.source,json.loads(args.usage),metadata)
            else:
                db.add_observation(args.run_id,args.entity_type,args.entity_id,{"operation":args.operation,"status":args.status,"evidence":json.loads(args.evidence)}); result={"entity_id":args.entity_id,"status":args.status}
        elif args.command=="action": result={"action_id":db.set_action(args.run_id,args.kind,args.target)}
        elif args.command=="finish-action": db.finish_action(args.action_id,args.status,json.loads(args.result) if args.result else None); result={"action_id":args.action_id,"status":args.status}
        elif args.command=="next-action":
            from next_action import next_action
            result=next_action(db,args.run_id)
        elif args.command=="metrics":
            from runtime_metrics import build_metrics
            result=build_metrics(db,args.run_id,args.baseline_version)
        else: result=db.snapshot(args.run_id)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 0
    finally: db.close()

if __name__ == "__main__": raise SystemExit(main())
