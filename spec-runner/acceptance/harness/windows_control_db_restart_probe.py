"""Exercise real Windows control-DB locking across detached Runner recovery."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from spec_runner.store import _process_alive


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def _git(repository: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True, text=True)


def _invoke(args: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, dict[str, Any]]:
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


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _integrity(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        connection.close()


def _lock_holder_script() -> str:
    return r'''
import sqlite3
import sys
import time
from pathlib import Path

db_path, ready_path, release_path = map(Path, sys.argv[1:])
connection = sqlite3.connect(db_path, timeout=0.1, isolation_level=None)
connection.execute("BEGIN EXCLUSIVE")
ready_path.write_text("exclusive-lock\n", encoding="utf-8")
while not release_path.exists():
    time.sleep(0.02)
connection.execute("ROLLBACK")
connection.close()
'''


def _terminate_known_pid(pid: int) -> int:
    result = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        return result.returncode
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            return 0
        time.sleep(0.05)
    return 1


def validate_report(report: dict[str, Any]) -> None:
    if report.get("evidence_kind") != "observed_windows_control_db_restart":
        raise AssertionError("report must identify the native Windows control DB restart evidence")
    if report.get("platform") != "Windows":
        raise AssertionError("this probe must run on Windows")
    if report.get("lock_acquired_before_runner_termination") is not True:
        raise AssertionError("the real control DB lock must precede Runner termination")
    if report.get("lock_write_attempt_error") != "control_database_busy":
        raise AssertionError("the public drive command must report a structured bounded lock error")
    if not isinstance(report.get("lock_write_attempt_elapsed_seconds"), (int, float)):
        raise AssertionError("the public lock attempt must record its bounded wait")
    if report.get("lock_write_attempt_elapsed_seconds", 0) > 10:
        raise AssertionError("the public lock attempt exceeded its bounded wait")
    if report.get("runner_terminated_while_lock_held") is not True:
        raise AssertionError("the known detached Runner must be terminated while the lock is held")
    if report.get("control_db_exists_after") is not True or report.get("control_db_integrity_after") != "ok":
        raise AssertionError("recovery must preserve an intact control DB")
    if report.get("recovered_same_run") is not True or report.get("final_state") != "completed":
        raise AssertionError("drive must recover the same run to completion")
    if report.get("launcher_logs_readable") is not True:
        raise AssertionError("launcher logs must remain readable after recovery")


def run_probe(output: Path | None = None) -> dict[str, Any]:
    if platform.system() != "Windows":
        raise RuntimeError("native Windows control DB probe requires Windows")
    source_root = Path(__file__).resolve().parents[2] / "src"
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
        _write(brief, "# Windows control DB restart acceptance\n")
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
        env["SPEC_RUNNER_TEST_LEASE_STALE_AFTER_SECONDS"] = "0"
        launch_process = subprocess.Popen(
            [sys.executable, "-m", "spec_runner.cli", "launch", "--brief", str(brief), "--config", str(config), "--control-root", str(control), "--launch-key", "windows-control-db-restart"],
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        wrapper_pid = launch_process.pid
        launch_stdout, _ = launch_process.communicate(timeout=30)
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
            raise RuntimeError("detached child did not reach its durable pause")
        database = control / "spec-runner.sqlite3"
        if not database.is_file():
            raise RuntimeError("the detached Runner did not create its control DB")
        holder_ready = control / "lock-holder.ready"
        holder_release = control / "lock-holder.release"
        holder = subprocess.Popen(
            [sys.executable, "-c", _lock_holder_script(), str(database), str(holder_ready), str(holder_release)],
            cwd=root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(200):
                if holder_ready.is_file():
                    break
                if holder.poll() is not None:
                    raise RuntimeError("SQLite lock holder exited before acquiring the lock")
                time.sleep(0.05)
            else:
                raise RuntimeError("SQLite lock holder did not acquire the lock")
            lock_digest_before = _digest(database)
            lock_attempt_started = time.monotonic()
            lock_attempt_code, lock_attempt = _invoke(
                ["drive", "--brief", str(brief), "--config", str(config), "--control-root", str(control), "--launch-key", "windows-control-db-restart"],
                root,
                env,
            )
            lock_attempt_elapsed = time.monotonic() - lock_attempt_started
            lock_attempt_error = str(lock_attempt.get("error", {}).get("code") or "")
            if lock_attempt_code != 2 or lock_attempt_error != "control_database_busy":
                raise RuntimeError("public drive did not fail closed with a structured control DB lock error")
            _write(control / "faults" / f"{run_id}.after_first_artifact.continue", "continue\n")
            time.sleep(0.75)
            termination_code = _terminate_known_pid(child_pid)
            runner_terminated_while_lock_held = termination_code == 0
            # The lock is intentionally allowed to block the control DB read
            # path.  Readback resumes only after the holder releases it.
        finally:
            holder_release.touch()
            holder.wait(timeout=10)
        recovery_env = dict(env)
        recovery_env.pop("SPEC_RUNNER_FAULT_POINT", None)
        # Keep the bounded deterministic stale-owner setting for this recovery
        # probe. The killed Runner's heartbeat is intentionally recent, so the
        # normal production five-second lease window would mask the restart
        # path that this acceptance test is exercising.
        status_code_after_unlock, before = _invoke(["status", "--control-root", str(control), "--run-id", run_id], root, recovery_env)
        if status_code_after_unlock != 0:
            raise RuntimeError("control DB did not become readable after lock release")
        drive_code, recovered = _invoke(["drive", "--brief", str(brief), "--config", str(config), "--control-root", str(control), "--launch-key", "windows-control-db-restart"], root, recovery_env)
        final_run = recovered.get("run", {})
        database_after = database.is_file()
        logs = list((control / "launcher-logs").glob("*.log"))
        launcher_logs_readable = bool(logs) and all(path.read_text(encoding="utf-8", errors="replace") is not None for path in logs)
        report = {
            "schema_version": "spec-runner-windows-control-db-restart/v1",
            "run_marker": "SRAC-20260925-windows-control-db-restart",
            "evidence_kind": "observed_windows_control_db_restart",
            "platform": platform.system(),
            "python": platform.python_version(),
            "wrapper_pid": wrapper_pid,
            "child_pid": child_pid,
            "lock_holder_pid": holder.pid,
            "run_id": run_id,
            "lock_acquired_before_runner_termination": holder_ready.is_file(),
            "lock_write_attempt_error": lock_attempt_error,
            "lock_write_attempt_elapsed_seconds": round(lock_attempt_elapsed, 3),
            "runner_terminated_while_lock_held": runner_terminated_while_lock_held,
            "termination_exit_code": termination_code,
            "status_before_recovery": before["run"]["state"],
            "drive_exit_code": drive_code,
            "recovered_same_run": final_run.get("run_id") == run_id,
            "final_state": final_run.get("state"),
            "control_db_exists_after": database_after,
            "control_db_integrity_after": _integrity(database) if database_after else "missing",
            "control_db_digest_before_lock": lock_digest_before,
            "control_db_digest_after_recovery": _digest(database) if database_after else None,
            "launcher_logs_readable": launcher_logs_readable,
            "launcher_log_count": len(logs),
            "unverified": [
                "SQLite locking on non-Windows hosts",
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
    print(json.dumps({"report": str(args.output) if args.output else None, "run_id": report["run_id"], "final_state": report["final_state"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())