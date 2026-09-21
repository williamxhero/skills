"""Small, resumable command-line controller for an Implement Needs run."""
from __future__ import annotations

import argparse
import json
import shlex
import time
from pathlib import Path

from control_db import ActionConflict, ControlDB, DecisionError, PolicyError, RestoreValidationError, StaleState
from control_db import ActionClaimConflict, UnsafeLeaseTakeover
from authorization import AuthorizationError
from evidence_gate import EvidenceGateError
from run_state import PHASES, RUN_RESULTS, RunStateError
from sync_scope import SyncScopeError, build_sync_plan
from startup_contract import StartupContractError
from context_projection import ContextProjectionError, build_context, measure_context, read_history
from action_contracts import ActionContractError, render_cli_help, validate_action_surface
from safety_metrics import collect_metrics, benchmark_manifest, compare_metrics
from dependency_readiness import readiness_from_db, structure_from_db
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
    init=sub.add_parser("init"); init.add_argument("--run-id",required=True); init.add_argument("--initiative",required=True); init.add_argument("--requirement",required=True); init.add_argument("--execution-mode",choices=("whole-spec","single-ticket-line"),default="whole-spec"); init.add_argument("--controller-task-id"); init.add_argument("--queue-definition",default="[]"); init.add_argument("--authorization")
    spec=sub.add_parser("add-spec"); spec.add_argument("--run-id",required=True); spec.add_argument("--spec-id",required=True); spec.add_argument("--title",required=True); spec.add_argument("--position",type=int,required=True); spec.add_argument("--blocked-by",default="[]"); spec.add_argument("--acceptance",default="[]"); spec.add_argument("--expected-version",type=int)
    ticket=sub.add_parser("add-ticket"); ticket.add_argument("--spec-id",required=True); ticket.add_argument("--ticket-id",required=True); ticket.add_argument("--title",required=True); ticket.add_argument("--blocked-by",default="[]"); ticket.add_argument("--issue-url"); ticket.add_argument("--queue-position",type=int); ticket.add_argument("--expected-version",type=int)
    reconcile_ticket=sub.add_parser("reconcile-ticket-dependencies"); reconcile_ticket.add_argument("--ticket-id",required=True); reconcile_ticket.add_argument("--blocked-by",required=True); reconcile_ticket.add_argument("--evidence",default="[]")
    thread=sub.add_parser("register-thread"); thread.add_argument("--run-id",required=True); thread.add_argument("--thread-id",required=True); thread.add_argument("--kind",required=True); thread.add_argument("--spec-id"); thread.add_argument("--identity"); thread.add_argument("--client-thread-id"); thread.add_argument("--formal-thread-id"); thread.add_argument("--host-id"); thread.add_argument("--owner-id"); thread.add_argument("--cwd"); thread.add_argument("--project-id"); thread.add_argument("--title-token")
    thread_state=sub.add_parser("thread-state"); thread_state.add_argument("--run-id",required=True); thread_state.add_argument("--thread-id",required=True); thread_state.add_argument("--lifecycle",required=True); thread_state.add_argument("--outcome",default="unknown"); thread_state.add_argument("--next-action"); thread_state.add_argument("--archive-operation"); thread_state.add_argument("--archive-readback"); thread_state.add_argument("--gate")
    spec_state=sub.add_parser("spec-state"); spec_state.add_argument("--spec-id",required=True); spec_state.add_argument("--status",required=True); spec_state.add_argument("--gate"); spec_state.add_argument("--context")
    ticket_state=sub.add_parser("ticket-state"); ticket_state.add_argument("--ticket-id",required=True); ticket_state.add_argument("--status",required=True); ticket_state.add_argument("--commits"); ticket_state.add_argument("--tests"); ticket_state.add_argument("--acceptance"); ticket_state.add_argument("--gate"); ticket_state.add_argument("--context")
    observation=sub.add_parser("record-observation"); observation.add_argument("--run-id",required=True); observation.add_argument("--entity-type",required=True); observation.add_argument("--entity-id",required=True); observation.add_argument("--operation",required=True); observation.add_argument("--status",required=True); observation.add_argument("--evidence",default="[]"); observation.add_argument("--observation-key"); observation.add_argument("--phase"); observation.add_argument("--scope",default="run"); observation.add_argument("--unit",default="count"); observation.add_argument("--started-at"); observation.add_argument("--ended-at"); observation.add_argument("--duration-ms",type=float); observation.add_argument("--source",default="controller"); observation.add_argument("--usage",default="{}"); observation.add_argument("--metadata",default="{}")
    tool_success=sub.add_parser("tool-success"); tool_success.add_argument("--result",required=True); tool_success.add_argument("--identifiers",required=True); tool_success.add_argument("--version",type=int,required=True); tool_success.add_argument("--evidence-uri",required=True)
    tool_failure=sub.add_parser("tool-failure"); tool_failure.add_argument("--category",required=True); tool_failure.add_argument("--error-fragment",required=True); tool_failure.add_argument("--log-uri",required=True); tool_failure.add_argument("--version",type=int)
    host_operation=sub.add_parser("host-operation"); host_operation.add_argument("--operation",required=True); host_operation.add_argument("--arguments",default="{}"); host_operation.add_argument("--required-capability",required=True); host_operation.add_argument("--capabilities",default="[]"); host_operation.add_argument("--capability-evidence",default="[]")
    action=sub.add_parser("action"); action.add_argument("--run-id",required=True); action.add_argument("--kind",required=True); action.add_argument("--target",required=True)
    complete_child=sub.add_parser("complete-child"); complete_child.add_argument("--run-id",required=True); complete_child.add_argument("--child-kind",choices=("spec", "ticket"),required=True); complete_child.add_argument("--child-id",required=True); complete_child.add_argument("--next-action",required=True); complete_child.add_argument("--result",default="{}")
    finish=sub.add_parser("finish-action"); finish.add_argument("--action-id",type=int,required=True); finish.add_argument("--status",choices=("succeeded","failed","blocked","cancelled"),required=True); finish.add_argument("--result"); finish.add_argument("--gate")
    snap=sub.add_parser("snapshot"); snap.add_argument("--run-id",required=True)
    save_snap=sub.add_parser("save-snapshot"); save_snap.add_argument("--run-id",required=True); save_snap.add_argument("--payload",required=True); save_snap.add_argument("--expected-event-cursor",type=int); save_snap.add_argument("--expected-state-version",type=int)
    nxt=sub.add_parser("next-action"); nxt.add_argument("--run-id",required=True)
    run_phase=sub.add_parser("run-phase"); run_phase.add_argument("--run-id",required=True); run_phase.add_argument("--phase",choices=PHASES[1:],required=True); run_phase.add_argument("--receipt",required=True)
    run_result=sub.add_parser("run-result"); run_result.add_argument("--run-id",required=True); run_result.add_argument("--result",choices=tuple(RUN_RESULTS - {"completed"}),required=True); run_result.add_argument("--reason",required=True); run_result.add_argument("--receipt",required=True)
    resume=sub.add_parser("resume-run"); resume.add_argument("--run-id",required=True)
    configure_auth=sub.add_parser("configure-auth"); configure_auth.add_argument("--run-id",required=True); configure_auth.add_argument("--authorization",required=True)
    auth_check=sub.add_parser("auth-check"); auth_check.add_argument("--run-id",required=True); auth_check.add_argument("--action",required=True); auth_check.add_argument("--path"); auth_check.add_argument("--target-ref"); auth_check.add_argument("--environment"); auth_check.add_argument("--full-project",action="store_true")
    freeze=sub.add_parser("freeze-candidate"); freeze.add_argument("--run-id",required=True); freeze.add_argument("--candidate-sha",required=True); freeze.add_argument("--merge-sha"); freeze.add_argument("--evidence",required=True)
    candidate_evidence=sub.add_parser("candidate-evidence"); candidate_evidence.add_argument("--run-id",required=True); candidate_evidence.add_argument("--kind",choices=("test","package","deployment","synchronization","final-readback"),required=True); candidate_evidence.add_argument("--evidence",required=True)
    invalidate=sub.add_parser("invalidate-candidate"); invalidate.add_argument("--run-id",required=True); invalidate.add_argument("--reason",required=True); invalidate.add_argument("--observed-candidate-sha")
    sync_plan=sub.add_parser("sync-plan"); sync_plan.add_argument("--run-id",required=True); sync_plan.add_argument("--changed-paths",required=True); sync_plan.add_argument("--requested-paths"); sync_plan.add_argument("--full-project",action="store_true")
    record_sync=sub.add_parser("record-sync"); record_sync.add_argument("--run-id",required=True); record_sync.add_argument("--readback",required=True)
    startup_contract=sub.add_parser("startup-contract"); startup_contract.add_argument("--run-id",required=True); startup_contract.add_argument("--contract",required=True); startup_contract.add_argument("--available-dependencies",default=None)
    startup_check=sub.add_parser("startup-check"); startup_check.add_argument("--run-id",required=True)
    decide=sub.add_parser("decide"); decide.add_argument("--run-id",required=True); decide.add_argument("--subject",required=True); decide.add_argument("--selected",required=True); decide.add_argument("--recommendation",required=True); decide.add_argument("--evidence",required=True); decide.add_argument("--rationale",required=True); decide.add_argument("--actor",required=True); decide.add_argument("--scope",required=True); decide.add_argument("--source",required=True); decide.add_argument("--authorization",required=True)
    dependency_check=sub.add_parser("dependency-check"); dependency_check.add_argument("--run-id",required=True)
    dependency_readiness=sub.add_parser("dependency-readiness"); dependency_readiness.add_argument("--run-id",required=True); dependency_readiness.add_argument("--spec-id",required=True)
    prepare_intent=sub.add_parser("prepare-intent"); prepare_intent.add_argument("--run-id",required=True); prepare_intent.add_argument("--logical-action",required=True); prepare_intent.add_argument("--target",required=True); prepare_intent.add_argument("--target-state",required=True)
    intent_outcome=sub.add_parser("intent-outcome"); intent_outcome.add_argument("--intent-id",type=int,required=True); intent_outcome.add_argument("--status",choices=("succeeded","failed","outcome_unknown"),required=True); intent_outcome.add_argument("--response",required=True); intent_outcome.add_argument("--readback")
    reconcile_intent=sub.add_parser("reconcile-intent"); reconcile_intent.add_argument("--intent-id",type=int,required=True); reconcile_intent.add_argument("--readback",required=True)
    claim_intent=sub.add_parser("claim-intent"); claim_intent.add_argument("--intent-id",type=int,required=True); claim_intent.add_argument("--owner",required=True); claim_intent.add_argument("--lease-until",required=True); claim_intent.add_argument("--fencing-supported",action="store_true"); claim_intent.add_argument("--fencing-receipt")
    recovery=sub.add_parser("record-recovery"); recovery.add_argument("--intent-id",type=int,required=True); recovery.add_argument("--owner",required=True); recovery.add_argument("--classification",required=True); recovery.add_argument("--budget",type=int,required=True); recovery.add_argument("--evidence")
    recovery_failure=sub.add_parser("record-recovery-failure"); recovery_failure.add_argument("--run-id",required=True); recovery_failure.add_argument("--action-id",type=int,required=True); recovery_failure.add_argument("--category",required=True); recovery_failure.add_argument("--error-fingerprint",required=True); recovery_failure.add_argument("--code-digest",required=True); recovery_failure.add_argument("--environment-digest",required=True); recovery_failure.add_argument("--strategy-digest",required=True); recovery_failure.add_argument("--progress-marker",required=True); recovery_failure.add_argument("--retry-owner",required=True); recovery_failure.add_argument("--evidence",required=True); recovery_failure.add_argument("--max-attempts",type=int,default=3); recovery_failure.add_argument("--budget-seconds",type=int,default=3600); recovery_failure.add_argument("--budget-version",default="recovery-v1")
    claim_action=sub.add_parser("claim-action"); claim_action.add_argument("--action-id",type=int,required=True); claim_action.add_argument("--owner-id",required=True); claim_action.add_argument("--lease-seconds",type=int,default=60); claim_action.add_argument("--supports-fencing",action="store_true"); claim_action.add_argument("--outcome-reconciled",action="store_true")
    assert_effect=sub.add_parser("assert-action-effect"); assert_effect.add_argument("--action-id",type=int,required=True); assert_effect.add_argument("--owner-id",required=True)
    bootstrap_state=sub.add_parser("bootstrap-state"); bootstrap_state.add_argument("--run-id",required=True); bootstrap_state.add_argument("--thread-id",required=True); bootstrap_state.add_argument("--state",choices=("route_verifying","assigned","cancelled"),required=True); bootstrap_state.add_argument("--receipt")
    train_init=sub.add_parser("initialize-test-train"); train_init.add_argument("--run-id",required=True); train_init.add_argument("--spec-ids",required=True)
    test_gate=sub.add_parser("test-gate"); test_gate.add_argument("--run-id",required=True); test_gate.add_argument("--spec-id",required=True); test_gate.add_argument("--level",choices=("L0","L1","L2","L3"),required=True); test_gate.add_argument("--status",choices=("passed","failed"),required=True); test_gate.add_argument("--candidate-sha",required=True); test_gate.add_argument("--evidence",required=True)
    checkpoint=sub.add_parser("checkpoint"); checkpoint.add_argument("--run-id",required=True); checkpoint.add_argument("--sequence",type=int,required=True); checkpoint.add_argument("--status",choices=("passed","failed"),required=True); checkpoint.add_argument("--candidate-sha",required=True); checkpoint.add_argument("--evidence",required=True)
    train_status=sub.add_parser("test-train-status"); train_status.add_argument("--run-id",required=True)
    pin_policy=sub.add_parser("pin-policy"); pin_policy.add_argument("--run-id",required=True); pin_policy.add_argument("--policy",required=True); pin_policy.add_argument("--implementation-digest",required=True); pin_policy.add_argument("--migration")
    verify_policy=sub.add_parser("verify-policy"); verify_policy.add_argument("--run-id",required=True); verify_policy.add_argument("--policy",required=True); verify_policy.add_argument("--implementation-digest",required=True)
    context=sub.add_parser("context"); context.add_argument("--run-id",required=True); context.add_argument("--phase",required=True); context.add_argument("--entity-type",choices=("run","spec","ticket","intent")); context.add_argument("--entity-id"); context.add_argument("--refresh",action="store_true"); context.add_argument("--delta",action="store_true"); context.add_argument("--base-event-cursor",type=int); context.add_argument("--base-state-version",type=int); context.add_argument("--base-digests",default="{}")
    history=sub.add_parser("context-history"); history.add_argument("--pointer",required=True)
    measure=sub.add_parser("measure-context"); measure.add_argument("--run-id",required=True); measure.add_argument("--phase",required=True); measure.add_argument("--entity-type",choices=("run","spec","ticket","intent"),required=True); measure.add_argument("--entity-id",required=True); measure.add_argument("--observed-tokens",type=int); measure.add_argument("--fee",type=float); measure.add_argument("--refresh-count",type=int,default=0); measure.add_argument("--rejection-count",type=int,default=0)
    measurements=sub.add_parser("context-measurements"); measurements.add_argument("--run-id",required=True)
    action_contract=sub.add_parser("action-contract"); action_contract.add_argument("--action")
    action_check=sub.add_parser("action-contract-check"); action_check.add_argument("--actions",required=True)
    safety_metrics=sub.add_parser("safety-metrics"); safety_metrics.add_argument("--run-id",required=True); safety_metrics.add_argument("--scenario",choices=("simulation","contract-backed"),default="contract-backed")
    benchmark=sub.add_parser("benchmark-manifest")
    compare=sub.add_parser("compare-safety-metrics"); compare.add_argument("--before",type=Path,required=True); compare.add_argument("--after",type=Path,required=True)
    receipt=sub.add_parser("record-delivery-receipt"); receipt.add_argument("--run-id",required=True); receipt.add_argument("--entity-type",choices=("spec","ticket","run"),required=True); receipt.add_argument("--entity-id",required=True); receipt.add_argument("--receipt",required=True)
    receipt_check=sub.add_parser("validate-delivery-receipt"); receipt_check.add_argument("--run-id",required=True); receipt_check.add_argument("--entity-type",choices=("spec","ticket","run"),required=True); receipt_check.add_argument("--entity-id",required=True); receipt_check.add_argument("--expected",required=True)
    advance_cmd=sub.add_parser("advance"); advance_cmd.add_argument("--run-id",required=True); advance_cmd.add_argument("--max-actions",type=int,default=32)
    supervisor=sub.add_parser("supervisor"); supervisor.add_argument("--run-id",required=True); supervisor.add_argument("--owner-id",default="supervisor"); supervisor.add_argument("--lease-seconds",type=int,default=60); supervisor.add_argument("--stale-after-seconds",type=int,default=0); supervisor.add_argument("--max-attempts",type=int,default=3); supervisor.add_argument("--budget-seconds",type=float,default=300.0); supervisor.add_argument("--max-actions",type=int,default=1); supervisor.add_argument("--no-execute",action="store_true")
    context_budget=sub.add_parser("context-budget"); context_budget.add_argument("--run-id",required=True); context_budget.add_argument("--phase",default="planning"); context_budget.add_argument("--mode",choices=("compact","delta"),default="compact"); context_budget.add_argument("--fixture",action="append",default=[],help="CASE=RUN_ID; repeat for the fixed five-case suite")
    budget_gate=sub.add_parser("context-budget-gate"); budget_gate.add_argument("--run-id",required=True); budget_gate.add_argument("--phase",required=True); budget_gate.add_argument("--mode",choices=("compact","delta"),default="compact"); budget_gate.add_argument("--base-event-cursor",type=int); budget_gate.add_argument("--base-state-version",type=int); budget_gate.add_argument("--fixture",action="append",default=[],help="CASE=RUN_ID; repeat for the fixed five-case suite")
    backup_manifest=sub.add_parser("backup-manifest"); backup_manifest.add_argument("--run-id",required=True); backup_manifest.add_argument("--file-digests",default="{}"); backup_manifest.add_argument("--database-digest")
    validate_manifest=sub.add_parser("validate-backup-manifest"); validate_manifest.add_argument("--run-id",required=True); validate_manifest.add_argument("--manifest-id",type=int,required=True); validate_manifest.add_argument("--file-digests"); validate_manifest.add_argument("--database-digest")
    begin_restore=sub.add_parser("begin-restore"); begin_restore.add_argument("--run-id",required=True); begin_restore.add_argument("--manifest-id",type=int,required=True)
    restore_reconcile=sub.add_parser("restore-reconciliation"); restore_reconcile.add_argument("--restore-id",type=int,required=True); restore_reconcile.add_argument("--status",choices=("pending","complete","blocked"),required=True); restore_reconcile.add_argument("--evidence",required=True)
    operation_intent=sub.add_parser("operation-intent"); operation_intent.add_argument("--run-id",required=True); operation_intent.add_argument("--operation",required=True); operation_intent.add_argument("--target",required=True); operation_intent.add_argument("--parameters",required=True); operation_intent.add_argument("--generation",type=int,default=0); operation_intent.add_argument("--idempotency-key")
    start_intent=sub.add_parser("start-operation-intent"); start_intent.add_argument("--intent-id",type=int,required=True); start_intent.add_argument("--executor-id",required=True)
    unknown_intent=sub.add_parser("operation-outcome-unknown"); unknown_intent.add_argument("--intent-id",type=int,required=True); unknown_intent.add_argument("--reason",required=True); unknown_intent.add_argument("--evidence",required=True)
    reconcile_operation=sub.add_parser("reconcile-operation-intent"); reconcile_operation.add_argument("--intent-id",type=int,required=True); reconcile_operation.add_argument("--outcome",choices=("not_found","succeeded","failed"),required=True); reconcile_operation.add_argument("--evidence",required=True); reconcile_operation.add_argument("--result",default="{}")
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
    for versioned in (thread, thread_state, spec_state, ticket_state, action, complete_child, finish, migrate, ledger, adopt, run_phase, run_result, resume, configure_auth, freeze, candidate_evidence, invalidate, record_sync, startup_contract, decide, prepare_intent, intent_outcome, reconcile_intent, claim_intent, recovery, bootstrap_state, train_init, test_gate, checkpoint, pin_policy, backup_manifest, begin_restore, restore_reconcile, reconcile_ticket):
        versioned.add_argument("--expected-version", type=int, required=True)
    args=parser.parse_args()
    db=ControlDB(args.db, mode="create" if args.command == "init" else ("read-only" if args.command in {"snapshot", "auth-check", "sync-plan", "startup-check", "dependency-check", "dependency-readiness", "test-train-status", "verify-policy", "validate-backup-manifest", "context-history", "context-measurements", "context-budget", "context-budget-gate", "action-contract", "action-contract-check", "safety-metrics", "benchmark-manifest", "compare-safety-metrics"} else "open-existing"))
    try:
        if args.command=="init": db.create_run(args.run_id,args.initiative,args.requirement,args.execution_mode,args.controller_task_id,json.loads(args.queue_definition),json.loads(args.authorization) if args.authorization else None); result={"run_id":args.run_id,"status":"active","execution_mode":args.execution_mode}
        elif args.command=="add-spec": db.add_spec(args.run_id,args.spec_id,args.title,args.position,json.loads(args.blocked_by),json.loads(args.acceptance),args.expected_version); result={"spec_id":args.spec_id}
        elif args.command=="add-ticket": db.add_ticket(args.spec_id,args.ticket_id,args.title,json.loads(args.blocked_by),args.issue_url,args.queue_position,args.expected_version); result={"ticket_id":args.ticket_id}
        elif args.command=="reconcile-ticket-dependencies": result=db.reconcile_ticket_blockers(args.ticket_id,json.loads(args.blocked_by),args.expected_version,json.loads(args.evidence))
        elif args.command=="register-thread":
            from task_identity import TaskIdentity
            identity=TaskIdentity(**json.loads(args.identity)) if args.identity else None
            inserted=db.add_thread(args.run_id,args.thread_id,args.kind,args.spec_id,identity=identity,client_thread_id=args.client_thread_id,host_id=args.host_id,owner_id=args.owner_id,cwd=args.cwd,project_id=args.project_id,title_token=args.title_token,formal_thread_id=args.formal_thread_id,expected_version=args.expected_version)
            result={"thread_id":args.thread_id,"lifecycle":"created","inserted":inserted}
        elif args.command=="thread-state": db.update_thread(args.run_id,args.thread_id,args.lifecycle,args.outcome,args.next_action,args.archive_operation,args.archive_readback,args.expected_version,json.loads(args.gate) if args.gate else None); result={"thread_id":args.thread_id,"lifecycle":args.lifecycle}
        elif args.command=="spec-state":
            if args.context:
                db.update_spec_from_context(args.spec_id, args.status, json.loads(args.context), json.loads(args.gate) if args.gate else None)
            else:
                db.update_spec(args.spec_id,args.status,args.expected_version,json.loads(args.gate) if args.gate else None)
            result={"spec_id":args.spec_id,"status":args.status}
        elif args.command=="ticket-state":
            if args.context:
                db.update_ticket_from_context(args.ticket_id, args.status, json.loads(args.context), json.loads(args.commits) if args.commits else None, json.loads(args.tests) if args.tests else None, json.loads(args.acceptance) if args.acceptance else None, json.loads(args.gate) if args.gate else None)
            else:
                db.update_ticket(args.ticket_id,args.status,json.loads(args.commits) if args.commits else None,json.loads(args.tests) if args.tests else None,json.loads(args.acceptance) if args.acceptance else None,args.expected_version,json.loads(args.gate) if args.gate else None)
            result={"ticket_id":args.ticket_id,"status":args.status}
        elif args.command=="record-observation":
            if args.observation_key or args.phase:
                metadata=json.loads(args.metadata); metadata.setdefault("operation", args.operation)
                if args.evidence != "[]": metadata["evidence"] = json.loads(args.evidence)
                result=db.record_runtime_observation(args.run_id, args.observation_key or f"{args.run_id}:{args.entity_type}:{args.entity_id}:{args.operation}", args.entity_type, args.entity_id, args.phase or args.operation, args.status, args.scope, args.unit, args.started_at, args.ended_at, args.duration_ms, args.source, json.loads(args.usage), metadata)
            else:
                db.add_observation(args.run_id,args.entity_type,args.entity_id,{"operation":args.operation,"status":args.status,"evidence":json.loads(args.evidence)}); result={"entity_id":args.entity_id,"status":args.status}
        elif args.command=="tool-success":
            from tool_envelopes import success
            result=success(json.loads(args.result), json.loads(args.identifiers), args.version, args.evidence_uri)
        elif args.command=="tool-failure":
            from tool_envelopes import failure
            result=failure(args.category, args.error_fragment, args.log_uri, args.version)
        elif args.command=="host-operation":
            from tool_envelopes import host_operation
            result=host_operation(args.operation, json.loads(args.arguments), args.required_capability, json.loads(args.capabilities), json.loads(args.capability_evidence))
        elif args.command=="action": result={"action_id":db.set_action(args.run_id,args.kind,args.target,expected_version=args.expected_version)}
        elif args.command=="complete-child":
            from controller_recovery import complete_child_and_persist_next_action
            result=complete_child_and_persist_next_action(
                db, args.run_id, child_kind=args.child_kind, child_id=args.child_id,
                next_action=json.loads(args.next_action), result=json.loads(args.result),
                expected_version=args.expected_version,
            )
        elif args.command=="finish-action": db.finish_action(args.action_id,args.status,json.loads(args.result) if args.result else None,expected_version=args.expected_version,gate=json.loads(args.gate) if args.gate else None); result={"action_id":args.action_id,"status":args.status}
        elif args.command=="next-action":
            from next_action import next_action
            result=next_action(db,args.run_id)
        elif args.command=="run-phase":
            result=db.advance_run_phase(args.run_id,args.phase,json.loads(args.receipt),args.expected_version)
        elif args.command=="run-result":
            result=db.set_run_result(args.run_id,args.result,args.reason,json.loads(args.receipt),args.expected_version)
        elif args.command=="resume-run":
            result=db.resume_run(args.run_id,args.expected_version)
        elif args.command=="configure-auth":
            result=db.configure_authorization(args.run_id,json.loads(args.authorization),args.expected_version)
        elif args.command=="auth-check":
            result=db.authorize(args.run_id, action=args.action, path=args.path, target_ref=args.target_ref, environment=args.environment, full_project=args.full_project)
        elif args.command=="freeze-candidate":
            result=db.freeze_candidate(args.run_id,args.candidate_sha,json.loads(args.evidence),args.merge_sha,args.expected_version)
        elif args.command=="candidate-evidence":
            result=db.record_candidate_evidence(args.run_id,args.kind,json.loads(args.evidence),args.expected_version)
        elif args.command=="invalidate-candidate":
            result=db.invalidate_candidate(args.run_id,args.reason,args.observed_candidate_sha,args.expected_version)
        elif args.command=="sync-plan":
            record=db.authorization(args.run_id)
            result=build_sync_plan(record["payload"],json.loads(args.changed_paths),requested_paths=json.loads(args.requested_paths) if args.requested_paths else None,full_project=args.full_project)
        elif args.command=="record-sync":
            result=db.record_synchronization(args.run_id,json.loads(args.readback),args.expected_version)
        elif args.command=="startup-contract":
            available = json.loads(args.available_dependencies) if args.available_dependencies else None
            result=db.record_startup_contract(args.run_id,json.loads(args.contract),available,args.expected_version)
        elif args.command=="startup-check":
            result=db.startup_contract(args.run_id)
        elif args.command=="decide":
            result=db.decide(args.run_id, args.subject, json.loads(args.selected), json.loads(args.recommendation), json.loads(args.evidence), args.rationale, args.actor, json.loads(args.scope), args.source, json.loads(args.authorization), args.expected_version)
        elif args.command=="dependency-check":
            result=structure_from_db(db, args.run_id)
        elif args.command=="dependency-readiness":
            result=readiness_from_db(db, args.run_id, args.spec_id)
        elif args.command=="prepare-intent":
            result=db.prepare_intent(args.run_id, args.logical_action, args.target, args.target_state, args.expected_version)
        elif args.command=="intent-outcome":
            result=db.record_intent_outcome(args.intent_id, args.status, json.loads(args.response), json.loads(args.readback) if args.readback else None, args.expected_version)
        elif args.command=="reconcile-intent":
            result=db.reconcile_intent(args.intent_id, json.loads(args.readback), args.expected_version)
        elif args.command=="claim-intent":
            result=db.claim_intent(args.intent_id, args.owner, args.lease_until, args.fencing_supported, json.loads(args.fencing_receipt) if args.fencing_receipt else None, args.expected_version)
        elif args.command=="record-recovery":
            result=db.record_recovery(args.intent_id, args.owner, args.classification, args.budget, json.loads(args.evidence) if args.evidence else None, args.expected_version)
        elif args.command=="record-recovery-failure":
            from recovery import record_failure
            result=record_failure(db, args.run_id, args.action_id, args.category, args.error_fingerprint, args.code_digest, args.environment_digest, args.strategy_digest, args.progress_marker, args.retry_owner, json.loads(args.evidence), args.max_attempts, args.budget_seconds, args.budget_version)
        elif args.command=="claim-action":
            result=db.claim_action(args.action_id, args.owner_id, args.lease_seconds, args.supports_fencing, args.outcome_reconciled)
        elif args.command=="assert-action-effect":
            result=db.assert_action_effect_permitted(args.action_id, args.owner_id)
        elif args.command=="bootstrap-state":
            result=db.advance_bootstrap(args.run_id, args.thread_id, args.state, json.loads(args.receipt) if args.receipt else None, args.expected_version)
        elif args.command=="initialize-test-train":
            result=db.initialize_test_train(args.run_id, json.loads(args.spec_ids), expected_version=args.expected_version)
        elif args.command=="test-gate":
            result=db.record_test_gate(args.run_id, args.spec_id, args.level, args.status, args.candidate_sha, json.loads(args.evidence), args.expected_version)
        elif args.command=="checkpoint":
            result=db.record_checkpoint(args.run_id, args.sequence, args.status, args.candidate_sha, json.loads(args.evidence), args.expected_version)
        elif args.command=="test-train-status":
            result=db.test_train_status(args.run_id)
        elif args.command=="pin-policy":
            result=db.pin_policy(args.run_id, json.loads(args.policy), args.implementation_digest, args.expected_version, json.loads(args.migration) if args.migration else None)
        elif args.command=="verify-policy":
            result=db.verify_policy(args.run_id, json.loads(args.policy), args.implementation_digest)
        elif args.command=="context":
            if args.entity_type and args.entity_id:
                if args.delta:
                    from context_delta import ContextDeltaError
                    raise ContextDeltaError("delta_entity_conflict")
                result=build_context(db, args.run_id, args.phase, args.entity_type, args.entity_id)
            elif args.delta:
                from context_delta import ContextDeltaError, build_delta_context
                if args.base_event_cursor is None:
                    raise ContextDeltaError("delta_base_cursor_missing")
                try:
                    base_digests=json.loads(args.base_digests)
                except (TypeError, json.JSONDecodeError):
                    raise ContextDeltaError("delta_base_digests_invalid") from None
                result=build_delta_context(db, args.run_id, args.phase, args.base_event_cursor, args.base_state_version, base_digests)
            else:
                from phase_context import assemble_context, refresh_context
                result=refresh_context(db, args.run_id, args.phase) if args.refresh else assemble_context(db, args.run_id, args.phase)
        elif args.command=="context-history":
            result=read_history(db, args.pointer)
        elif args.command=="measure-context":
            started = time.perf_counter()
            context = build_context(db, args.run_id, args.phase, args.entity_type, args.entity_id)
            measurement = measure_context(context, latency_ms=(time.perf_counter() - started) * 1000, observed_tokens=args.observed_tokens, fee=args.fee, refresh_count=args.refresh_count, rejection_count=args.rejection_count)
            result=db.record_context_measurement(args.run_id, context, measurement)
        elif args.command=="context-measurements":
            result={"run_id": args.run_id, "measurements": db.context_measurements(args.run_id)}
        elif args.command=="action-contract":
            result=render_cli_help(args.action)
        elif args.command=="action-contract-check":
            result=validate_action_surface(json.loads(args.actions))
        elif args.command=="safety-metrics":
            result=collect_metrics(db, args.run_id, scenario=args.scenario)
        elif args.command=="benchmark-manifest":
            result=benchmark_manifest()
        elif args.command=="compare-safety-metrics":
            result=compare_metrics(json.loads(args.before.read_text(encoding="utf-8")), json.loads(args.after.read_text(encoding="utf-8")))
        elif args.command=="record-delivery-receipt":
            from delivery_receipts import record
            result=record(db, args.run_id, args.entity_type, args.entity_id, json.loads(args.receipt))
        elif args.command=="validate-delivery-receipt":
            from delivery_receipts import project
            result=project(db, args.run_id, args.entity_type, args.entity_id, json.loads(args.expected))
        elif args.command=="advance":
            from advance_runtime import advance
            result=advance(db, args.run_id, args.max_actions)
        elif args.command=="supervisor":
            from supervisor import supervise
            result=supervise(
                db, args.run_id, owner_id=args.owner_id, lease_seconds=args.lease_seconds,
                stale_after_seconds=args.stale_after_seconds, max_attempts=args.max_attempts,
                budget_seconds=args.budget_seconds, max_actions=args.max_actions, execute=not args.no_execute,
            )
        elif args.command=="context-budget":
            from context_budget import BENCHMARK_CASES, benchmark_context, benchmark_context_suite, benchmark_delta_context, benchmark_delta_suite
            fixtures = {}
            for item in args.fixture:
                if "=" not in item:
                    raise ValueError("--fixture must use CASE=RUN_ID")
                case, fixture_run_id = item.split("=", 1)
                if case not in BENCHMARK_CASES or not fixture_run_id:
                    raise ValueError("--fixture must name a fixed benchmark case and run id")
                if case in fixtures:
                    raise ValueError("--fixture cannot repeat a benchmark case")
                fixtures[case] = fixture_run_id
            if args.mode == "delta":
                result = benchmark_delta_suite(db, fixtures, args.phase) if fixtures else benchmark_delta_context(db, args.run_id, args.phase)
            else:
                result = benchmark_context_suite(db, fixtures, args.phase) if fixtures else benchmark_context(db, args.run_id, args.phase)
        elif args.command=="context-budget-gate":
            from context_budget_gate import BENCHMARK_CASES, admit_context, benchmark_budget_suite
            fixtures = {}
            for item in args.fixture:
                if "=" not in item:
                    raise ValueError("--fixture must use CASE=RUN_ID")
                case, fixture_run_id = item.split("=", 1)
                if case not in BENCHMARK_CASES or not fixture_run_id or case in fixtures:
                    raise ValueError("--fixture must name each fixed benchmark case once")
                fixtures[case] = fixture_run_id
            result=benchmark_budget_suite(db, fixtures, args.phase) if fixtures else admit_context(db, args.run_id, args.phase, args.mode, args.base_event_cursor, args.base_state_version)
        elif args.command=="save-snapshot":
            result=db.save_snapshot(args.run_id, json.loads(args.payload), args.expected_event_cursor, args.expected_state_version)
        elif args.command=="backup-manifest":
            result=db.create_backup_manifest(args.run_id, json.loads(args.file_digests), args.database_digest, args.expected_version)
        elif args.command=="validate-backup-manifest":
            result=db.validate_backup_manifest(args.run_id, args.manifest_id, json.loads(args.file_digests) if args.file_digests else None, args.database_digest)
        elif args.command=="begin-restore":
            result=db.begin_restore(args.run_id, args.manifest_id, args.expected_version)
        elif args.command=="restore-reconciliation":
            result=db.record_restore_reconciliation(args.restore_id, args.status, json.loads(args.evidence), args.expected_version)
        elif args.command=="operation-intent":
            result=db.create_operation_intent(args.run_id, args.operation, args.target, json.loads(args.parameters), args.generation, args.idempotency_key)
        elif args.command=="start-operation-intent":
            result=db.start_operation_intent(args.intent_id, args.executor_id)
        elif args.command=="operation-outcome-unknown":
            result=db.mark_operation_unknown(args.intent_id, args.reason, json.loads(args.evidence))
        elif args.command=="reconcile-operation-intent":
            result=db.reconcile_operation_intent(args.intent_id, args.outcome, json.loads(args.evidence), json.loads(args.result))
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
    except EvidenceGateError as exc:
        result={"decision":"reject","error":exc.code,"details":exc.details}
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 1
    except RunStateError as exc:
        result={"decision":"reject","error":exc.code,"details":exc.details}
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 1
    except AuthorizationError as exc:
        result={"decision":"reject","error":exc.code,"details":exc.details}
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 1
    except SyncScopeError as exc:
        result={"decision":"reject","error":exc.code,"details":exc.details}
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 1
    except StartupContractError as exc:
        result={"decision":"reject","error":exc.code,"details":exc.details}
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 1
    except DecisionError as exc:
        result={"decision":"reject","error":exc.code,"details":exc.details}
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 1
    except RestoreValidationError as exc:
        result={"decision":"reject","error":exc.code,"details":exc.details}
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 1
    except PolicyError as exc:
        result={"decision":"reject","error":exc.code,"details":exc.details}
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 1
    except ContextProjectionError as exc:
        result={"decision":"reject","error":exc.code,"details":exc.details}
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 1
    except ActionContractError as exc:
        result={"decision":"reject","error":exc.code,"details":exc.details}
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 1
    except ActionClaimConflict as exc:
        result={"decision":"repair","error":"action_claim_conflict","detail":str(exc)}
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 1
    except UnsafeLeaseTakeover as exc:
        result={"decision":"repair","error":"unsafe_lease_takeover","detail":str(exc)}
        print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 1
    finally: db.close()

if __name__ == "__main__": raise SystemExit(main())
