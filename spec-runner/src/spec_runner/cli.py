from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .errors import RunnerError
from .github_tracker import GitHubTracker
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
        if arguments.command == "start":
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
            else:
                github_result = GitHubTracker().read_issue(
                    repository=arguments.repository, number=arguments.issue, linked_numbers=arguments.linked_issue
                )
                result = {"snapshot": github_result.snapshot.public(), "relation_evidence": github_result.relation_evidence}
        else:
            result = doctor(config_file=arguments.config, control_root=arguments.control_root)
    except RunnerError as exc:
        _emit({"ok": False, "error": {"code": exc.code, "message": exc.message, "details": exc.details}})
        return 2
    _emit({"ok": True, "command": arguments.command, **result})
    return 0


if __name__ == "__main__":
    sys.exit(main())
