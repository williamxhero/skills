"""Durable launcher-log rotation and retention for the public Runner CLI."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import RunnerError


_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SCHEMA = "spec-runner-launcher-log-rotation/v1"


@dataclass(frozen=True)
class LauncherLogPaths:
    stdout: Path
    stderr: Path

    def public(self) -> dict[str, str]:
        return {"stdout": os.fspath(self.stdout), "stderr": os.fspath(self.stderr)}


def _token(value: str, name: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise RunnerError(
            "launcher_log_identity_invalid",
            f"{name} must contain only bounded identifier characters",
        )
    return value


def launcher_log_paths(control_root: Path, run_id: str) -> LauncherLogPaths:
    run_id = _token(run_id, "run_id")
    root = control_root.expanduser().resolve() / "launcher-logs"
    return LauncherLogPaths(
        stdout=root / f"{run_id}.stdout.log",
        stderr=root / f"{run_id}.stderr.log",
    )


def _rotation_root(control_root: Path, run_id: str) -> Path:
    return control_root.expanduser().resolve() / "launcher-logs" / "rotated" / run_id


def _rotation_paths(control_root: Path, run_id: str, rotation_key: str) -> LauncherLogPaths:
    root = _rotation_root(control_root, run_id) / rotation_key
    return LauncherLogPaths(stdout=root / "stdout.log", stderr=root / "stderr.log")


def _receipt_path(control_root: Path, run_id: str, rotation_key: str) -> Path:
    return (
        control_root.expanduser().resolve()
        / "launcher-logs"
        / "rotation-receipts"
        / f"{run_id}.{rotation_key}.json"
    )


def _write_json_atomic(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(document, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise RunnerError(
            "launcher_log_receipt_write_failed",
            "could not persist launcher-log rotation receipt",
            details={"receipt": os.fspath(path), "error_type": type(exc).__name__},
        ) from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _read_receipt(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RunnerError(
            "launcher_log_rotation_receipt_invalid",
            "launcher-log rotation receipt cannot be read safely",
            details={"receipt": os.fspath(path), "error_type": type(exc).__name__},
        ) from exc
    if not isinstance(document, dict) or document.get("schema_version") != _SCHEMA:
        raise RunnerError(
            "launcher_log_rotation_receipt_invalid",
            "launcher-log rotation receipt has an unsupported schema",
            details={"receipt": os.fspath(path)},
        )
    return document


def _rollback(moved: list[tuple[Path, Path]]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    for source, destination in reversed(moved):
        try:
            if source.exists() and not destination.exists():
                os.replace(source, destination)
            elif source.exists() or destination.exists():
                failures.append(os.fspath(source))
        except OSError:
            failures.append(os.fspath(source))
    return not failures, failures


def _remove_tree(path: Path) -> tuple[bool, list[str]]:
    pending: list[str] = []
    if not path.exists():
        return True, pending
    for child in (path / "stdout.log", path / "stderr.log"):
        try:
            child.unlink(missing_ok=True)
        except OSError:
            pending.append(os.fspath(child))
    try:
        path.rmdir()
    except OSError:
        if path.exists():
            pending.append(os.fspath(path))
    return not pending, pending


def _retain_rotations(control_root: Path, run_id: str, retain: int) -> dict[str, object]:
    root = _rotation_root(control_root, run_id)
    if not root.is_dir():
        return {"outcome": "cleaned", "removed": [], "pending": []}
    candidates = [
        child
        for child in root.iterdir()
        if child.is_dir() and (child / "stdout.log").is_file() and (child / "stderr.log").is_file()
    ]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    removed: list[str] = []
    pending: list[str] = []
    for candidate in candidates[retain:]:
        cleaned, failed = _remove_tree(candidate)
        if cleaned:
            removed.append(candidate.name)
        else:
            pending.extend(failed)
    incomplete = [
        child
        for child in root.iterdir()
        if child.is_dir() and not ((child / "stdout.log").is_file() and (child / "stderr.log").is_file())
    ]
    pending.extend(os.fspath(child) for child in incomplete)
    return {
        "outcome": "cleaned" if not pending else "pending",
        "removed": removed,
        "pending": sorted(set(pending)),
    }


def _result(
    *,
    run_id: str,
    rotation_key: str,
    receipt: Path,
    active: LauncherLogPaths,
    rotated: LauncherLogPaths,
    state: str,
    retention: dict[str, object],
    replayed: bool,
) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA,
        "run_id": run_id,
        "rotation_key": rotation_key,
        "state": state,
        "replayed": replayed,
        "receipt": os.fspath(receipt),
        "active_logs": active.public(),
        "rotated_logs": rotated.public(),
        "retention": retention,
    }


def rotate_launcher_logs(
    *,
    control_root: Path,
    run_id: str,
    rotation_key: str,
    retain: int = 3,
) -> dict[str, object]:
    """Rotate one run's stdout/stderr pair with replayable durable intent."""

    run_id = _token(run_id, "run_id")
    rotation_key = _token(rotation_key, "rotation_key")
    if not isinstance(retain, int) or isinstance(retain, bool) or not 1 <= retain <= 100:
        raise RunnerError("launcher_log_retention_invalid", "retain must be an integer between 1 and 100")

    control_root = control_root.expanduser().resolve()
    active = launcher_log_paths(control_root, run_id)
    rotated = _rotation_paths(control_root, run_id, rotation_key)
    receipt_path = _receipt_path(control_root, run_id, rotation_key)
    prior = _read_receipt(receipt_path)
    replayed_intent = prior is not None
    if prior is not None:
        if prior.get("run_id") != run_id or prior.get("rotation_key") != rotation_key:
            raise RunnerError("launcher_log_rotation_receipt_invalid", "rotation receipt identity does not match request")
        if prior.get("state") == "failed" and not rotated.stdout.exists() and not rotated.stderr.exists():
            prior = None
        elif prior.get("state") in {"rotated", "retention_pending"}:
            if not (rotated.stdout.is_file() and rotated.stderr.is_file()):
                raise RunnerError(
                    "launcher_log_rotation_uncertain",
                    "rotation receipt exists but the rotated log pair is incomplete",
                    details={"receipt": os.fspath(receipt_path)},
                )
            retention = _retain_rotations(control_root, run_id, retain)
            state = "rotated" if retention["outcome"] == "cleaned" else "retention_pending"
            document = {**prior, "state": state, "retention": retention}
            _write_json_atomic(receipt_path, document)
            return _result(
                run_id=run_id,
                rotation_key=rotation_key,
                receipt=receipt_path,
                active=active,
                rotated=rotated,
                state=state,
                retention=retention,
                replayed=True,
            )
        elif prior.get("state") == "intent" and rotated.stdout.exists() != rotated.stderr.exists():
            raise RunnerError(
                "launcher_log_rotation_uncertain",
                "rotation intent has only one moved launcher log",
                details={"receipt": os.fspath(receipt_path)},
            )

    intent = {
        "schema_version": _SCHEMA,
        "run_id": run_id,
        "rotation_key": rotation_key,
        "state": "intent",
        "active_logs": active.public(),
        "rotated_logs": rotated.public(),
        "retain": retain,
    }
    if prior is None:
        _write_json_atomic(receipt_path, intent)

    if rotated.stdout.exists() or rotated.stderr.exists():
        raise RunnerError(
            "launcher_log_rotation_uncertain",
            "rotation target already exists without a completed receipt",
            details={"receipt": os.fspath(receipt_path)},
        )
    if not active.stdout.is_file() or not active.stderr.is_file():
        raise RunnerError(
            "launcher_log_missing",
            "both active launcher logs are required before rotation",
            details={"active_logs": active.public()},
        )
    rotated.stdout.parent.mkdir(parents=True, exist_ok=True)

    moved: list[tuple[Path, Path]] = []
    created_active: list[Path] = []
    try:
        os.replace(active.stdout, rotated.stdout)
        moved.append((rotated.stdout, active.stdout))
        os.replace(active.stderr, rotated.stderr)
        moved.append((rotated.stderr, active.stderr))
        for path in (active.stdout, active.stderr):
            with path.open("xb"):
                pass
            created_active.append(path)
    except OSError as exc:
        for path in created_active:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        rolled_back, rollback_failures = _rollback(moved)
        failed = {**intent, "state": "failed", "rollback": rolled_back, "rollback_failures": rollback_failures}
        _write_json_atomic(receipt_path, failed)
        code = "launcher_log_rotation_failed" if rolled_back else "launcher_log_rotation_uncertain"
        raise RunnerError(
            code,
            "launcher logs could not be rotated as one pair",
            details={
                "error_type": type(exc).__name__,
                "receipt": os.fspath(receipt_path),
                "rollback": rolled_back,
                "rollback_failures": rollback_failures,
            },
        ) from exc

    retention = _retain_rotations(control_root, run_id, retain)
    state = "rotated" if retention["outcome"] == "cleaned" else "retention_pending"
    completed = {**intent, "state": state, "retention": retention}
    _write_json_atomic(receipt_path, completed)
    return _result(
        run_id=run_id,
        rotation_key=rotation_key,
        receipt=receipt_path,
        active=active,
        rotated=rotated,
        state=state,
        retention=retention,
        replayed=replayed_intent,
    )
