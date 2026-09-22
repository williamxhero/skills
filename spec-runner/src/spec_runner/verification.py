from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import RunnerError
from .store import RunRecord

VALIDATOR_VERSION = "spec-runner-validator/v1"


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_file(path: Path, root: Path, label: str) -> Path:
    if path.is_symlink():
        raise RunnerError("verification_failed", f"{label} is a symbolic link")
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RunnerError("verification_failed", f"{label} escaped the authorized artifact root") from exc
    if not path.is_file():
        raise RunnerError("verification_failed", f"{label} does not exist as a regular file")
    return path


def _load_json(path: Path, root: Path, label: str) -> dict[str, Any]:
    safe_path = _safe_file(path, root, label)
    try:
        value = json.loads(safe_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError("verification_failed", f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise RunnerError("verification_failed", f"{label} must contain a JSON object")
    return value


def verify_run(*, control_root: Path, run: RunRecord, worker: dict[str, object]) -> dict[str, object]:
    """Validate a stage's real files and return a program-owned receipt payload."""
    artifact_root = (control_root / run.artifact_root).resolve()
    artifact_directory = (artifact_root / run.run_id).resolve()
    if artifact_root not in (artifact_directory, *artifact_directory.parents) or artifact_directory.is_symlink():
        raise RunnerError("verification_failed", "run artifact directory escaped or is a symbolic link")
    if not artifact_directory.is_dir():
        raise RunnerError("verification_failed", "run artifact directory does not exist")

    artifacts: dict[str, str] = {}
    if run.backend_kind == "deterministic_test":
        if run.current_step == "deterministic_second":
            final_path = _safe_file(artifact_directory / "final.json", artifact_directory, "final.json")
            final = _load_json(final_path, artifact_directory, "final.json")
            if final.get("run_id") != run.run_id or final.get("consumed_stage") != "deterministic_example":
                raise RunnerError("verification_failed", "second-stage handoff identity does not match the run")
            artifacts["final.json"] = _digest(final_path)
            return {
                "schema_version": "spec-runner-verification-receipt/v1",
                "run_id": run.run_id,
                "stage": run.current_step,
                "input_digest": run.input_digest,
                "artifact_digests": artifacts,
                "validator_version": VALIDATOR_VERSION,
                "observed_at": datetime.now(UTC).isoformat(),
                "outcome": "verified",
            }
        brief_path = _safe_file(artifact_directory / "brief.md", artifact_directory, "brief.md")
        if hashlib.sha256(brief_path.read_bytes()).hexdigest() != run.input_digest:
            raise RunnerError("verification_failed", "brief.md digest does not match the run input")
        handoff_path = artifact_directory / "handoff.json"
        handoff = _load_json(handoff_path, artifact_directory, "handoff.json")
        if handoff.get("run_id") != run.run_id or handoff.get("brief_digest") != run.input_digest:
            raise RunnerError("verification_failed", "handoff identity does not match the run")
        if handoff.get("backend_kind") != "deterministic_test":
            raise RunnerError("verification_failed", "handoff backend kind is not deterministic_test")
        artifacts["brief.md"] = _digest(brief_path)
        artifacts["handoff.json"] = _digest(handoff_path)
    elif run.backend_kind == "codex_sdk":
        result_name = "worker-result.json" if run.current_step == "codex_example" else "worker-result-second.json"
        result_path = artifact_directory / result_name
        result = _load_json(result_path, artifact_directory, result_name)
        if result.get("schema_version") != "spec-runner-worker-result/v1":
            raise RunnerError("verification_failed", "worker result schema is not supported")
        if result.get("input_digest") != run.input_digest:
            raise RunnerError("verification_failed", "worker result input digest does not match the run")
        if result.get("thread_id") != worker.get("external_thread_id") or result.get("turn_id") != worker.get("external_turn_id"):
            raise RunnerError("verification_failed", "worker result IDs do not match the persisted SDK IDs")
        if result.get("status") != "completed":
            raise RunnerError("verification_failed", "worker result is not terminal completed")
        declared_artifacts = result.get("artifacts")
        if not isinstance(declared_artifacts, list) or not declared_artifacts:
            raise RunnerError("verification_failed", "non-empty worker text without declared artifacts is not completion")
        artifacts["worker-result.json"] = _digest(result_path)
        workspace_root = Path(run.repository_path).resolve()
        allowed_workspace_root = (workspace_root / "spec-runner-output" / run.run_id).resolve()
        for raw_path in declared_artifacts:
            if not isinstance(raw_path, str) or not raw_path:
                raise RunnerError("verification_failed", "worker artifact paths must be non-empty strings")
            candidate = Path(raw_path)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise RunnerError("verification_failed", "worker artifact path is not safely relative")
            full_path = (workspace_root / candidate).resolve()
            safe_path = _safe_file(full_path, allowed_workspace_root, f"worker artifact {raw_path}")
            artifacts[f"workspace:{candidate.as_posix()}"] = _digest(safe_path)
    else:
        raise RunnerError("verification_failed", "unknown backend kind")

    return {
        "schema_version": "spec-runner-verification-receipt/v1",
        "run_id": run.run_id,
        "stage": run.current_step,
        "input_digest": run.input_digest,
        "artifact_digests": artifacts,
        "validator_version": VALIDATOR_VERSION,
        "observed_at": datetime.now(UTC).isoformat(),
        "outcome": "verified",
    }
