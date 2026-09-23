"""Evidence gates at the real semantic worker/delivery boundary."""
from __future__ import annotations

import json
from pathlib import Path

from .codex_adapter import CodexWorkerResult
from .delivery import validate_review
from .errors import RunnerError


def worker_document(result: CodexWorkerResult) -> dict:
    if result.status != "completed" or result.error:
        raise RunnerError("worker_not_successful", "a failed or nonterminal worker cannot authorize delivery")
    if not result.thread_id or not result.turn_id:
        raise RunnerError("worker_identity_missing", "semantic worker must have formal thread and turn identities")
    try:
        document = json.loads(result.final_response or "null")
    except json.JSONDecodeError as exc:
        raise RunnerError("invalid_worker_output", "worker output must be JSON") from exc
    if not isinstance(document, dict):
        raise RunnerError("invalid_worker_output", "worker output must be an object")
    return document


def implementation_artifacts(result: CodexWorkerResult, workspace: Path) -> dict:
    document = worker_document(result)
    if document.get("outcome") != "completed" or document.get("blockers") != [] or document.get("questions") != []:
        raise RunnerError("implementation_not_ready", "implementation is incomplete or requires input")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RunnerError("implementation_artifacts_missing", "implementation must identify real artifacts")
    root = workspace.resolve()
    for item in artifacts:
        if not isinstance(item, str) or not item.strip():
            raise RunnerError("implementation_artifact_invalid", "artifact must be a relative path")
        relative = Path(item)
        path = root / relative
        if (relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts
            or root not in path.resolve().parents or not path.is_file()
            or any(part.is_symlink() for part in [path, *path.parents])):
            raise RunnerError("implementation_artifact_invalid", "artifact must be a real file inside the assigned workspace")
    return document


def independent_review(result: CodexWorkerResult, *, implementation_thread: str,
                       candidate_sha: str, acceptance_version: str) -> dict:
    document = worker_document(result)
    if result.thread_id == implementation_thread:
        raise RunnerError("review_not_independent", "implementation owner cannot approve its own candidate")
    return validate_review(result=document, candidate_sha=candidate_sha, acceptance_version=acceptance_version)
