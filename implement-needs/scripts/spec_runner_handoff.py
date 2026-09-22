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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="implement-needs-runner-handoff")
    parser.add_argument("--brief", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--launch-key")
    parser.add_argument("--takeover-file", type=Path)
    parser.add_argument("--takeover-key")
    args = parser.parse_args(argv)
    if args.takeover_file is not None and not args.takeover_key:
        parser.error("--takeover-file requires --takeover-key")
    if args.takeover_file is None and (args.brief is None or args.config is None or not args.launch_key):
        parser.error("new-run handoff requires --brief, --config, and --launch-key")
    completed = subprocess.run(runner_command(brief=args.brief, config=args.config, control_root=args.control_root, launch_key=args.launch_key, takeover_file=args.takeover_file, takeover_key=args.takeover_key), check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
