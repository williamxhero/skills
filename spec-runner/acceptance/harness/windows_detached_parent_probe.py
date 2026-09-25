"""Observe the public detached launch boundary on the native Windows host."""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def _git(repository: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True, text=True)


def _invoke(source_root: Path, args: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, dict[str, Any]]:
    process = subprocess.run(
        [sys.executable, "-m", "spec_runner.cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if not process.stdout.strip():
        raise RuntimeError("public CLI returned no JSON")
    return process.returncode, json.loads(process.stdout)


def validate_report(report: dict[str, Any]) -> None:
    if report.get("evidence_kind") != "observed_windows_native_detached_parent":
        raise AssertionError("report must identify native Windows detached-parent evidence")
    if report.get("platform") != "Windows":
        raise AssertionError("this probe must run on Windows")
    if report.get("wrapper_exit_code") != 0 or report.get("parent_exited_before_continue") is not True:
        raise AssertionError("the launch wrapper must exit before child continuation")
    if int(report.get("wrapper_pid", 0)) == int(report.get("child_pid", 0)):
        raise AssertionError("wrapper and detached child must have distinct PIDs")
    if report.get("ready_after_wrapper_exit") is not True or report.get("final_state") != "completed":
        raise AssertionError("detached child must survive wrapper exit and finish after continuation")


def run_probe(output: Path | None = None) -> dict[str, Any]:
    if platform.system() != "Windows":
        raise RuntimeError("native Windows detached-parent probe requires Windows")
    source_root = Path(__file__).resolve().parents[2] / "src"
    marker = "SRAC-20260925-windows-detached-parent"
    with tempfile.TemporaryDirectory(prefix="spec runner 中文 ") as directory:
        root = Path(directory)
        repository = root / "仓库 with spaces"
        repository.mkdir()
        _git(repository, "init", "-q")
        _git(repository, "config", "user.name", "Acceptance")
        _git(repository, "config", "user.email", "acceptance@example.invalid")
        _write(repository / "tracked.txt", "base\n")
        _git(repository, "add", "tracked.txt")
        _git(repository, "commit", "-qm", "base")
        brief = root / "brief.md"
        _write(brief, "# Windows detached parent acceptance\n")
        control = root / "控制 root"
        config = root / "runner.json"
        config.write_text(json.dumps({
            "schema_version": "spec-runner-config/v1",
            "repository_path": str(repository),
            "target_ref": "HEAD",
            "artifact_root": "artifacts",
            "execution_backend": "deterministic_test",
            "allowed_stages": ["example"],
            "model": {"name": "deterministic-test", "effort": "none"},
            "authorization": {"artifact_roots": ["artifacts"]},
        }, ensure_ascii=False), encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(source_root)
        env["SPEC_RUNNER_FAULT_POINT"] = "after_first_artifact"
        launch_process = subprocess.Popen(
            [sys.executable, "-m", "spec_runner.cli", "launch", "--brief", str(brief), "--config", str(config), "--control-root", str(control), "--launch-key", "windows-detached-parent"],
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        wrapper_pid = launch_process.pid
        launch_stdout, launch_stderr = launch_process.communicate(timeout=30)
        if launch_process.returncode != 0 or not launch_stdout.strip():
            raise RuntimeError("public detached launch failed")
        started = json.loads(launch_stdout)
        run_id = str(started["run_id"])
        child_pid = int(started["pid"])
        ready = control / "faults" / f"{run_id}.after_first_artifact.ready"
        for _ in range(200):
            if ready.is_file():
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("detached child did not reach the durable pause marker")
        status_code, waiting = _invoke(source_root, ["status", "--control-root", str(control), "--run-id", run_id], root, env)
        runtime = waiting.get("runtime") if isinstance(waiting, dict) else None
        _write(control / "faults" / f"{run_id}.after_first_artifact.continue", "continue\n")
        if status_code != 0 or not isinstance(runtime, dict) or runtime.get("pid") != child_pid:
            raise RuntimeError("detached child identity was not readable after wrapper exit")
        final: dict[str, Any] = waiting
        for _ in range(200):
            status_code, final = _invoke(source_root, ["status", "--control-root", str(control), "--run-id", run_id], root, env)
            if status_code == 0 and final["run"]["state"] == "completed":
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("detached child did not complete after continuation")
        report = {
            "schema_version": "spec-runner-windows-native-detached-parent/v1",
            "run_marker": marker,
            "evidence_kind": "observed_windows_native_detached_parent",
            "platform": platform.system(),
            "python": platform.python_version(),
            "wrapper_pid": wrapper_pid,
            "child_pid": child_pid,
            "wrapper_exit_code": launch_process.returncode,
            "parent_exited_before_continue": True,
            "ready_after_wrapper_exit": ready.is_file(),
            "status_after_wrapper_exit": waiting["run"]["state"],
            "final_state": final["run"]["state"],
            "runtime_pid_readback": runtime["pid"],
            "unverified": [
                "Windows control DB and launcher log recovery across host/process restart",
                "live SDK process restart",
                "source-thread takeover",
                "GitHub merge queue",
                "complete A-J recovery acceptance",
            ],
        }
    validate_report(report)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_probe(output=args.output)
    print(json.dumps({"report": str(args.output) if args.output else None, "child_pid": report["child_pid"], "final_state": report["final_state"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())