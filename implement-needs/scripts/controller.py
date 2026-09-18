"""Small, resumable command-line controller for an Implement Needs run."""
from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path

from control_db import ActionConflict, ControlDB, StaleState
from task_backend import (
    MCP_CONNECTOR,
    BackendError,
    JsonLineTransport,
    JsonRpcStdioTransport,
    McpStdioTransport,
    ProtocolError,
    TaskBackend,
    probe_app_server,
)


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,required=True)
    sub=parser.add_subparsers(dest="command",required=True)
    init=sub.add_parser("init"); init.add_argument("--run-id",required=True); init.add_argument("--initiative",required=True); init.add_argument("--requirement",required=True); init.add_argument("--execution-mode",choices=("whole-spec","single-ticket-line"),default="whole-spec"); init.add_argument("--controller-task-id"); init.add_argument("--queue-definition",default="[]")
    spec=sub.add_parser("add-spec"); spec.add_argument("--run-id",required=True); spec.add_argument("--spec-id",required=True); spec.add_argument("--title",required=True); spec.add_argument("--position",type=int,required=True); spec.add_argument("--blocked-by",default="[]"); spec.add_argument("--acceptance",default="[]")
    ticket=sub.add_parser("add-ticket"); ticket.add_argument("--spec-id",required=True); ticket.add_argument("--ticket-id",required=True); ticket.add_argument("--title",required=True); ticket.add_argument("--blocked-by",default="[]"); ticket.add_argument("--issue-url"); ticket.add_argument("--queue-position",type=int)
    thread=sub.add_parser("register-thread"); thread.add_argument("--run-id",required=True); thread.add_argument("--thread-id",required=True); thread.add_argument("--kind",required=True); thread.add_argument("--spec-id"); thread.add_argument("--identity"); thread.add_argument("--client-thread-id"); thread.add_argument("--formal-thread-id"); thread.add_argument("--host-id"); thread.add_argument("--owner-id"); thread.add_argument("--cwd"); thread.add_argument("--project-id"); thread.add_argument("--title-token")
    thread_state=sub.add_parser("thread-state"); thread_state.add_argument("--run-id",required=True); thread_state.add_argument("--thread-id",required=True); thread_state.add_argument("--lifecycle",required=True); thread_state.add_argument("--outcome",default="unknown"); thread_state.add_argument("--next-action"); thread_state.add_argument("--archive-operation"); thread_state.add_argument("--archive-readback")
    spec_state=sub.add_parser("spec-state"); spec_state.add_argument("--spec-id",required=True); spec_state.add_argument("--status",required=True)
    ticket_state=sub.add_parser("ticket-state"); ticket_state.add_argument("--ticket-id",required=True); ticket_state.add_argument("--status",required=True); ticket_state.add_argument("--commits"); ticket_state.add_argument("--tests"); ticket_state.add_argument("--acceptance")
    observation=sub.add_parser("record-observation"); observation.add_argument("--run-id",required=True); observation.add_argument("--entity-type",required=True); observation.add_argument("--entity-id",required=True); observation.add_argument("--operation",required=True); observation.add_argument("--status",required=True); observation.add_argument("--evidence",default="[]")
    action=sub.add_parser("action"); action.add_argument("--run-id",required=True); action.add_argument("--kind",required=True); action.add_argument("--target",required=True)
    finish=sub.add_parser("finish-action"); finish.add_argument("--action-id",type=int,required=True); finish.add_argument("--status",choices=("succeeded","failed","blocked","cancelled"),required=True); finish.add_argument("--result")
    snap=sub.add_parser("snapshot"); snap.add_argument("--run-id",required=True)
    nxt=sub.add_parser("next-action"); nxt.add_argument("--run-id",required=True)
    migrate=sub.add_parser("migrate-run-to-single-ticket-line"); migrate.add_argument("--run-id",required=True); migrate.add_argument("--queue-definition",required=True)
    ledger=sub.add_parser("import-ticket-ledger"); ledger.add_argument("--run-id",required=True); ledger.add_argument("--ledger",type=Path,required=True)
    build_ledger=sub.add_parser("build-ticket-ledger"); build_ledger.add_argument("--readback",type=Path,required=True); build_ledger.add_argument("--history",type=Path,help="historical local delivery evidence JSON"); build_ledger.add_argument("--output",type=Path,required=True)
    reconcile=sub.add_parser("reconcile-backend"); reconcile.add_argument("--run-id",required=True); reconcile.add_argument("--inventory",type=Path); reconcile.add_argument("--backend-command")
    reconcile.add_argument("--backend-protocol",choices=("json-lines", "mcp-stdio", "app-server-stdio", "app-server-bridge"),default="json-lines")
    reconcile.add_argument("--method-map",type=Path)
    reconcile.add_argument("--app-server-metadata", type=Path)
    adopt=sub.add_parser("adopt-controller"); adopt.add_argument("--run-id",required=True); adopt.add_argument("--thread-id",required=True); adopt.add_argument("--identity",type=Path,required=True); adopt.add_argument("--backend-command",required=True); adopt.add_argument("--app-server-metadata",type=Path,required=True)
    # Every direct state-changing controller command carries the version read
    # with its input.  Observation and reconciliation commands deliberately do
    # not use this flag because they write the separate telemetry stream.
    for versioned in (spec, ticket, thread, thread_state, spec_state, ticket_state, action, finish, migrate, ledger, adopt):
        versioned.add_argument("--expected-version", type=int, required=True)
    args=parser.parse_args()
    db=ControlDB(args.db, mode="create" if args.command == "init" else ("read-only" if args.command == "snapshot" else "open-existing"))
    try:
        if args.command=="init": db.create_run(args.run_id,args.initiative,args.requirement,args.execution_mode,args.controller_task_id,json.loads(args.queue_definition)); result={"run_id":args.run_id,"status":"active","execution_mode":args.execution_mode}
        elif args.command=="add-spec": db.add_spec(args.run_id,args.spec_id,args.title,args.position,json.loads(args.blocked_by),json.loads(args.acceptance),args.expected_version); result={"spec_id":args.spec_id}
        elif args.command=="add-ticket": db.add_ticket(args.spec_id,args.ticket_id,args.title,json.loads(args.blocked_by),args.issue_url,args.queue_position,args.expected_version); result={"ticket_id":args.ticket_id}
        elif args.command=="register-thread":
            from task_identity import TaskIdentity
            identity=TaskIdentity(**json.loads(args.identity)) if args.identity else None
            inserted=db.add_thread(args.run_id,args.thread_id,args.kind,args.spec_id,identity=identity,client_thread_id=args.client_thread_id,host_id=args.host_id,owner_id=args.owner_id,cwd=args.cwd,project_id=args.project_id,title_token=args.title_token,formal_thread_id=args.formal_thread_id,expected_version=args.expected_version)
            result={"thread_id":args.thread_id,"lifecycle":"created","inserted":inserted}
        elif args.command=="thread-state": db.update_thread(args.run_id,args.thread_id,args.lifecycle,args.outcome,args.next_action,args.archive_operation,args.archive_readback,args.expected_version); result={"thread_id":args.thread_id,"lifecycle":args.lifecycle}
        elif args.command=="spec-state": db.update_spec(args.spec_id,args.status,args.expected_version); result={"spec_id":args.spec_id,"status":args.status}
        elif args.command=="ticket-state": db.update_ticket(args.ticket_id,args.status,json.loads(args.commits) if args.commits else None,json.loads(args.tests) if args.tests else None,json.loads(args.acceptance) if args.acceptance else None,args.expected_version); result={"ticket_id":args.ticket_id,"status":args.status}
        elif args.command=="record-observation": db.add_observation(args.run_id,args.entity_type,args.entity_id,{"operation":args.operation,"status":args.status,"evidence":json.loads(args.evidence)}); result={"entity_id":args.entity_id,"status":args.status}
        elif args.command=="action": result={"action_id":db.set_action(args.run_id,args.kind,args.target,expected_version=args.expected_version)}
        elif args.command=="finish-action": db.finish_action(args.action_id,args.status,json.loads(args.result) if args.result else None,expected_version=args.expected_version); result={"action_id":args.action_id,"status":args.status}
        elif args.command=="next-action":
            from next_action import next_action
            result=next_action(db,args.run_id)
        elif args.command=="migrate-run-to-single-ticket-line":
            result=db.migrate_run_to_single_ticket_line(args.run_id,json.loads(args.queue_definition),args.expected_version)
        elif args.command=="import-ticket-ledger":
            from ticket_ledger import build_ticket_ledger
            ledger_payload=json.loads(args.ledger.read_text(encoding="utf-8"))
            if "root_issue" in ledger_payload:
                ledger_payload=build_ticket_ledger(ledger_payload)
            result=db.import_ticket_ledger(args.run_id,ledger_payload,args.expected_version)
        elif args.command=="build-ticket-ledger":
            from ticket_ledger import build_ticket_ledger
            readback=json.loads(args.readback.read_text(encoding="utf-8"))
            if args.history:
                history=json.loads(args.history.read_text(encoding="utf-8"))
                if not isinstance(history, dict):
                    raise ValueError("historical delivery evidence must be an object")
                readback["delivery_records"] = history.get("delivery_records", history.get("tickets", []))
            ledger_value=build_ticket_ledger(readback)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(ledger_value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            result={"output":str(args.output),"ticket_count":len(ledger_value["tickets"])}
        elif args.command=="reconcile-backend":
            from reconcile import (
                probe_and_record,
                reconcile_backend,
                reconcile_inventory,
            )
            if getattr(args, "backend_command", None):
                transport=None
                try:
                    if args.backend_protocol == "app-server-bridge":
                        if args.app_server_metadata is None:
                            raise ProtocolError("app-server-bridge requires --app-server-metadata")
                        from app_server_bridge import AppServerBridge
                        raw_transport = AppServerBridge(
                            shlex.split(args.backend_command, posix=False),
                            args.app_server_metadata,
                        )
                        transport = TaskBackend(MCP_CONNECTOR, raw_transport)
                        capability = probe_and_record(db, args.run_id, raw_transport)
                    elif args.backend_protocol == "app-server-stdio":
                        if args.method_map is None:
                            raise ProtocolError("app-server-stdio requires --method-map")
                        method_map=json.loads(args.method_map.read_text(encoding="utf-8"))
                        raw_transport=JsonRpcStdioTransport(args.backend_command)
                        capability=probe_app_server(raw_transport, method_map)
                        transport=TaskBackend("codex-app-server-jsonrpc", raw_transport, capability["method_map"])
                        db.add_observation(
                            args.run_id, "backend", "task-backend",
                            {"operation": "capability_probe", "status": "succeeded", "evidence": capability},
                        )
                    else:
                        raw_transport=(McpStdioTransport(args.backend_command)
                                       if args.backend_protocol == "mcp-stdio"
                                       else JsonLineTransport(args.backend_command))
                        transport=TaskBackend(MCP_CONNECTOR, raw_transport)
                        capability=probe_and_record(db, args.run_id, transport.transport)
                    result=reconcile_backend(db,args.run_id,transport)
                    result["capability_evidence"] = capability["capability_evidence"]
                except (BackendError, ProtocolError, OSError, ValueError) as exc:
                    result={"decision":"repair","status":"inconclusive","errors":["capability_handshake_failed:" + str(exc)],"matches":[]}
                finally:
                    if transport is not None:
                        transport.transport.close()
            else:
                if args.inventory is None:
                    inventory={"reconciliation_status":"inconclusive"}
                else:
                    try: inventory=json.loads(args.inventory.read_text(encoding="utf-8"))
                    except (OSError,UnicodeError,json.JSONDecodeError): inventory={"reconciliation_status":"inconclusive"}
                result=reconcile_inventory(db,args.run_id,inventory)
        elif args.command=="adopt-controller":
            from app_server_bridge import AppServerBridge
            from reconcile import adopt_controller, probe_and_record
            identity=json.loads(args.identity.read_text(encoding="utf-8"))
            if not isinstance(identity, dict):
                raise ValueError("controller identity must be an object")
            required=("formal_thread_id", "host_id", "task_id", "run_id", "attempt_id", "owner_id", "cwd", "project_id", "identity_evidence")
            if any(not isinstance(identity.get(field), str) or not identity[field] for field in required):
                raise ValueError("controller identity enrollment requires all identity fields")
            if identity["run_id"] != args.run_id:
                raise ValueError("controller identity run_id does not match --run-id")
            bridge=AppServerBridge(shlex.split(args.backend_command, posix=False), args.app_server_metadata)
            transport=TaskBackend(MCP_CONNECTOR, bridge)
            try:
                # Enrollment is the only operation allowed to create sidecar
                # identity for an existing thread. Probe immediately afterward.
                bridge.request("adopt_thread", identity)
                capability=probe_and_record(db, args.run_id, bridge)
                result=adopt_controller(
                    db, args.run_id, transport, thread_id=args.thread_id,
                    **{field: identity[field] for field in required}, expected_version=args.expected_version,
                )
                result["capability_evidence"]=capability["capability_evidence"]
            finally:
                bridge.close()
        else: result=db.snapshot(args.run_id)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 0 if result.get("decision") in (None,"allow") else 1
    except ActionConflict as exc:
        result={"decision":"repair","error":"multiple_pending_actions","detail":str(exc)}
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 1
    except StaleState as exc:
        result={"decision":"refresh","error":exc.code,"run_id":exc.run_id,"expected_version":exc.expected,"actual_version":exc.actual}
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 1
    finally: db.close()

if __name__ == "__main__": raise SystemExit(main())
