"""Exercise the real control DB and launcher-log locks in one Windows run."""
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.windows_control_db_restart_probe import (
    _digest,
    _git,
    _integrity,
    _lock_holder_script,
    _terminate_known_pid,
    _write,
)
from harness.windows_launcher_log_handles_probe import _HOLDER, _invoke, _invoke_launch


def validate_report(report: dict[str, Any]) -> None:
    if report.get("evidence_kind") != "observed_windows_control_db_launcher_logs":
        raise AssertionError("report must identify the combined native Windows lock evidence")
    if report.get("platform") != "Windows":
        raise AssertionError("this probe must run on Windows")
    if report.get("control_lock_ready") is not True or report.get("log_handles_ready") is not True:
        raise AssertionError("both independent lock holders must be ready before the restart")
    if report.get("lock_attempt_error") != "control_database_busy":
        raise AssertionError("the combined locked write must fail with control_database_busy")
    if report.get("control_db_did_not_advance") is not True:
        raise AssertionError("the control database must not advance during the rejected write")
    if report.get("runner_terminated_while_both_locks_held") is not True:
        raise AssertionError("the recorded detached child must be terminated while both locks are held")
    if report.get("recovered_same_run_with_logs_held") is not True:
        raise AssertionError("the same run must recover after the DB unlock while log handles remain held")
    if report.get("rotation_error_with_logs_held") != "launcher_log_rotation_failed":
        raise AssertionError("rotation must remain observable while the independent log handles are held")
    if report.get("rotation_after_log_release") is not True or report.get("rotation_replayed") is not True:
        raise AssertionError("the same rotation intent must replay successfully after log release")
    if report.get("control_db_exists_after") is not True or report.get("control_db_integrity_after") != "ok":
        raise AssertionError("combined recovery must preserve an intact control database")
    if report.get("final_state") != "completed":
        raise AssertionError("combined recovery must finish the original run")


def _config(root: Path, repository: Path, control: Path) -> Path:
    path = root / "runner.json"
    path.write_text(json.dumps({
        "schema_version": "spec-runner-config/v1",
        "repository_path": str(repository),
        "target_ref": "HEAD",
        "artifact_root": "artifacts",
        "execution_backend": "deterministic_test",
        "allowed_stages": ["example"],
        "model": {"name": "deterministic-test", "effort": "none"},
        "authorization": {"artifact_roots": ["artifacts"]},
    }, ensure_ascii=False), encoding="utf-8")
    return path


def run_probe(output: Path | None = None) -> dict[str, Any]:
    if platform.system() != "Windows":
        raise RuntimeError("native Windows combined lock probe requires Windows")
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
        _write(brief, "# Windows combined control and launcher lock acceptance\n")
        control = root / "控制 root"
        config = _config(root, repository, control)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(source_root)
        env["SPEC_RUNNER_FAULT_POINT"] = "after_first_artifact"
        env["SPEC_RUNNER_TEST_LEASE_STALE_AFTER_SECONDS"] = "0"
        launch_code, launched, wrapper_pid = _invoke_launch([
            "launch", "--brief", str(brief), "--config", str(config),
            "--control-root", str(control), "--launch-key", "windows-combined-locks",
        ], root, env)
        if launch_code != 0:
            raise RuntimeError(f"public detached launch failed: {launched}")
        run_id = str(launched["run_id"])
        child_pid = int(launched["pid"])
        ready = control / "faults" / f"{run_id}.after_first_artifact.ready"
        stdout_path = control / "launcher-logs" / f"{run_id}.stdout.log"
        stderr_path = control / "launcher-logs" / f"{run_id}.stderr.log"
        for _ in range(200):
            if ready.is_file() and stdout_path.is_file() and stderr_path.is_file():
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("detached Runner or launcher logs did not become ready")
        database = control / "spec-runner.sqlite3"
        holder_ready = control / "combined-db-holder.ready"
        holder_release = control / "combined-db-holder.release"
        db_holder = subprocess.Popen([
            sys.executable, "-c", _lock_holder_script(), str(database),
            str(holder_ready), str(holder_release),
        ], cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log_ready = root / "combined-log-holder.ready"
        log_release = root / "combined-log-holder.release"
        log_holder = subprocess.Popen([
            sys.executable, "-c", _HOLDER, str(stdout_path), str(stderr_path),
            str(log_ready), str(log_release),
        ], cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        termination_code = 1
        try:
            for _ in range(200):
                if holder_ready.is_file() and log_ready.is_file():
                    break
                if db_holder.poll() is not None or log_holder.poll() is not None:
                    raise RuntimeError("one of the combined lock holders exited before becoming ready")
                time.sleep(0.05)
            if not holder_ready.is_file() or not log_ready.is_file():
                raise RuntimeError("combined lock holders did not become ready")
            before_digest = _digest(database)
            started = time.monotonic()
            lock_code, lock_result = _invoke([
                "drive", "--brief", str(brief), "--config", str(config),
                "--control-root", str(control), "--launch-key", "windows-combined-locks",
            ], root, env)
            elapsed = time.monotonic() - started
            after_digest = _digest(database)
            lock_error = str(lock_result.get("error", {}).get("code") or "")
            if lock_code != 2 or lock_error != "control_database_busy":
                raise RuntimeError("combined lock attempt did not fail closed at the control DB boundary")
            if after_digest != before_digest:
                raise RuntimeError("control DB changed during the combined rejected write")
            _write(control / "faults" / f"{run_id}.after_first_artifact.continue", "continue\n")
            termination_code = _terminate_known_pid(child_pid)
            if termination_code != 0:
                raise RuntimeError("recorded detached Runner could not be terminated safely")
            # Release only the DB lock. The log holder remains active while the
            # same run is recovered and while rotation is intentionally rejected.
            holder_release.touch()
            db_holder.wait(timeout=10)
            recovery_env = dict(env)
            recovery_env.pop("SPEC_RUNNER_FAULT_POINT", None)
            drive_code, recovered = _invoke([
                "drive", "--brief", str(brief), "--config", str(config),
                "--control-root", str(control), "--launch-key", "windows-combined-locks",
            ], root, recovery_env)
            recovered_same_run = (
                drive_code == 0
                and recovered.get("run", {}).get("run_id") == run_id
                and recovered.get("run", {}).get("state") == "completed"
            )
            rotation_args = [
                "logs", "rotate", "--control-root", str(control), "--run-id", run_id,
                "--rotation-key", "windows-combined-01",
            ]
            rotation_code, rotation_failure = _invoke(rotation_args, root, recovery_env)
            rotation_error = str(rotation_failure.get("error", {}).get("code") or "")
        finally:
            if not holder_release.exists():
                holder_release.touch()
            if not log_release.exists():
                log_release.touch()
            db_holder.wait(timeout=10)
            log_holder.wait(timeout=10)
        retry_code, rotation_success = _invoke(rotation_args, root, recovery_env)
        rotated = rotation_success.get("rotated_logs", {}) if isinstance(rotation_success, dict) else {}
        readable = retry_code == 0 and all(
            Path(str(rotated.get(name, ""))).is_file()
            for name in ("stdout", "stderr")
        )
        report = {
            "schema_version": "spec-runner-windows-control-db-launcher-logs/v1",
            "run_marker": "SRAC-20260926-windows-control-db-launcher-logs-01",
            "evidence_kind": "observed_windows_control_db_launcher_logs",
            "platform": platform.system(),
            "python": platform.python_version(),
            "wrapper_pid": wrapper_pid,
            "child_pid": child_pid,
            "db_holder_pid": db_holder.pid,
            "log_holder_pid": log_holder.pid,
            "run_id": run_id,
            "control_lock_ready": holder_ready.is_file(),
            "log_handles_ready": log_ready.is_file(),
            "lock_attempt_error": lock_error,
            "lock_attempt_elapsed_seconds": round(elapsed, 3),
            "control_db_digest_before": before_digest,
            "control_db_digest_during": after_digest,
            "control_db_did_not_advance": before_digest == after_digest,
            "runner_terminated_while_both_locks_held": termination_code == 0,
            "recovered_same_run_with_logs_held": recovered_same_run,
            "rotation_code_with_logs_held": rotation_code,
            "rotation_error_with_logs_held": rotation_error,
            "rotation_after_log_release": retry_code == 0,
            "rotation_replayed": rotation_success.get("replayed") is True,
            "rotated_logs_readable": readable,
            "final_state": recovered.get("run", {}).get("state"),
            "control_db_exists_after": database.is_file(),
            "control_db_integrity_after": _integrity(database) if database.is_file() else "missing",
            "unverified": [
                "live GitHub side-effect reconciliation",
                "source-thread takeover",
                "remaining #266 and project-level L3-L5 gates",
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
    report = run_probe(args.output)
    print(json.dumps({"report": str(args.output) if args.output else None, "run_id": report["run_id"], "final_state": report["final_state"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
