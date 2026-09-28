"""Public /implement-needs handoff for the independently installed Spec Runner."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


HANDOFF_CONTRACT_VERSION = "implement-needs-handoff/v2"
RUNNER_CLI_SCHEMA_VERSION = "spec-runner-cli/v1"
OPERATIONS = ("launch", "status", "pause", "resume", "answer", "cancel", "diagnose")


def _runner_prefix() -> list[str]:
    executable = shutil.which("spec-runner")
    return [executable] if executable else [sys.executable, "-m", "spec_runner.cli"]


def _required(value: Path | str | None, name: str) -> Path | str:
    if value is None or (isinstance(value, str) and not value):
        raise ValueError(f"{name} is required")
    return value


def runner_command(
    operation: str = "launch",
    *,
    brief: Path | None = None,
    config: Path | None = None,
    control_root: Path,
    launch_key: str | None = None,
    run_id: str | None = None,
    question_id: str | None = None,
    value: str | None = None,
    handshake_timeout: float = 10.0,
) -> list[str]:
    """Build one command from the public Runner control surface."""
    if operation not in OPERATIONS:
        raise ValueError(f"unsupported handoff operation: {operation}")
    command = [*_runner_prefix(), operation]
    if operation in {"launch", "resume"}:
        command.extend(
            [
                "--brief",
                str(_required(brief, "brief")),
                "--config",
                str(_required(config, "config")),
                "--control-root",
                str(control_root),
                "--launch-key",
                str(_required(launch_key, "launch_key")),
            ]
        )
        if operation == "launch":
            command.extend(["--handshake-timeout", str(handshake_timeout)])
    elif operation == "diagnose":
        command.extend(["--config", str(_required(config, "config")), "--control-root", str(control_root)])
    else:
        command.extend(["--control-root", str(control_root)])
        if operation == "status":
            command.extend(["--run-id", str(_required(run_id, "run_id"))])
        elif operation in {"pause", "cancel", "answer"}:
            command.extend(["--run-id", str(_required(run_id, "run_id"))])
            if operation == "answer":
                command.extend(
                    [
                        "--question-id",
                        str(_required(question_id, "question_id")),
                        "--value",
                        str(_required(value, "value")),
                    ]
                )
    return command


def _decode_output(raw: bytes, stream: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"Runner {stream} was not UTF-8") from exc


def _parse_payload(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Runner did not return JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Runner returned a non-object JSON value")
    return payload


def invoke_public_runner(
    operation: str = "launch",
    *,
    brief: Path | None = None,
    config: Path | None = None,
    control_root: Path,
    launch_key: str | None = None,
    run_id: str | None = None,
    question_id: str | None = None,
    value: str | None = None,
    handshake_timeout: float = 10.0,
) -> tuple[int, dict[str, Any]]:
    """Invoke and validate one public Runner response."""
    command = runner_command(
        operation,
        brief=brief,
        config=config,
        control_root=control_root,
        launch_key=launch_key,
        run_id=run_id,
        question_id=question_id,
        value=value,
        handshake_timeout=handshake_timeout,
    )
    try:
        completed = subprocess.run(command, check=False, capture_output=True)
    except OSError as exc:
        return 1, {
            "schema_version": HANDOFF_CONTRACT_VERSION,
            "status": "not_verified",
            "phase": "handoff",
            "reason": {"code": "runner_unavailable", "message": str(exc)},
            "evidence": {"operation": operation, "command": command},
        }
    stdout = _decode_output(completed.stdout, "stdout")
    stderr = _decode_output(completed.stderr, "stderr")
    try:
        payload = _parse_payload(stdout)
    except RuntimeError as exc:
        return completed.returncode or 1, {
            "schema_version": HANDOFF_CONTRACT_VERSION,
            "status": "blocked",
            "phase": "handoff",
            "reason": {"code": "runner_response_invalid", "message": str(exc)},
            "evidence": {"operation": operation, "stderr": stderr},
        }
    if payload.get("schema_version") != RUNNER_CLI_SCHEMA_VERSION:
        return 1, {
            "schema_version": HANDOFF_CONTRACT_VERSION,
            "status": "blocked",
            "phase": "handoff",
            "reason": {
                "code": "runner_contract_mismatch",
                "expected": RUNNER_CLI_SCHEMA_VERSION,
                "observed": payload.get("schema_version"),
            },
            "evidence": {"operation": operation, "stderr": stderr, "runner_payload": payload},
        }
    if operation in {"launch", "resume"}:
        returned_run = payload.get("run")
        if isinstance(returned_run, dict) and returned_run.get("launch_key") not in {None, launch_key}:
            return 1, {
                "schema_version": HANDOFF_CONTRACT_VERSION,
                "status": "blocked",
                "phase": "handoff",
                "reason": {
                    "code": "runner_launch_identity_mismatch",
                    "expected_launch_key": launch_key,
                    "observed_launch_key": returned_run.get("launch_key"),
                },
                "evidence": {"operation": operation, "runner_payload": payload},
            }
    if completed.returncode != 0 and payload.get("status") in {"completed", "success"}:
        return completed.returncode, {
            "schema_version": HANDOFF_CONTRACT_VERSION,
            "status": "blocked",
            "phase": "handoff",
            "reason": {
                "code": "runner_exit_conflicts_with_completion",
                "exit_code": completed.returncode,
            },
            "evidence": {"operation": operation, "stderr": stderr, "runner_payload": payload},
        }
    payload["handoff_contract_version"] = HANDOFF_CONTRACT_VERSION
    if stderr:
        payload["handoff_stderr"] = stderr
    return completed.returncode, payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="implement-needs-runner-handoff")
    parser.add_argument("operation", nargs="?", choices=OPERATIONS, default="launch")
    parser.add_argument("--brief", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--launch-key")
    parser.add_argument("--run-id")
    parser.add_argument("--question-id")
    parser.add_argument("--value")
    parser.add_argument("--handshake-timeout", type=float, default=10.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        code, payload = invoke_public_runner(
            args.operation,
            brief=args.brief,
            config=args.config,
            control_root=args.control_root,
            launch_key=args.launch_key,
            run_id=args.run_id,
            question_id=args.question_id,
            value=args.value,
            handshake_timeout=args.handshake_timeout,
        )
    except (RuntimeError, ValueError) as exc:
        payload = {
            "schema_version": HANDOFF_CONTRACT_VERSION,
            "status": "blocked",
            "phase": "handoff",
            "reason": {"code": "handoff_input_invalid", "message": str(exc)},
        }
        code = 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
