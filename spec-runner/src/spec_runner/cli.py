from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .errors import RunnerError
from .github_tracker import GitHubTracker
from .github_delivery import GitHubDelivery
from .faults import run_fault_matrix
from .delivery import merge_local, prepare_workspace, validate_review, verify_candidate
from .diagnostics import build_release_report, inspect_wheel, load_json as diagnostic_json, runtime_report, validate_fault_matrix, validate_release_report
from .matt import load_lock, render_prompt, resolve_grill, resolve_local_skill
from .multi_spec import run_local_delivery
from .legacy import legacy_takeover_inventory, read_legacy_database
from .plans import intake_snapshot, load_json as plan_json, validate_spec_plan, validate_ticket_plan
from .takeover import completion_action, inspect_takeover, load_inventory, perform_cleanup, plan_frontier, write_takeover_record
from .tracker import publish_local, read_local
from .store import Store
from .workflow import control, doctor, launch, resume, start, status
from .codex_adapter import CodexAdapter

CLI_SCHEMA_VERSION = "spec-runner-cli/v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spec-runner")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    start_parser = subparsers.add_parser("start", help="create or replay a deterministic test run")
    start_parser.add_argument("--brief", required=True, type=Path)
    start_parser.add_argument("--config", required=True, type=Path)
    start_parser.add_argument("--control-root", required=True, type=Path)
    start_parser.add_argument("--launch-key", required=True)
    start_parser.add_argument("--run-id")
    drive_parser = subparsers.add_parser("drive", help="resume the durable execution loop after an external process exit")
    drive_parser.add_argument("--brief", required=True, type=Path)
    drive_parser.add_argument("--config", required=True, type=Path)
    drive_parser.add_argument("--control-root", required=True, type=Path)
    drive_parser.add_argument("--launch-key", required=True)
    status_parser = subparsers.add_parser("status", help="read persisted status without changing it")
    status_parser.add_argument("--control-root", required=True, type=Path)
    status_parser.add_argument("--run-id")
    for name, request in (("pause", "pause_requested"), ("cancel", "cancel_requested")):
        command_parser = subparsers.add_parser(name, help=f"request {name} at the next safe stage boundary")
        command_parser.add_argument("--control-root", required=True, type=Path)
        command_parser.add_argument("--run-id", required=True)
        command_parser.set_defaults(control_request=request)
    resume_parser = subparsers.add_parser("resume", help="clear pause and resume an existing launch identity")
    resume_parser.add_argument("--brief", required=True, type=Path)
    resume_parser.add_argument("--config", required=True, type=Path)
    resume_parser.add_argument("--control-root", required=True, type=Path)
    resume_parser.add_argument("--launch-key", required=True)
    answer_parser = subparsers.add_parser("answer", help="persist an answer for a blocked or input-gated run")
    answer_parser.add_argument("--control-root", required=True, type=Path)
    answer_parser.add_argument("--run-id", required=True)
    answer_parser.add_argument("--question-id", required=True)
    answer_parser.add_argument("--value", required=True)
    answer_parser.add_argument("--brief", type=Path)
    answer_parser.add_argument("--config", type=Path)
    launch_parser = subparsers.add_parser("launch", help="start a detached Runner and wait for its handshake")
    launch_parser.add_argument("--brief", required=True, type=Path)
    launch_parser.add_argument("--config", required=True, type=Path)
    launch_parser.add_argument("--control-root", required=True, type=Path)
    launch_parser.add_argument("--launch-key", required=True)
    tracker_parser = subparsers.add_parser("tracker", help="read or publish a local issue tracker")
    tracker_subparsers = tracker_parser.add_subparsers(dest="tracker_command", required=True)
    tracker_read = tracker_subparsers.add_parser("read")
    tracker_read.add_argument("--root", required=True, type=Path)
    tracker_publish = tracker_subparsers.add_parser("publish")
    tracker_publish.add_argument("--source-root", required=True, type=Path)
    tracker_publish.add_argument("--target-root", required=True, type=Path)
    tracker_publish.add_argument("--operation-id", required=True)
    tracker_github = tracker_subparsers.add_parser("github-read")
    tracker_github.add_argument("--repository", required=True)
    tracker_github.add_argument("--issue", required=True, type=int)
    tracker_github.add_argument("--linked-issue", action="append", type=int, default=[])
    tracker_github_publish = tracker_subparsers.add_parser("github-publish")
    tracker_github_publish.add_argument("--repository", required=True)
    tracker_github_publish.add_argument("--draft", required=True, type=Path)
    tracker_github_publish.add_argument("--receipt-root", required=True, type=Path)
    tracker_github_publish.add_argument("--operation-id", required=True)
    tracker_github_publish.add_argument("--relation-mode", choices=["body_links", "native"], default="body_links")
    github_delivery_parser = subparsers.add_parser("github-delivery", help="guarded GitHub PR/check/merge operations")
    github_delivery_sub = github_delivery_parser.add_subparsers(dest="github_delivery_command", required=True)
    pr_parser = github_delivery_sub.add_parser("pr")
    pr_parser.add_argument("--repository", required=True)
    pr_parser.add_argument("--head", required=True)
    pr_parser.add_argument("--base", required=True)
    pr_parser.add_argument("--candidate-sha", required=True)
    pr_parser.add_argument("--body", required=True, type=Path)
    pr_parser.add_argument("--operation-id", required=True)
    pr_parser.add_argument("--receipt-root", required=True, type=Path)
    checks_parser = github_delivery_sub.add_parser("checks")
    checks_parser.add_argument("--repository", required=True)
    checks_parser.add_argument("--candidate-sha", required=True)
    checks_parser.add_argument("--required", action="append", required=True)
    merge_parser = github_delivery_sub.add_parser("merge")
    merge_parser.add_argument("--repository", required=True)
    merge_parser.add_argument("--number", required=True, type=int)
    merge_parser.add_argument("--expected-head", required=True)
    merge_parser.add_argument("--expected-base", required=True)
    merge_parser.add_argument("--candidate-receipt", required=True, type=Path)
    merge_parser.add_argument("--review", required=True, type=Path)
    merge_parser.add_argument("--checks", required=True, type=Path)
    merge_parser.add_argument("--authorize", action="store_true")
    intake_parser = subparsers.add_parser("intake", help="adopt an explicit existing plan without regenerating tickets")
    intake_sub = intake_parser.add_subparsers(dest="intake_command", required=True)
    intake_local = intake_sub.add_parser("local")
    intake_local.add_argument("--root", required=True, type=Path)
    intake_local.add_argument("--entry", required=True)
    plan_parser = subparsers.add_parser("plan", help="validate versioned planning artifacts")
    plan_sub = plan_parser.add_subparsers(dest="plan_command", required=True)
    for name in ("validate-spec", "validate-tickets"):
        item = plan_sub.add_parser(name)
        item.add_argument("--file", required=True, type=Path)
    plan_sub.choices["validate-tickets"].add_argument("--spec-key")
    plan_sub.choices["validate-tickets"].add_argument("--base-sha")
    skill_parser = subparsers.add_parser("skill", help="render current local Skill input without publishing")
    skill_sub = skill_parser.add_subparsers(dest="skill_command", required=True)
    skill_render = skill_sub.add_parser("render")
    skill_render.add_argument("--lock", type=Path, help="historical pinned source mode only")
    skill_render.add_argument("--config", type=Path, help="optional live local Skill mapping")
    skill_render.add_argument("--phase", required=True, choices=["grill", "to-spec", "to-tickets", "implement", "review"])
    skill_render.add_argument("--trusted", required=True, type=Path)
    skill_render.add_argument("--untrusted", required=True, type=Path)
    skill_render.add_argument("--schema", required=True, type=Path)
    skill_render.add_argument("--skill-root", action="append", type=Path, default=[])
    grill_parser = subparsers.add_parser("grill", help="resolve one bounded Grill round from structured questions")
    grill_parser.add_argument("--file", required=True, type=Path)
    workspace_parser = subparsers.add_parser("workspace", help="prepare a run-owned isolated Git worktree")
    workspace_sub = workspace_parser.add_subparsers(dest="workspace_command", required=True)
    workspace_prepare = workspace_sub.add_parser("prepare")
    for option in ("repository", "workspace-root"):
        workspace_prepare.add_argument(f"--{option}", required=True, type=Path)
    workspace_prepare.add_argument("--run-id", required=True)
    workspace_prepare.add_argument("--spec-key", required=True)
    workspace_prepare.add_argument("--base-ref", default="HEAD")
    candidate_parser = subparsers.add_parser("candidate", help="run trusted candidate verification")
    candidate_sub = candidate_parser.add_subparsers(dest="candidate_command", required=True)
    candidate_verify = candidate_sub.add_parser("verify")
    candidate_verify.add_argument("--workspace", required=True, type=Path)
    candidate_verify.add_argument("--candidate-sha", required=True)
    candidate_verify.add_argument("--acceptance-version", required=True)
    candidate_verify.add_argument("--checks", required=True, type=Path)
    candidate_verify.add_argument("--acceptance", required=True, type=Path)
    review_parser = subparsers.add_parser("review", help="validate a fresh read-only review receipt")
    review_sub = review_parser.add_subparsers(dest="review_command", required=True)
    review_validate = review_sub.add_parser("validate")
    review_validate.add_argument("--file", required=True, type=Path)
    review_validate.add_argument("--candidate-sha", required=True)
    review_validate.add_argument("--acceptance-version", required=True)
    merge_parser = subparsers.add_parser("merge", help="perform guarded local Git merge")
    merge_sub = merge_parser.add_subparsers(dest="merge_command", required=True)
    merge_local_parser = merge_sub.add_parser("local")
    for option in ("repository", "workspace-root"):
        merge_local_parser.add_argument(f"--{option}", required=True, type=Path)
    merge_local_parser.add_argument("--candidate-branch", required=True)
    merge_local_parser.add_argument("--target-ref", required=True)
    merge_local_parser.add_argument("--expected-target-sha", required=True)
    merge_local_parser.add_argument("--run-id", required=True)
    delivery_parser = subparsers.add_parser("delivery", help="run a trusted local multi-SPEC delivery plan")
    delivery_sub = delivery_parser.add_subparsers(dest="delivery_command", required=True)
    delivery_run = delivery_sub.add_parser("run")
    delivery_run.add_argument("--plan", required=True, type=Path)
    delivery_run.add_argument("--repository", required=True, type=Path)
    delivery_run.add_argument("--workspace-root", required=True, type=Path)
    delivery_run.add_argument("--control-root", required=True, type=Path)
    delivery_run.add_argument("--run-id", required=True)
    delivery_run.add_argument("--target-ref", default="HEAD")
    takeover_parser = subparsers.add_parser("takeover", help="inventory and safely adopt an arbitrary-stage delivery")
    takeover_sub = takeover_parser.add_subparsers(dest="takeover_command", required=True)
    takeover_inspect = takeover_sub.add_parser("inspect")
    takeover_inspect.add_argument("--file", required=True, type=Path)
    takeover_sdk_read = takeover_sub.add_parser("sdk-read", help="read one explicitly supplied SDK thread without starting a turn")
    takeover_sdk_read.add_argument("--thread-id", required=True)
    takeover_sdk_read.add_argument("--repository", required=True, type=Path)
    takeover_apply = takeover_sub.add_parser("apply")
    takeover_apply.add_argument("--file", required=True, type=Path)
    takeover_apply.add_argument("--control-root", required=True, type=Path)
    takeover_apply.add_argument("--takeover-key", required=True)
    takeover_apply.add_argument("--brief", type=Path)
    takeover_apply.add_argument("--config", type=Path)
    takeover_apply.add_argument("--launch-key")
    diagnostic_parser = subparsers.add_parser("diagnose", help="validate fault and release evidence without LLM calls")
    diagnostic_sub = diagnostic_parser.add_subparsers(dest="diagnostic_command", required=True)
    for name in ("fault-matrix", "release-report"):
        item = diagnostic_sub.add_parser(name)
        item.add_argument("--file", required=True, type=Path)
    diagnostic_sub.choices["release-report"].add_argument("--runner-version", default=__version__)
    release_build = diagnostic_sub.add_parser("release-build")
    release_build.add_argument("--subject", required=True, type=Path)
    release_build.add_argument("--evidence", required=True, action="append", type=Path)
    release_build.add_argument("--output", required=True, type=Path)
    release_build.add_argument("--required-kind", action="append", default=[])
    package_diagnostic = diagnostic_sub.add_parser("package")
    package_diagnostic.add_argument("--wheel", required=True, type=Path)
    package_diagnostic.add_argument("--runner-version", default=__version__)
    runtime_diagnostic = diagnostic_sub.add_parser("runtime")
    runtime_diagnostic.add_argument("--control-root", required=True, type=Path)
    legacy_parser = subparsers.add_parser("legacy", help="read an old control DB without migrating or writing it")
    legacy_sub = legacy_parser.add_subparsers(dest="legacy_command", required=True)
    legacy_read = legacy_sub.add_parser("read")
    legacy_read.add_argument("--db", required=True, type=Path)
    legacy_inspect = legacy_sub.add_parser("inspect", help="convert a read-only legacy observation into takeover input")
    legacy_inspect.add_argument("--db", required=True, type=Path)
    legacy_inspect.add_argument("--repository", required=True, type=Path)
    fault_parser = subparsers.add_parser("fault", help="run deterministic public-CLI fault scenarios")
    fault_sub = fault_parser.add_subparsers(dest="fault_command", required=True)
    fault_run = fault_sub.add_parser("run")
    fault_run.add_argument("--seed", default="sr-07-seed-1")
    fault_run.add_argument("--keep-artifacts", action="store_true")
    doctor_parser = subparsers.add_parser("doctor", help="read-only configuration checks")
    doctor_parser.add_argument("--config", type=Path)
    doctor_parser.add_argument("--control-root", required=True, type=Path)
    return parser


def _emit(payload: dict[str, object]) -> None:
    # The CLI contract is UTF-8 even when Windows inherited a legacy console
    # code page.  This also makes JSON safe for callers outside a console.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({"schema_version": CLI_SCHEMA_VERSION, **payload}, ensure_ascii=False, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command in {"start", "drive"}:
            result = start(
                brief_file=arguments.brief,
                config_file=arguments.config,
                control_root=arguments.control_root,
                launch_key=arguments.launch_key,
                run_id=getattr(arguments, "run_id", None),
            )
        elif arguments.command == "status":
            result = status(control_root=arguments.control_root, run_id=arguments.run_id)
        elif arguments.command in {"pause", "cancel"}:
            result = control(
                control_root=arguments.control_root,
                run_id=arguments.run_id,
                requested_state=arguments.control_request,
            )
        elif arguments.command == "resume":
            result = resume(
                brief_file=arguments.brief,
                config_file=arguments.config,
                control_root=arguments.control_root,
                launch_key=arguments.launch_key,
            )
        elif arguments.command == "answer":
            try:
                answer_value = json.loads(arguments.value)
            except json.JSONDecodeError:
                answer_value = arguments.value
            store = Store.open(arguments.control_root.expanduser().resolve(), create=False)
            try:
                current = store.find_by_run_id(arguments.run_id)
                if current and current.state == "needs_input":
                    answer = store.submit_answer_and_wake(run_id=arguments.run_id, question_id=arguments.question_id, value=answer_value)
                else:
                    # Preserve the historical answer-only CLI contract for
                    # non-interactive runs; only a waiting run gains a wake intent.
                    answer = store.submit_answer(run_id=arguments.run_id, question_id=arguments.question_id, value=answer_value)
                result = {"accepted": True, "answer": answer, **store.public_status(arguments.run_id)}
            finally:
                store.close()
            if arguments.brief or arguments.config:
                if not arguments.brief or not arguments.config:
                    raise RunnerError("answer_inputs_incomplete", "answer continuation requires both --brief and --config")
                result["continuation"] = resume(brief_file=arguments.brief, config_file=arguments.config, control_root=arguments.control_root, launch_key=str(result["run"]["launch_key"]))
        elif arguments.command == "launch":
            result = launch(
                brief_file=arguments.brief,
                config_file=arguments.config,
                control_root=arguments.control_root,
                launch_key=arguments.launch_key,
            )
        elif arguments.command == "tracker":
            if arguments.tracker_command == "read":
                result = read_local(arguments.root).public()
            elif arguments.tracker_command == "publish":
                result = publish_local(
                    read_local(arguments.source_root), arguments.target_root, operation_id=arguments.operation_id
                )
            elif arguments.tracker_command == "github-read":
                github_result = GitHubTracker().read_issue(
                    repository=arguments.repository, number=arguments.issue, linked_numbers=arguments.linked_issue
                )
                result = {"snapshot": github_result.snapshot.public(), "relation_evidence": github_result.relation_evidence}
            else:
                result = GitHubTracker().publish_draft(
                    repository=arguments.repository,
                    draft=plan_json(arguments.draft),
                    operation_id=arguments.operation_id,
                    receipt_root=arguments.receipt_root,
                    relation_mode=arguments.relation_mode,
                )
        elif arguments.command == "github-delivery":
            adapter = GitHubDelivery()
            if arguments.github_delivery_command == "pr":
                result = adapter.create_or_adopt_pr(repository=arguments.repository, head=arguments.head, base=arguments.base, candidate_sha=arguments.candidate_sha, body=arguments.body.read_text(encoding="utf-8"), operation_id=arguments.operation_id, receipt_root=arguments.receipt_root)
            elif arguments.github_delivery_command == "checks":
                result = adapter.checks(repository=arguments.repository, candidate_sha=arguments.candidate_sha, required=arguments.required)
            else:
                result = adapter.merge(
                    repository=arguments.repository, number=arguments.number,
                    expected_head=arguments.expected_head, expected_base=arguments.expected_base,
                    candidate_receipt=plan_json(arguments.candidate_receipt),
                    review=plan_json(arguments.review), checks=plan_json(arguments.checks),
                    allow=arguments.authorize)
        elif arguments.command == "intake":
            result = intake_snapshot(read_local(arguments.root), entry_key=arguments.entry)
        elif arguments.command == "plan":
            document = plan_json(arguments.file)
            result = validate_spec_plan(document) if arguments.plan_command == "validate-spec" else validate_ticket_plan(document, expected_spec_key=arguments.spec_key, expected_base_sha=arguments.base_sha)
        elif arguments.command == "skill":
            skill = load_lock(arguments.lock, roots=tuple(arguments.skill_root))[arguments.phase] if arguments.lock else resolve_local_skill(arguments.phase, roots=tuple(arguments.skill_root), config_file=arguments.config)
            result = render_prompt(phase=arguments.phase, lock=skill, trusted=plan_json(arguments.trusted), untrusted=plan_json(arguments.untrusted), schema=plan_json(arguments.schema))
        elif arguments.command == "grill":
            document = plan_json(arguments.file)
            result = resolve_grill(requirement_digest=str(document.get("requirement_digest", "")), questions=document.get("questions", []), decisions=document.get("decisions", {}), authorization=set(document.get("authorization", [])), max_rounds=int(document.get("max_rounds", 1)))
        elif arguments.command == "workspace":
            result = prepare_workspace(repository=arguments.repository, workspace_root=arguments.workspace_root, run_id=arguments.run_id, spec_key=arguments.spec_key, base_ref=arguments.base_ref)
        elif arguments.command == "candidate":
            checks_doc = plan_json(arguments.checks)
            acceptance_doc = plan_json(arguments.acceptance)
            result = verify_candidate(workspace=arguments.workspace, candidate_sha=arguments.candidate_sha, acceptance_version=arguments.acceptance_version, checks=checks_doc.get("checks", []), acceptance=acceptance_doc.get("acceptance", []))
        elif arguments.command == "review":
            result = validate_review(result=plan_json(arguments.file), candidate_sha=arguments.candidate_sha, acceptance_version=arguments.acceptance_version)
        elif arguments.command == "merge":
            result = merge_local(repository=arguments.repository, candidate_branch=arguments.candidate_branch, target_ref=arguments.target_ref, expected_target_sha=arguments.expected_target_sha, workspace_root=arguments.workspace_root, run_id=arguments.run_id)
        elif arguments.command == "delivery":
            plan = plan_json(arguments.plan)
            result = run_local_delivery(
                plan=plan,
                repository=arguments.repository,
                workspace_root=arguments.workspace_root,
                control_root=arguments.control_root,
                run_id=arguments.run_id,
                target_ref=arguments.target_ref,
            )
        elif arguments.command == "takeover":
            if arguments.takeover_command == "sdk-read":
                result = CodexAdapter().read_thread(thread_id=arguments.thread_id, repository_path=arguments.repository.resolve())
            else:
                report = inspect_takeover(load_inventory(arguments.file))
                frontier = plan_frontier(report)
                if arguments.takeover_command == "apply":
                    record = write_takeover_record(control_root=arguments.control_root, takeover_key=arguments.takeover_key, report=report, frontier=frontier)
                    action = completion_action(report)
                    result = {**record, "frontier": frontier, "action": action}
                    if action["state"] == "cleanup_pending":
                        result["cleanup"] = perform_cleanup(report)
                        if result["cleanup"]["outcome"] == "cleaned":
                            result["action"] = {**action, "state": "cleaned"}
                    # A cleanup-only takeover has no remaining implementation
                    # authority. Persist the adoption record but do not create a
                    # generic worker run merely to make status look active.
                    if action["state"] == "resume_delivery" and frontier["state"] == "planned" and arguments.brief and arguments.config:
                        launch_key = arguments.launch_key or f"takeover:{arguments.takeover_key}"
                        result["runner"] = start(
                            brief_file=arguments.brief,
                            config_file=arguments.config,
                            control_root=arguments.control_root,
                            launch_key=launch_key,
                        )
                    elif action["state"] == "resume_delivery" and (arguments.brief or arguments.config):
                        raise RunnerError("takeover_inputs_incomplete", "takeover continuation requires both --brief and --config")
                else:
                    result = {"report": report, "frontier": frontier, "action": completion_action(report)}
        elif arguments.command == "diagnose":
            if arguments.diagnostic_command == "runtime":
                result = runtime_report(runner_version=__version__, store_status=status(control_root=arguments.control_root, run_id=None))
            elif arguments.diagnostic_command == "release-build":
                subject = diagnostic_json(arguments.subject, code="invalid_release_subject")
                bodies = [diagnostic_json(path, code="invalid_release_evidence") for path in arguments.evidence]
                result = build_release_report(runner_version=__version__, subject=subject, evidence_documents=bodies, required_kinds=set(arguments.required_kind) or None)
                arguments.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
                qualification = validate_release_report(result, expected_runner_version=__version__)
                result = {"output": str(arguments.output.resolve()), "report_digest": result["report_digest"], **qualification}
            elif arguments.diagnostic_command == "package":
                result = inspect_wheel(arguments.wheel, expected_runner_version=arguments.runner_version)
            else:
                document = diagnostic_json(arguments.file, code="invalid_diagnostic_input")
                result = validate_fault_matrix(document) if arguments.diagnostic_command == "fault-matrix" else validate_release_report(document, expected_runner_version=arguments.runner_version)
        elif arguments.command == "legacy":
            result = read_legacy_database(arguments.db) if arguments.legacy_command == "read" else legacy_takeover_inventory(database=arguments.db, repository=arguments.repository)
        elif arguments.command == "fault":
            result = run_fault_matrix(seed=arguments.seed, keep_artifacts=arguments.keep_artifacts)
        else:
            result = doctor(config_file=arguments.config, control_root=arguments.control_root)
    except RunnerError as exc:
        _emit({"ok": False, "error": {"code": exc.code, "message": exc.message, "details": exc.details}})
        return 2
    _emit({"ok": True, "command": arguments.command, **result})
    return 0


if __name__ == "__main__":
    sys.exit(main())
