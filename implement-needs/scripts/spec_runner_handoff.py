"""Thin /implement-needs handoff to the separately installed Spec Runner."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def runner_command(*, brief: Path, config: Path, control_root: Path, launch_key: str) -> list[str]:
    executable = shutil.which("spec-runner")
    if executable:
        prefix = [executable]
    else:
        prefix = [sys.executable, "-m", "spec_runner.cli"]
    return [*prefix, "launch", "--brief", str(brief), "--config", str(config), "--control-root", str(control_root), "--launch-key", launch_key]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="implement-needs-runner-handoff")
    parser.add_argument("--brief", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--control-root", required=True, type=Path)
    parser.add_argument("--launch-key", required=True)
    args = parser.parse_args(argv)
    completed = subprocess.run(runner_command(brief=args.brief, config=args.config, control_root=args.control_root, launch_key=args.launch_key), check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
