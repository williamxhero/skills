"""Detached process lifecycle for the public Runner launch operation."""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from hashlib import sha256
from pathlib import Path
from .config import RunnerConfig, read_brief
from .errors import RunnerError
from .scope_lock import ScopeLock
from .store import Store, _lease_age_seconds, _process_alive


def validate_launch_key(value: str) -> str:
    if not value or len(value) > 200 or any(character.isspace() for character in value):
        raise RunnerError("invalid_launch_key", "launch_key must be non-empty, at most 200 characters, and contain no whitespace")
    return value


def launch_claim(*, control_root: Path, run_id: str, child_pid: int,
                 launch_token: str | None = None, launch_key: str | None = None,
                 lease_scope: str | None = None,
                 require_lease: bool = False) -> dict[str, object] | None:
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
        lease = store.lease(lease_scope) if lease_scope else None
        token_claim = (
            isinstance(launch_token, str)
            and bool(launch_token)
            and isinstance(runtime, dict)
            and record is not None
            and str(runtime.get("owner_token", "")).startswith(f"{record.run_id}:launch:{launch_token}:")
        )
        active_writer_claim = (
            record is not None
            and runtime is not None
            and lease_scope is not None
            and isinstance(lease, dict)
            and str(lease.get("owner_token")) == str(runtime.get("owner_token"))
            and token_claim
        )
        terminal_claim = token_claim and record is not None and record.state in {
            "completed", "cancelled", "failed", "blocked", "blocked_writer_busy",
        }
        legacy_token_claim = token_claim and lease_scope is None
        pid_claim = int(runtime["pid"]) == child_pid if record and runtime else False
        if record and runtime and (
            (pid_claim and not require_lease)
            or active_writer_claim
            or terminal_claim
            or legacy_token_claim
        ):
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
        _, brief_digest = read_brief(brief_file)
        config = RunnerConfig.from_file(config_file, control_root)
        lease_scope = f"{os.path.normcase(os.fspath(config.repository_path))}@{config.target_ref}"
        coordination_path = _launch_coordination_path(lease_scope)
        coordination_lock = ScopeLock.acquire(coordination_path, f"launch:{os.getpid()}:{uuid.uuid4().hex}")
        try:
            existing = _existing_launch(
                control_root=control_root,
                launch_key=launch_key,
                brief_digest=brief_digest,
                config=config,
            )
            if existing is not None:
                if _owner_unresolved(
                    control_root=control_root,
                    run_id=existing.run_id,
                    lease_scope=lease_scope,
                    stale_after_seconds=_stale_after_seconds(config),
                ):
                    return _replayed_launch(control_root=control_root, record=existing, lease_scope=lease_scope)
                if existing.state in {"completed", "cancelled", "failed", "blocked", "blocked_writer_busy"}:
                    return _replayed_launch(control_root=control_root, record=existing, lease_scope=lease_scope)
                run_id = existing.run_id
            else:
                run_id = str(uuid.uuid4())

            _assert_repository_scope_available(lease_scope)
            launch_token = secrets.token_hex(32)
            return self._spawn_and_handshake(
                brief_file=brief_file,
                config_file=config_file,
                control_root=control_root,
                launch_key=launch_key,
                run_id=run_id,
                launch_token=launch_token,
                lease_scope=lease_scope,
                handshake_timeout_seconds=handshake_timeout_seconds,
            )
        finally:
            coordination_lock.release(coordination_lock.owner_token)

    def _spawn_and_handshake(self, *, brief_file: Path, config_file: Path, control_root: Path,
                             launch_key: str, run_id: str, launch_token: str,
                             lease_scope: str, handshake_timeout_seconds: float) -> dict[str, object]:
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
                lease_scope=lease_scope,
                require_lease=True,
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
            lease_scope=lease_scope,
            require_lease=True,
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


def _launch_coordination_path(lease_scope: str) -> Path:
    """Return a stable process lock shared by launchers with the same repo/ref."""
    digest = sha256(lease_scope.encode("utf-8")).hexdigest()
    return Path(tempfile.gettempdir()) / "spec-runner-launches" / f"{digest}.lock"


def _existing_launch(*, control_root: Path, launch_key: str, brief_digest: str,
                     config: RunnerConfig):
    try:
        store = Store.open(control_root, create=False)
    except RunnerError as exc:
        if exc.code in {"unknown_control_root", "control_not_ready"}:
            return None
        raise
    try:
        existing = store.find_by_launch_key(launch_key)
        if existing is None:
            return None
        from .workflow import _acceptance_upgrade_compatible

        config_matches = _acceptance_upgrade_compatible(existing, config)
        if existing.input_digest != brief_digest or (
            existing.config_digest != config.digest and not config_matches
        ):
            raise RunnerError(
                "launch_key_input_conflict",
                "launch_key already belongs to different normalized input",
                details={"run_id": existing.run_id},
            )
        return existing
    finally:
        store.close()


def _replayed_launch(*, control_root: Path, record, lease_scope: str) -> dict[str, object]:
    """Project an existing identity without creating a second detached child."""
    store = Store.open(control_root, create=False)
    try:
        status = store.public_status(record.run_id)
        runtime = status.get("runtime")
        lease = store.lease(lease_scope)
        pid = runtime.get("pid") if isinstance(runtime, dict) else None
        owner_matches = (
            isinstance(runtime, dict)
            and isinstance(lease, dict)
            and str(runtime.get("owner_token")) == str(lease.get("owner_token"))
            and str(lease.get("run_id")) == record.run_id
        )
        alive = owner_matches and isinstance(pid, int) and _process_alive(pid)
        return {
            "started": bool(alive),
            "replayed": True,
            "pid": pid if alive else None,
            "run_id": record.run_id,
            "log_path": os.fspath(control_root / "launcher-logs" / f"{record.run_id}.stdout.log"),
            "run": status,
        }
    finally:
        store.close()


def _owner_unresolved(*, control_root: Path, run_id: str, lease_scope: str,
                      stale_after_seconds: float) -> bool:
    store = Store.open(control_root, create=False)
    try:
        runtime = store.runtime_for_run(run_id)
        lease = store.lease(lease_scope)
        runtime_pid = runtime.get("pid") if isinstance(runtime, dict) else None
        if not isinstance(lease, dict) or str(lease.get("run_id")) != run_id:
            return False
        owner_matches = (
            isinstance(runtime, dict)
            and str(runtime.get("owner_token")) == str(lease.get("owner_token"))
        )
        if owner_matches and isinstance(runtime_pid, int) and _process_alive(runtime_pid):
            return True
        same_host = str(lease.get("host")) == socket.gethostname()
        age = _lease_age_seconds(str(lease.get("heartbeat_at", "")))
        lease_pid = lease.get("pid")
        reclaimable = (
            same_host
            and age is not None
            and age >= stale_after_seconds
            and isinstance(lease_pid, int)
            and not _process_alive(lease_pid)
        )
        return not reclaimable
    finally:
        store.close()


def _stale_after_seconds(config: RunnerConfig) -> float:
    value = os.environ.get("SPEC_RUNNER_TEST_LEASE_STALE_AFTER_SECONDS")
    if config.execution_backend == "deterministic_test" and value:
        try:
            return max(0.0, float(value))
        except ValueError as exc:
            raise RunnerError("invalid_test_fault_config", "test lease stale timeout must be numeric") from exc
    return 5.0


def _assert_repository_scope_available(lease_scope: str) -> None:
    """Reserve the writer slot before spawning, closing the duplicate-child race."""
    lock_path = _repository_scope_lock_path(lease_scope)
    probe = ScopeLock.acquire(lock_path, f"launch-probe:{os.getpid()}:{uuid.uuid4().hex}")
    probe.release(probe.owner_token)


def _repository_scope_lock_path(lease_scope: str) -> Path:
    digest = sha256(lease_scope.encode("utf-8")).hexdigest()
    return Path(tempfile.gettempdir()) / "spec-runner-leases" / f"{digest}.lock"
