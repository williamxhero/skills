"""Thin /implement-needs handoff to the separately installed Spec Runner."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def runner_command(*, brief: Path | None = None, config: Path | None = None, control_root: Path, launch_key: str | None = None, takeover_file: Path | None = None, takeover_key: str | None = None) -> list[str]:
    executable = shutil.which("spec-runner")
    if executable:
        prefix = [executable]
    else:
        prefix = [sys.executable, "-m", "spec_runner.cli"]
    if takeover_file is not None:
        command = [*prefix, "takeover", "apply", "--file", str(takeover_file), "--control-root", str(control_root), "--takeover-key", str(takeover_key)]
        if brief is not None:
            command.extend(["--brief", str(brief)])
        if config is not None:
            command.extend(["--config", str(config)])
        if launch_key is not None:
            command.extend(["--launch-key", launch_key])
        return command
    if brief is None or config is None or launch_key is None:
        raise ValueError("new-run handoff requires brief, config, and launch_key")
    return [*prefix, "launch", "--brief", str(brief), "--config", str(config), "--control-root", str(control_root), "--launch-key", launch_key]


def takeover_discovery_command(*, repository: str, issue: int, workspace: Path,
                               target_ref: str, control_root: Path, takeover_key: str,
                               output: Path, artifact_roots: list[Path],
                               required_checks: list[str], source_thread_id: str | None = None) -> list[str]:
    executable = shutil.which("spec-runner")
    prefix = [executable] if executable else [sys.executable, "-m", "spec_runner.cli"]
    command = [*prefix, "takeover", "discover", "--repository", repository,
               "--issue", str(issue), "--workspace", str(workspace),
               "--target-ref", target_ref, "--control-root", str(control_root),
               "--takeover-key", takeover_key, "--output", str(output)]
    for root in artifact_roots:
        command.extend(["--artifact-root", str(root)])
    for check in required_checks:
        command.extend(["--required-check", check])
    if source_thread_id:
        command.extend(["--thread-id", source_thread_id])
    return command


def takeover_apply_command(*, discovery: Path, control_root: Path,
                           takeover_key: str, brief: Path, config: Path,
                           launch_key: str | None = None) -> list[str]:
    executable = shutil.which("spec-runner")
    prefix = [executable] if executable else [sys.executable, "-m", "spec_runner.cli"]
    command = [*prefix, "takeover", "apply", "--discovery", str(discovery),
               "--control-root", str(control_root), "--takeover-key", takeover_key,
               "--brief", str(brief), "--config", str(config)]
    if launch_key:
        command.extend(["--launch-key", launch_key])
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="implement-needs-runner-handoff")
    parser.add_argument("--brief", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--launch-key")
    parser.add_argument("--takeover-file", type=Path)
    parser.add_argument("--takeover-key")
    parser.add_argument("--takeover-repository")
    parser.add_argument("--umbrella-issue", type=int)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--target-ref")
    parser.add_argument("--artifact-root", action="append", type=Path, default=[])
    parser.add_argument("--required-check", action="append", default=[])
    parser.add_argument("--source-thread-id")
    parser.add_argument("--discovery-output", type=Path)
    args = parser.parse_args(argv)
    if args.takeover_file is not None and not args.takeover_key:
        parser.error("--takeover-file requires --takeover-key")
    discovery_values = (args.takeover_repository, args.umbrella_issue, args.workspace, args.target_ref)
    if any(value is not None for value in discovery_values) and not all(value is not None for value in discovery_values):
        parser.error("GitHub takeover requires --takeover-repository, --umbrella-issue, --workspace, and --target-ref")
    if args.takeover_repository is not None:
        if args.takeover_file is not None:
            parser.error("GitHub takeover cannot be combined with --takeover-file")
        if not args.takeover_key or args.brief is None or args.config is None:
            parser.error("GitHub takeover requires --takeover-key, --brief, and --config")
        output = args.discovery_output or (args.control_root / "takeover-discoveries" / f"{args.takeover_key}.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        roots = args.artifact_root or [args.control_root]
        discovery = subprocess.run(
            takeover_discovery_command(
                repository=args.takeover_repository, issue=args.umbrella_issue,
                workspace=args.workspace, target_ref=args.target_ref,
                control_root=args.control_root, takeover_key=args.takeover_key,
                output=output, artifact_roots=roots,
                required_checks=args.required_check, source_thread_id=args.source_thread_id,
            ), check=False,
        )
        if discovery.returncode:
            return discovery.returncode
        applied = subprocess.run(
            takeover_apply_command(
                discovery=output, control_root=args.control_root,
                takeover_key=args.takeover_key, brief=args.brief,
                config=args.config, launch_key=args.launch_key,
            ), check=False,
        )
        return applied.returncode
    if args.takeover_file is None and (args.brief is None or args.config is None or not args.launch_key):
        parser.error("new-run handoff requires --brief, --config, and --launch-key")
    completed = subprocess.run(runner_command(brief=args.brief, config=args.config, control_root=args.control_root, launch_key=args.launch_key, takeover_file=args.takeover_file, takeover_key=args.takeover_key), check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
