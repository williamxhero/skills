"""Observe both detached launcher logs under an independent Windows handle."""
from __future__ import annotations

import ctypes
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any


_HOLDER = r'''
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

stdout_path, stderr_path, ready, release = map(Path, sys.argv[1:])
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
create_file = kernel32.CreateFileW
create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
create_file.restype = wintypes.HANDLE
handles = []
for path in (stdout_path, stderr_path):
    handle = create_file(str(path), 0x80000000, 0x00000001 | 0x00000002, None, 3, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    handles.append(handle)
ready.write_text("ready", encoding="utf-8")
while not release.exists():
    time.sleep(0.01)
for handle in handles:
    if not kernel32.CloseHandle(wintypes.HANDLE(handle)):
        raise ctypes.WinError(ctypes.get_last_error())
'''


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def _git(repository: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True, text=True)


def _invoke(args: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, dict[str, Any]]:
    process = subprocess.run(
        [sys.executable, "-m", "spec_runner.cli", *args], cwd=cwd, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    if not process.stdout.strip():
        raise RuntimeError(f"public CLI returned no JSON: {process.stderr[-1000:]}")
    return process.returncode, json.loads(process.stdout)


def _invoke_launch(args: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, dict[str, Any], int]:
    process = subprocess.Popen(
        [sys.executable, "-m", "spec_runner.cli", *args], cwd=cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace",
    )
    wrapper_pid = process.pid
    stdout, stderr = process.communicate(timeout=30)
    if not stdout.strip():
        raise RuntimeError(f"public CLI returned no JSON: {stderr[-1000:]}")
    return process.returncode, json.loads(stdout), wrapper_pid


def _terminate_known_pid(pid: int) -> int:
    # taskkill cannot terminate every DETACHED_PROCESS child on this host.
    # Use the exact durable PID with the Win32 process API instead of broad
    # name-based termination or a recursive process-tree kill.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    terminate_process = kernel32.TerminateProcess
    terminate_process.argtypes = [wintypes.HANDLE, wintypes.UINT]
    terminate_process.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    handle = open_process(0x0001, False, pid)  # PROCESS_TERMINATE
    if not handle:
        # ERROR_INVALID_PARAMETER means the recorded process already exited.
        return 0 if ctypes.get_last_error() == 87 else 1
    try:
        if not terminate_process(handle, 1):
            return 1
    finally:
        close_handle(handle)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        listing = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        ).stdout
        rows = [line for line in listing.splitlines() if line.startswith('"')]
        if not rows:
            return 0
        time.sleep(0.05)
    return 1


def _rotate(path: Path) -> tuple[bool, str | None]:
    target = path.with_name(path.name + ".rotated")
    try:
        if target.exists():
            target.unlink()
        os.replace(path, target)
    except OSError as exc:
        return False, type(exc).__name__
    return True, None


def validate_report(report: dict[str, Any]) -> None:
    if report.get("evidence_kind") != "observed_windows_launcher_log_handles" or report.get("platform") != "Windows":
        raise AssertionError("report must identify native Windows launcher-log handle evidence")
    if report.get("wrapper_exit_code") != 0 or report.get("child_pid") == report.get("wrapper_pid"):
        raise AssertionError("public launch must return distinct wrapper and detached child identities")
    if report.get("holder_ready") is not True or report.get("rotation_blocked_while_held") is not True:
        raise AssertionError("an independent process must hold both launcher logs and block their rotation")
    if report.get("runner_terminated_while_held") is not True:
        raise AssertionError("the recorded detached Runner must be terminated while the log handles are held")
    if report.get("recovered_same_run_while_log_handles_held") is not True:
        raise AssertionError("public drive must recover the same run while independent log handles remain held")
    if report.get("public_rotation_error") != "launcher_log_rotation_failed":
        raise AssertionError("the public rotation command must report a held-log failure")
    if report.get("rotation_after_release") is not True or report.get("rotated_logs_readable") is not True:
        raise AssertionError("the same launcher logs must rotate and remain readable after release")
    if report.get("public_rotation_replayed") is not True or report.get("final_state") != "completed":
        raise AssertionError("the durable public rotation intent must complete after release")
    if report.get("recovered_same_run") is not True:
        raise AssertionError("public drive must recover the same run after log-handle release")


def run_probe(output: Path | None = None) -> dict[str, Any]:
    if platform.system() != "Windows":
        raise RuntimeError("native Windows launcher-log probe requires Windows")
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
        _write(brief, "# Windows launcher log handle acceptance\n")
        control = root / "控制 root"
        config = root / "runner.json"
        config.write_text(json.dumps({
            "schema_version": "spec-runner-config/v1",
            "repository_path": str(repository), "target_ref": "HEAD", "artifact_root": "artifacts",
            "execution_backend": "deterministic_test", "allowed_stages": ["example"],
            "model": {"name": "deterministic-test", "effort": "none"},
            "authorization": {"artifact_roots": ["artifacts"]},
        }, ensure_ascii=False), encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(source_root)
        env["SPEC_RUNNER_FAULT_POINT"] = "after_first_artifact"
        launch_code, launched, wrapper_pid = _invoke_launch([
            "launch", "--brief", str(brief), "--config", str(config),
            "--control-root", str(control), "--launch-key", "windows-launcher-log-handles",
        ], root, env)
        if launch_code != 0:
            raise RuntimeError(f"public detached launch failed: {launched}")
        run_id = str(launched["run_id"])
        child_pid = int(launched["pid"])
        log_root = control / "launcher-logs"
        stdout_path = log_root / f"{run_id}.stdout.log"
        stderr_path = log_root / f"{run_id}.stderr.log"
        ready_marker = control / "faults" / f"{run_id}.after_first_artifact.ready"
        for _ in range(200):
            if ready_marker.is_file() and stdout_path.is_file() and stderr_path.is_file():
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("detached Runner or launcher logs did not become ready")
        holder_ready = root / "holder.ready"
        holder_release = root / "holder.release"
        holder = subprocess.Popen([
            sys.executable, "-c", _HOLDER, str(stdout_path), str(stderr_path),
            str(holder_ready), str(holder_release),
        ], cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(200):
                if holder_ready.is_file():
                    break
                if holder.poll() is not None:
                    raise RuntimeError("launcher-log holder exited before acquiring both handles")
                time.sleep(0.05)
            if not holder_ready.is_file():
                raise RuntimeError("launcher-log holder did not become ready")
            blocked = [_rotate(stdout_path), _rotate(stderr_path)]
            _write(control / "faults" / f"{run_id}.after_first_artifact.continue", "continue\n")
            termination_code = _terminate_known_pid(child_pid)
            recovery_env = dict(env)
            recovery_env.pop("SPEC_RUNNER_FAULT_POINT", None)
            recovery_args = [
                "drive", "--brief", str(brief), "--config", str(config),
                "--control-root", str(control), "--launch-key", "windows-launcher-log-handles",
            ]
            recovery_deadline = time.monotonic() + 15
            drive_code = 2
            recovered: dict[str, Any] = {}
            while time.monotonic() < recovery_deadline:
                drive_code, recovered = _invoke(recovery_args, root, recovery_env)
                if drive_code == 0:
                    break
                if recovered.get("error", {}).get("code") != "writer_busy":
                    break
                time.sleep(0.1)
            recovered_same_run_while_log_handles_held = (
                drive_code == 0
                and recovered.get("run", {}).get("run_id") == run_id
                and recovered.get("run", {}).get("state") == "completed"
            )
            rotation_args = [
                "logs", "rotate", "--control-root", str(control), "--run-id", run_id,
                "--rotation-key", "windows-replay-01",
            ]
            rotation_code, rotation_failure = _invoke(rotation_args, root, recovery_env)
            public_rotation_error = str(rotation_failure.get("error", {}).get("code") or "")
        finally:
            holder_release.touch()
            holder.wait(timeout=10)
        if holder.returncode != 0:
            raise RuntimeError("launcher-log holder did not close both handles cleanly")
        retry_code, rotation_success = _invoke(rotation_args, root, recovery_env)
        rotated_paths = [
            Path(rotation_success.get("rotated_logs", {}).get("stdout", "")),
            Path(rotation_success.get("rotated_logs", {}).get("stderr", "")),
        ]
        readable = retry_code == 0 and all(
            path.is_file() and path.read_text(encoding="utf-8", errors="replace") is not None
            for path in rotated_paths
        )
        report = {
            "schema_version": "spec-runner-windows-launcher-log-handles/v1",
            "run_marker": "SRAC-20260926-windows-launcher-log-handles-01",
            "evidence_kind": "observed_windows_launcher_log_handles",
            "platform": platform.system(), "python": platform.python_version(),
            "run_id": run_id, "wrapper_pid": wrapper_pid, "child_pid": child_pid,
            "wrapper_exit_code": 0, "holder_pid": holder.pid, "holder_ready": holder_ready.is_file(),
            "rotation_while_held": blocked,
            "rotation_blocked_while_held": all(not item[0] for item in blocked),
            "runner_terminated_while_held": termination_code == 0,
            "rotation_after_release": retry_code == 0,
            "rotated_logs_readable": readable,
            "public_rotation_error": public_rotation_error,
            "public_rotation_replayed": rotation_success.get("replayed") is True,
            "public_rotation_first_exit_code": rotation_code,
            "drive_exit_code": drive_code,
            "recovered_same_run_while_log_handles_held": recovered_same_run_while_log_handles_held,
            "recovered_same_run": recovered.get("run", {}).get("run_id") == run_id,
            "final_state": recovered.get("run", {}).get("state"),
            "unverified": [
                "control DB lock combined with launcher-log lifecycle",
                "external GitHub side-effect reconciliation",
                "remaining #266 and project-level L3-L5 gates",
            ],
        }
    validate_report(report)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return report


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_probe(args.output)
    print(json.dumps({"report": str(args.output) if args.output else None,
                      "run_id": report["run_id"], "final_state": report["final_state"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
