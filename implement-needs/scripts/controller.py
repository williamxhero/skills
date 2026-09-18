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
    observation=sub.add_parser("record-observation"); observation.add_argument("--run-id",required=True); observation.add_argument("--entity-type",required=True); observation.add_argument("--entity-id",required=True); observation.add_argument("--operation",required=True); observation.add_argument("--status",required=True); observation.add_argument("--evidence",default="[]"); observation.add_argument("--observation-key"); observation.add_argument("--phase"); observation.add_argument("--scope",default="run"); observation.add_argument("--unit"); observation.add_argument("--started-at"); observation.add_argument("--ended-at"); observation.add_argument("--duration-ms",type=float); observation.add_argument("--source",default="controller"); observation.add_argument("--usage",default="{}"); observation.add_argument("--metadata",default="{}")
    action=sub.add_parser("action"); action.add_argument("--run-id",required=True); action.add_argument("--kind",required=True); action.add_argument("--target",required=True)
    finish=sub.add_parser("finish-action"); finish.add_argument("--action-id",type=int,required=True); finish.add_argument("--status",choices=("succeeded","failed","blocked","cancelled"),required=True); finish.add_argument("--result")
    intent=sub.add_parser("operation-intent"); intent.add_argument("--run-id",required=True); intent.add_argument("--operation",required=True); intent.add_argument("--target",required=True); intent.add_argument("--parameters",default="{}"); intent.add_argument("--generation",type=int,default=0); intent.add_argument("--idempotency-key")
    start_intent=sub.add_parser("start-operation-intent"); start_intent.add_argument("--intent-id",type=int,required=True); start_intent.add_argument("--executor-id",required=True)
    unknown_intent=sub.add_parser("operation-outcome-unknown"); unknown_intent.add_argument("--intent-id",type=int,required=True); unknown_intent.add_argument("--reason",required=True); unknown_intent.add_argument("--evidence",required=True)
    reconcile_intent=sub.add_parser("reconcile-operation-intent"); reconcile_intent.add_argument("--intent-id",type=int,required=True); reconcile_intent.add_argument("--outcome",choices=("not_found","succeeded","failed"),required=True); reconcile_intent.add_argument("--evidence",required=True); reconcile_intent.add_argument("--result",default="{}")
    claim=sub.add_parser("claim-action"); claim.add_argument("--action-id",type=int,required=True); claim.add_argument("--owner-id",required=True); claim.add_argument("--lease-seconds",type=int,default=60); claim.add_argument("--supports-fencing",action="store_true"); claim.add_argument("--outcome-reconciled",action="store_true")
    effect=sub.add_parser("assert-action-effect"); effect.add_argument("--action-id",type=int,required=True); effect.add_argument("--owner-id",required=True)
    resource=sub.add_parser("claim-resource"); resource.add_argument("--coordination-db",type=Path,required=True); resource.add_argument("--resource-key",required=True); resource.add_argument("--owner-id",required=True); resource.add_argument("--run-id",required=True); resource.add_argument("--lease-seconds",type=int,default=60); resource.add_argument("--supports-fencing",action="store_true"); resource.add_argument("--outcome-reconciled",action="store_true")
    release_resource=sub.add_parser("release-resource"); release_resource.add_argument("--coordination-db",type=Path,required=True); release_resource.add_argument("--resource-key",required=True); release_resource.add_argument("--owner-id",required=True)
    proof=sub.add_parser("record-delivery-proof"); proof.add_argument("--entity-type",choices=("spec","ticket"),required=True); proof.add_argument("--entity-id",required=True); proof.add_argument("--artifact-type",default="delivery"); proof.add_argument("--artifact-ref",required=True); proof.add_argument("--evidence",required=True)
    waiver=sub.add_parser("waive-dependency"); waiver.add_argument("--dependent-type",choices=("spec","ticket"),required=True); waiver.add_argument("--dependent-id",required=True); waiver.add_argument("--blocker-type",choices=("spec","ticket"),required=True); waiver.add_argument("--blocker-id",required=True); waiver.add_argument("--reason",required=True); waiver.add_argument("--authorization-source",required=True); waiver.add_argument("--scope",required=True); waiver.add_argument("--evidence",required=True)
    dependencies=sub.add_parser("validate-dependencies"); dependencies.add_argument("--run-id",required=True)
    failure=sub.add_parser("record-recovery-failure"); failure.add_argument("--run-id",required=True); failure.add_argument("--action-id",type=int,required=True); failure.add_argument("--category",required=True); failure.add_argument("--error-fingerprint",required=True); failure.add_argument("--code-digest",required=True); failure.add_argument("--environment-digest",required=True); failure.add_argument("--strategy-digest",required=True); failure.add_argument("--progress-marker",required=True); failure.add_argument("--retry-owner",required=True); failure.add_argument("--evidence",required=True); failure.add_argument("--max-attempts",type=int,default=3); failure.add_argument("--budget-seconds",type=int,default=3600); failure.add_argument("--budget-version",default="recovery-v1")
    resume=sub.add_parser("resume-recovery"); resume.add_argument("--recovery-id",type=int,required=True); resume.add_argument("--old-attempt-archived",action="store_true"); resume.add_argument("--strategy-digest",required=True); resume.add_argument("--progress-marker",required=True); resume.add_argument("--evidence",required=True)
    snap=sub.add_parser("snapshot"); snap.add_argument("--run-id",required=True)
    save_snap=sub.add_parser("save-snapshot"); save_snap.add_argument("--run-id",required=True); save_snap.add_argument("--payload",required=True); save_snap.add_argument("--expected-event-cursor",type=int); save_snap.add_argument("--expected-state-version",type=int)
    context=sub.add_parser("context"); context.add_argument("--run-id",required=True); context.add_argument("--phase",required=True); context.add_argument("--refresh",action="store_true")
    exception=sub.add_parser("record-exception"); exception.add_argument("--run-id",required=True); exception.add_argument("--fingerprint",required=True); exception.add_argument("--category",required=True); exception.add_argument("--summary",required=True); exception.add_argument("--log-uri"); exception.add_argument("--details",default="{}")
    resolve_exception=sub.add_parser("resolve-exception"); resolve_exception.add_argument("--run-id",required=True); resolve_exception.add_argument("--fingerprint",required=True); resolve_exception.add_argument("--evidence",required=True)
    wait=sub.add_parser("record-external-wait"); wait.add_argument("--run-id",required=True); wait.add_argument("--external-request-id",required=True); wait.add_argument("--event-cursor",type=int,required=True); wait.add_argument("--wake-condition",required=True); wait.add_argument("--next-safe-check-at",required=True); wait.add_argument("--action-id",type=int)
    poll=sub.add_parser("poll-external"); poll.add_argument("--external-request-id",required=True); poll.add_argument("--result",required=True); poll.add_argument("--changed",action="store_true"); poll.add_argument("--evidence",default="[]")
    tool_success=sub.add_parser("tool-success"); tool_success.add_argument("--result",required=True); tool_success.add_argument("--identifiers",required=True); tool_success.add_argument("--version",type=int,required=True); tool_success.add_argument("--evidence-uri",required=True)
    tool_failure=sub.add_parser("tool-failure"); tool_failure.add_argument("--category",required=True); tool_failure.add_argument("--error-fragment",required=True); tool_failure.add_argument("--log-uri",required=True); tool_failure.add_argument("--version",type=int)
    tool_wait=sub.add_parser("tool-waiting-external"); tool_wait.add_argument("--external-request-id",required=True); tool_wait.add_argument("--event-cursor",type=int,required=True); tool_wait.add_argument("--wake-condition",required=True); tool_wait.add_argument("--next-safe-check-at",required=True)
    host=sub.add_parser("host-operation"); host.add_argument("--operation",required=True); host.add_argument("--arguments",default="{}"); host.add_argument("--required-capability",required=True); host.add_argument("--capabilities",default="[]"); host.add_argument("--capability-evidence",default="[]")
    terminal=sub.add_parser("record-terminal-validation"); terminal.add_argument("--run-id",required=True); terminal.add_argument("--decision",required=True); terminal.add_argument("--evidence",required=True)
    receipt=sub.add_parser("record-delivery-receipt"); receipt.add_argument("--run-id",required=True); receipt.add_argument("--entity-type",choices=("spec","ticket","run"),required=True); receipt.add_argument("--entity-id",required=True); receipt.add_argument("--receipt",required=True)
    receipt_check=sub.add_parser("validate-delivery-receipt"); receipt_check.add_argument("--run-id",required=True); receipt_check.add_argument("--entity-type",choices=("spec","ticket","run"),required=True); receipt_check.add_argument("--entity-id",required=True); receipt_check.add_argument("--expected",required=True)
    metrics=sub.add_parser("metrics"); metrics.add_argument("--run-id",required=True); metrics.add_argument("--baseline-version",default="runtime-observations-v1")
    nxt=sub.add_parser("next-action"); nxt.add_argument("--run-id",required=True)
    advance=sub.add_parser("advance"); advance.add_argument("--run-id",required=True); advance.add_argument("--max-actions",type=int,default=32)
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
                result=db.record_runtime_observation(args.run_id,args.observation_key or f"{args.run_id}:{args.entity_type}:{args.entity_id}:{args.operation}",args.entity_type,args.entity_id,args.phase or args.operation,args.status,args.scope,args.unit or ("milliseconds" if args.duration_ms is not None else "count"),args.started_at,args.ended_at,args.duration_ms,args.source,json.loads(args.usage),metadata)
            else:
                db.add_observation(args.run_id,args.entity_type,args.entity_id,{"operation":args.operation,"status":args.status,"evidence":json.loads(args.evidence)}); result={"entity_id":args.entity_id,"status":args.status}
        elif args.command=="action": result={"action_id":db.set_action(args.run_id,args.kind,args.target)}
        elif args.command=="finish-action": db.finish_action(args.action_id,args.status,json.loads(args.result) if args.result else None); result={"action_id":args.action_id,"status":args.status}
        elif args.command=="operation-intent": result=db.create_operation_intent(args.run_id,args.operation,args.target,json.loads(args.parameters),args.generation,args.idempotency_key)
        elif args.command=="start-operation-intent": result=db.start_operation_intent(args.intent_id,args.executor_id)
        elif args.command=="operation-outcome-unknown": db.mark_operation_unknown(args.intent_id,args.reason,json.loads(args.evidence)); result={"intent_id":args.intent_id,"status":"outcome_unknown"}
        elif args.command=="reconcile-operation-intent": result=db.reconcile_operation_intent(args.intent_id,args.outcome,json.loads(args.evidence),json.loads(args.result))
        elif args.command=="claim-action": result=db.claim_action(args.action_id,args.owner_id,args.lease_seconds,args.supports_fencing,args.outcome_reconciled)
        elif args.command=="assert-action-effect": result=db.assert_action_effect_permitted(args.action_id,args.owner_id)
        elif args.command=="claim-resource":
            from resource_coordinator import ResourceCoordinator
            coordinator=ResourceCoordinator(args.coordination_db)
            try: result=coordinator.acquire(args.resource_key,args.owner_id,args.run_id,args.lease_seconds,args.supports_fencing,args.outcome_reconciled)
            finally: coordinator.close()
        elif args.command=="release-resource":
            from resource_coordinator import ResourceCoordinator
            coordinator=ResourceCoordinator(args.coordination_db)
            try: result=coordinator.release(args.resource_key,args.owner_id)
            finally: coordinator.close()
        elif args.command=="record-delivery-proof": result=db.record_delivery_proof(args.entity_type,args.entity_id,args.artifact_type,args.artifact_ref,json.loads(args.evidence))
        elif args.command=="waive-dependency": result=db.waive_dependency(args.dependent_type,args.dependent_id,args.blocker_type,args.blocker_id,args.reason,args.authorization_source,args.scope,json.loads(args.evidence))
        elif args.command=="validate-dependencies":
            from dependencies import validation_errors
            errors=validation_errors(db,args.run_id); result={"run_id":args.run_id,"decision":"allow" if not errors else "repair","errors":errors}
        elif args.command=="record-recovery-failure":
            from recovery import record_failure
            result=record_failure(db,args.run_id,args.action_id,args.category,args.error_fingerprint,args.code_digest,args.environment_digest,args.strategy_digest,args.progress_marker,args.retry_owner,json.loads(args.evidence),args.max_attempts,args.budget_seconds,args.budget_version)
        elif args.command=="resume-recovery":
            from recovery import resume_recovery
            result=resume_recovery(db,args.recovery_id,args.old_attempt_archived,args.strategy_digest,args.progress_marker,json.loads(args.evidence))
        elif args.command=="next-action":
            from next_action import next_action
            result=next_action(db,args.run_id)
        elif args.command=="metrics":
            from runtime_metrics import build_metrics
            result=build_metrics(db,args.run_id,args.baseline_version)
        elif args.command=="advance":
            from advance_runtime import advance
            result=advance(db,args.run_id,args.max_actions)
        elif args.command=="save-snapshot":
            result=db.save_snapshot(args.run_id,json.loads(args.payload),args.expected_event_cursor,args.expected_state_version)
        elif args.command=="context":
            from phase_context import assemble_context, refresh_context
            result=refresh_context(db,args.run_id,args.phase) if args.refresh else assemble_context(db,args.run_id,args.phase)
        elif args.command=="record-exception":
            result=db.record_exception(args.run_id,args.fingerprint,args.category,args.summary,args.log_uri,json.loads(args.details))
        elif args.command=="resolve-exception":
            result=db.resolve_exception(args.run_id,args.fingerprint,json.loads(args.evidence))
        elif args.command=="record-external-wait":
            result=db.record_external_wait(args.external_request_id,args.run_id,args.event_cursor,args.wake_condition,args.next_safe_check_at,args.action_id)
        elif args.command=="poll-external":
            result=db.poll_external_wait(args.external_request_id,json.loads(args.result),args.changed,json.loads(args.evidence))
        elif args.command=="tool-success":
            from tool_envelopes import success
            result=success(json.loads(args.result),json.loads(args.identifiers),args.version,args.evidence_uri)
        elif args.command=="tool-failure":
            from tool_envelopes import failure
            result=failure(args.category,args.error_fragment,args.log_uri,args.version)
        elif args.command=="tool-waiting-external":
            from tool_envelopes import waiting_external
            result=waiting_external(args.external_request_id,args.event_cursor,args.wake_condition,args.next_safe_check_at)
        elif args.command=="host-operation":
            from tool_envelopes import host_operation
            result=host_operation(args.operation,json.loads(args.arguments),args.required_capability,json.loads(args.capabilities),json.loads(args.capability_evidence))
        elif args.command=="record-terminal-validation":
            result=db.record_terminal_validation(args.run_id,args.decision,json.loads(args.evidence))
        elif args.command=="record-delivery-receipt":
            from delivery_receipts import record
            result=record(db,args.run_id,args.entity_type,args.entity_id,json.loads(args.receipt))
        elif args.command=="validate-delivery-receipt":
            from delivery_receipts import project
            result=project(db,args.run_id,args.entity_type,args.entity_id,json.loads(args.expected))
        else: result=db.snapshot(args.run_id)
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 0
    finally: db.close()

if __name__ == "__main__": raise SystemExit(main())
