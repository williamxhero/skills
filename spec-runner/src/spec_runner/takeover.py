"""Evidence-first takeover of ordinary threads and partially completed work."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .errors import RunnerError
from .plans import digest


def _git(path: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", os.fspath(path), *args], check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RunnerError("takeover_repository_invalid", "takeover repository is not a readable Git worktree") from exc
    return result.stdout.strip()


def load_inventory(path: Path) -> dict[str, Any]:
    try:
        inventory = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError("invalid_takeover_inventory", "takeover inventory must be UTF-8 JSON") from exc
    if inventory.get("schema_version") != "spec-runner-takeover-input/v1":
        raise RunnerError("invalid_takeover_inventory", "unexpected takeover inventory schema")
    return inventory


def inspect_takeover(inventory: dict[str, Any]) -> dict[str, object]:
    repo_value = inventory.get("repository_path")
    if not isinstance(repo_value, str):
        raise RunnerError("invalid_takeover_inventory", "repository_path is required")
    repository = Path(repo_value).expanduser().resolve()
    head = _git(repository, "rev-parse", "HEAD")
    status = _git(repository, "status", "--porcelain=v1")
    source_threads = inventory.get("source_threads", [])
    if not isinstance(source_threads, list) or any(not isinstance(thread, dict) for thread in source_threads):
        raise RunnerError("invalid_takeover_inventory", "source_threads must be a list of objects")
    adopted: list[dict[str, object]] = []
    unresolved: list[dict[str, object]] = []
    for thread in source_threads:
        identifier = thread.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise RunnerError("invalid_takeover_inventory", "source thread requires a stable id")
        ownership = thread.get("ownership")
        active = bool(thread.get("active", False))
        stop_confirmed = bool(thread.get("stop_confirmed", False))
        if ownership not in {"confirmed", "unknown", "preserve"}:
            raise RunnerError("invalid_takeover_inventory", "thread ownership must be confirmed, unknown, or preserve")
        if ownership != "confirmed":
            unresolved.append({"thread_id": identifier, "reason": "thread_ownership_unconfirmed"})
        elif active and not stop_confirmed:
            unresolved.append({"thread_id": identifier, "reason": "active_writer_not_stopped"})
        else:
            adopted.append({"thread_id": identifier, "lineage": thread.get("lineage"), "action": "continue_or_archive"})
    artifacts = inventory.get("artifacts", [])
    if not isinstance(artifacts, list) or any(not isinstance(item, dict) for item in artifacts):
        raise RunnerError("invalid_takeover_inventory", "artifacts must be a list of objects")
    classifications = []
    for artifact in artifacts:
        path = artifact.get("path")
        if not isinstance(path, str):
            raise RunnerError("invalid_takeover_inventory", "artifact path is required")
        candidate = (repository / path).resolve()
        if repository not in (candidate, *candidate.parents):
            raise RunnerError("takeover_path_escape", "takeover artifact escapes repository")
        classifications.append({"path": path, "exists": candidate.exists(), "state": "adopted" if candidate.exists() else "missing_evidence"})
    facts = inventory.get("facts", {})
    if not isinstance(facts, dict):
        raise RunnerError("invalid_takeover_inventory", "facts must be an object")
    # Historical claims are intentionally not converted into current verification.
    return {
        "schema_version": "spec-runner-takeover-report/v1",
        "repository": os.fspath(repository),
        "head_sha": head,
        "working_tree": {"dirty": bool(status), "porcelain": status},
        "adopted_threads": adopted,
        "unresolved": unresolved,
        "artifacts": classifications,
        "historical_facts": facts,
        "next_state": "blocked" if unresolved else "adopted_ready",
        "digest": digest({"repo": os.fspath(repository), "head": head, "threads": source_threads, "artifacts": classifications, "facts": facts}),
    }


def completion_action(report: dict[str, Any]) -> dict[str, object]:
    """Choose only a mechanical next category; no hidden LLM control loop."""
    if report.get("next_state") == "blocked":
        return {"state": "blocked", "reason": "takeover ownership or active-writer evidence is incomplete"}
    facts = report.get("historical_facts", {})
    if isinstance(facts, dict) and facts.get("merged") and facts.get("verification_receipt"):
        return {"state": "cleanup_pending", "implementation_calls": 0, "merge_calls": 0}
    return {"state": "resume_delivery", "implementation_calls": 0, "merge_calls": 0, "requires": "normal Runner stage loop"}
