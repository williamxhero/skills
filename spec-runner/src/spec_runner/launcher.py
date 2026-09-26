"""Detached process lifecycle for the public Runner launch operation."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path
from .config import RunnerConfig, read_brief
from .errors import RunnerError
from .store import Store


def validate_launch_key(value: str) -> str:
    if not value or len(value) > 200 or any(character.isspace() for character in value):
        raise RunnerError("invalid_launch_key", "launch_key must be non-empty, at most 200 characters, and contain no whitespace")
    return value


def launch_claim(*, control_root: Path, run_id: str, child_pid: int,
                 launch_token: str | None = None, launch_key: str | None = None) -> dict[str, object] | None:
    """Read a detached launch claim without creating control state."""
    try:
        store = Store.open(control_root, create=False)
    except RunnerError:
        return None
    try:
        record = store.find_by_run_id(run_id)
        if record is None and launch_key:
            record = store.find_by_launch_key(launch_key)
        runtime = store.runtime_for_run(record.run_id) if record else None
        token_claim = (
            isinstance(launch_token, str)
            and bool(launch_token)
            and isinstance(runtime, dict)
            and record is not None
            and str(runtime.get("owner_token", "")).startswith(f"{record.run_id}:launch:{launch_token}:")
        )
        if record and runtime and (int(runtime["pid"]) == child_pid or token_claim):
            return {
                "started": True,
                "pid": child_pid,
                "run_id": record.run_id,
                "run": store.public_status(record.run_id),
            }
        return None
    finally:
        store.close()


def terminate_unclaimed_child(child: subprocess.Popen[bytes], *, timeout_seconds: float = 2.0) -> bool:
    """Stop only a child that never claimed a durable Runner identity."""
    if child.poll() is not None:
        return True
    try:
        child.terminate()
        child.wait(timeout=timeout_seconds)
        return True
    except subprocess.TimeoutExpired:
        try:
            child.kill()
            child.wait(timeout=timeout_seconds)
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False
    except OSError:
        return False


class DetachedLauncher:
    """Create one detached child and wait for its durable runtime handshake."""

    def launch(self, *, brief_file: Path, config_file: Path, control_root: Path,
               launch_key: str, handshake_timeout_seconds: float = 10.0) -> dict[str, object]:
        launch_key = validate_launch_key(launch_key)
        if handshake_timeout_seconds <= 0:
            raise RunnerError("launch_timeout_invalid", "detached launch handshake timeout must be positive")
        brief_file = brief_file.expanduser().resolve()
        config_file = config_file.expanduser().resolve()
        control_root = control_root.expanduser().resolve()
        read_brief(brief_file)
        RunnerConfig.from_file(config_file, control_root)
        run_id = str(uuid.uuid4())
        launch_token = secrets.token_hex(32)
        log_root = control_root / "launcher-logs"
        log_root.mkdir(parents=True, exist_ok=True)
        stdout_path = log_root / f"{run_id}.stdout.log"
        stderr_path = log_root / f"{run_id}.stderr.log"
        command = [
            sys.executable,
            "-m",
            "spec_runner.cli",
            "drive",
            "--brief",
            os.fspath(brief_file),
            "--config",
            os.fspath(config_file),
            "--control-root",
            os.fspath(control_root),
            "--launch-key",
            launch_key,
            "--run-id",
            run_id,
            "--launch-token",
            launch_token,
        ]
        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
            child = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                close_fds=True,
                creationflags=creation_flags,
                cwd=os.fspath(control_root),
            )
        deadline = time.monotonic() + handshake_timeout_seconds
        while time.monotonic() < deadline:
            claimed = launch_claim(
                control_root=control_root,
                run_id=run_id,
                child_pid=child.pid,
                launch_token=launch_token,
                launch_key=launch_key,
            )
            if claimed is not None:
                return {"log_path": os.fspath(stdout_path), **claimed}
            if child.poll() is not None:
                raise RunnerError(
                    "launch_handshake_failed",
                    "detached Runner exited before claiming the run",
                    details={"exit_code": child.returncode, "stderr_log": os.fspath(stderr_path)},
                )
            time.sleep(0.05)
        claimed = launch_claim(
            control_root=control_root,
            run_id=run_id,
            child_pid=child.pid,
            launch_token=launch_token,
            launch_key=launch_key,
        )
        if claimed is not None:
            return {"log_path": os.fspath(stdout_path), **claimed}
        terminated = terminate_unclaimed_child(child)
        if not terminated:
            raise RunnerError(
                "launch_cleanup_failed",
                "detached Runner missed its handshake and could not be stopped safely",
                details={"pid": child.pid, "stdout_log": os.fspath(stdout_path), "stderr_log": os.fspath(stderr_path)},
            )
        raise RunnerError(
            "launch_handshake_timeout",
            "detached Runner did not claim the run before the handshake deadline",
            details={"pid": child.pid, "stdout_log": os.fspath(stdout_path), "stderr_log": os.fspath(stderr_path), "terminated": True},
        )
