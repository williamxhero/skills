from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .errors import RunnerError
from .github_tracker import GitHubTracker
from .delivery import merge_local, prepare_workspace, validate_review, verify_candidate
from .diagnostics import load_json as diagnostic_json, runtime_report, validate_fault_matrix, validate_release_report
from .matt import load_lock, render_prompt, resolve_grill
from .plans import intake_snapshot, load_json as plan_json, validate_spec_plan, validate_ticket_plan
from .takeover import completion_action, inspect_takeover, load_inventory
from .tracker import publish_local, read_local
from .workflow import control, doctor, launch, resume, start, status

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
    skill_parser = subparsers.add_parser("skill", help="render a pinned Skill prompt without publishing")
    skill_sub = skill_parser.add_subparsers(dest="skill_command", required=True)
    skill_render = skill_sub.add_parser("render")
    skill_render.add_argument("--lock", required=True, type=Path)
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
    takeover_parser = subparsers.add_parser("takeover", help="inventory and safely adopt an arbitrary-stage delivery")
    takeover_sub = takeover_parser.add_subparsers(dest="takeover_command", required=True)
    takeover_inspect = takeover_sub.add_parser("inspect")
    takeover_inspect.add_argument("--file", required=True, type=Path)
    diagnostic_parser = subparsers.add_parser("diagnose", help="validate fault and release evidence without LLM calls")
    diagnostic_sub = diagnostic_parser.add_subparsers(dest="diagnostic_command", required=True)
    for name in ("fault-matrix", "release-report"):
        item = diagnostic_sub.add_parser(name)
        item.add_argument("--file", required=True, type=Path)
    diagnostic_sub.choices["release-report"].add_argument("--runner-version", default=__version__)
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
                run_id=arguments.run_id,
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
        elif arguments.command == "intake":
            result = intake_snapshot(read_local(arguments.root), entry_key=arguments.entry)
        elif arguments.command == "plan":
            document = plan_json(arguments.file)
            result = validate_spec_plan(document) if arguments.plan_command == "validate-spec" else validate_ticket_plan(document, expected_spec_key=arguments.spec_key, expected_base_sha=arguments.base_sha)
        elif arguments.command == "skill":
            locks = load_lock(arguments.lock, roots=tuple(arguments.skill_root))
            result = render_prompt(phase=arguments.phase, lock=locks[arguments.phase], trusted=plan_json(arguments.trusted), untrusted=plan_json(arguments.untrusted), schema=plan_json(arguments.schema))
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
        elif arguments.command == "takeover":
            report = inspect_takeover(load_inventory(arguments.file))
            result = {"report": report, "action": completion_action(report)}
        elif arguments.command == "diagnose":
            document = diagnostic_json(arguments.file, code="invalid_diagnostic_input")
            result = validate_fault_matrix(document) if arguments.diagnostic_command == "fault-matrix" else validate_release_report(document, expected_runner_version=arguments.runner_version)
        else:
            result = doctor(config_file=arguments.config, control_root=arguments.control_root)
    except RunnerError as exc:
        _emit({"ok": False, "error": {"code": exc.code, "message": exc.message, "details": exc.details}})
        return 2
    _emit({"ok": True, "command": arguments.command, **result})
    return 0


if __name__ == "__main__":
    sys.exit(main())
