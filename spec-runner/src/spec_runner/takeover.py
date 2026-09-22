"""Evidence-first takeover of ordinary threads and partially completed work."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .errors import RunnerError
from .plans import digest
from .store import Store


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
    try:
        head = _git(repository, "rev-parse", "HEAD")
    except RunnerError:
        # A valid newly initialized repository may not have its first commit.
        # Keep that fact explicit; it is different from a non-Git directory.
        try:
            _git(repository, "rev-parse", "--git-dir")
        except RunnerError:
            raise
        head = "unborn"
    status = _git(repository, "status", "--porcelain=v1")
    branch = _git(repository, "branch", "--show-current")
    try:
        latest_commit = _git(repository, "log", "-1", "--format=%H%x00%s")
    except RunnerError:
        latest_commit = ""
    status_lines = status.splitlines() if status else []
    source_threads = inventory.get("source_threads", [])
    if not isinstance(source_threads, list) or any(not isinstance(thread, dict) for thread in source_threads):
        raise RunnerError("invalid_takeover_inventory", "source_threads must be a list of objects")
    adopted: list[dict[str, object]] = []
    unresolved: list[dict[str, object]] = []
    handover_policy = inventory.get("handover_policy", "require_stop_confirmation")
    if handover_policy not in {"require_stop_confirmation", "wait_then_takeover", "interrupt_then_takeover"}:
        raise RunnerError("invalid_takeover_inventory", "handover_policy is not supported")
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
            unresolved.append(
                {
                    "thread_id": identifier,
                    "reason": "waiting_handover" if handover_policy == "wait_then_takeover" else "active_writer_not_stopped",
                    "policy": handover_policy,
                }
            )
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
        "repository_snapshot": {
            "branch": branch or None,
            "head_sha": head,
            "latest_commit": latest_commit or None,
            "dirty": bool(status),
            "porcelain": status,
            "staged": [line for line in status_lines if len(line) >= 2 and line[0] != " " and line[0] != "?"],
            "untracked": [line[3:] for line in status_lines if line.startswith("?? ")],
        },
        "working_tree": {"dirty": bool(status), "porcelain": status},
        "adopted_threads": adopted,
        "unresolved": unresolved,
        "artifacts": classifications,
        "historical_facts": facts,
        "handover": {
            "policy": handover_policy,
            "state": "waiting_handover" if any(item.get("reason") == "waiting_handover" for item in unresolved) else ("blocked" if unresolved else "released"),
            "active_threads": [str(thread.get("id")) for thread in source_threads if bool(thread.get("active", False))],
        },
        "next_state": "waiting_handover" if any(item.get("reason") == "waiting_handover" for item in unresolved) else ("blocked" if unresolved else "adopted_ready"),
        "digest": digest({"repo": os.fspath(repository), "snapshot": {"branch": branch, "head": head, "status": status}, "threads": source_threads, "artifacts": classifications, "facts": facts, "handover_policy": handover_policy}),
    }


def completion_action(report: dict[str, Any]) -> dict[str, object]:
    """Choose only a mechanical next category; no hidden LLM control loop."""
    if report.get("next_state") == "waiting_handover":
        return {"state": "waiting_handover", "reason": "the selected handover policy requires a real stop confirmation before a new writer starts"}
    if report.get("next_state") == "blocked":
        return {"state": "blocked", "reason": "takeover ownership or active-writer evidence is incomplete"}
    facts = report.get("historical_facts", {})
    if isinstance(facts, dict) and facts.get("merged") and facts.get("verification_receipt"):
        return {"state": "cleanup_pending", "implementation_calls": 0, "merge_calls": 0}
    return {"state": "resume_delivery", "implementation_calls": 0, "merge_calls": 0, "requires": "normal Runner stage loop"}


def plan_frontier(report: dict[str, Any]) -> dict[str, object]:
    """Derive a deterministic remaining-work frontier from observed facts.

    This is intentionally mechanical.  It never treats a missing historical
    receipt as success and never chooses a model/tool on behalf of the runner.
    """
    if report.get("schema_version") != "spec-runner-takeover-report/v1":
        raise RunnerError("invalid_takeover_report", "frontier planning requires a takeover report")
    if report.get("next_state") in {"blocked", "waiting_handover"}:
        state = str(report.get("next_state"))
        reason = "wait for a real source stop confirmation before starting a new writer" if state == "waiting_handover" else "ownership or active-writer evidence is incomplete"
        return {"schema_version": "spec-runner-frontier/v1", "state": state, "steps": [{"kind": "handover", "status": state, "reason": reason}], "digest": digest(report)}
    facts = report.get("historical_facts", {})
    if not isinstance(facts, dict):
        raise RunnerError("invalid_takeover_report", "historical_facts must be an object")
    steps: list[dict[str, object]] = []
    categories = {"adopted": [], "backfilled": [], "reverified": [], "new_work": [], "remaining": [], "cleanup": []}

    specs = facts.get("specs") if isinstance(facts, dict) else None
    if specs is not None:
        if not isinstance(specs, list) or any(not isinstance(spec, dict) or not isinstance(spec.get("key"), str) for spec in specs):
            raise RunnerError("invalid_takeover_report", "historical_facts.specs must be a list of keyed objects")
        for spec in specs:
            key = str(spec["key"])
            state = str(spec.get("state", "unknown"))
            if state in {"completed", "merged", "delivered"} and spec.get("verified"):
                categories["adopted"].append(key)
                if spec.get("cleanup_pending"):
                    categories["cleanup"].append(key)
                    steps.append({"kind": "cleanup", "target": key, "status": "planned", "implementation_calls": 0, "merge_calls": 0})
                else:
                    steps.append({"kind": "adopt", "target": key, "status": "adopted", "reason": "verified delivery evidence is present"})
            elif state in {"partial", "implementing", "candidate", "merged_without_evidence"}:
                categories["adopted"].append(key)
                categories["reverified"].append(key)
                categories["remaining"].append(key)
                steps.append({"kind": "reverify", "target": key, "status": "planned", "reason": "partial or stale evidence requires current verification"})
            elif state in {"not_started", "missing", "planned"}:
                categories["new_work"].append(key)
                categories["remaining"].append(key)
                steps.append({"kind": "resume", "target": key, "status": "planned", "reason": "SPEC has no adopted delivery candidate"})
            else:
                categories["remaining"].append(key)
                steps.append({"kind": "reconcile", "target": key, "status": "needs_input", "reason": "SPEC state is unknown or conflicting"})

    has_verified_delivery = bool(facts.get("merged") and facts.get("verification_receipt"))
    if not facts.get("requirements") and not has_verified_delivery:
        categories["backfilled"].append("requirement_scope")
        steps.append({"kind": "backfill", "target": "requirement_scope", "status": "needs_input", "reason": "original requirement scope is not present in the inventory"})
    if not facts.get("tracker"):
        categories["backfilled"].append("tracker_plan")
        steps.append({"kind": "backfill", "target": "tracker_plan", "status": "planned", "reason": "create the minimum local or GitHub tracker objects after source scope is confirmed"})
    if facts.get("working_tree") or facts.get("partial_code"):
        categories["adopted"].append("working_tree")
        categories["reverified"].append("candidate")
        steps.extend([
            {"kind": "adopt", "target": "working_tree", "status": "adopted", "reason": "preserve existing source, index, and untracked files"},
            {"kind": "reverify", "target": "candidate", "status": "planned", "reason": "existing code is evidence of files, not a passed acceptance receipt"},
        ])
    if facts.get("merged") and facts.get("verification_receipt"):
        categories["cleanup"].extend(["threads", "workspace"])
        steps.append({"kind": "cleanup", "target": "threads_and_workspace", "status": "planned", "implementation_calls": 0, "merge_calls": 0})
    elif facts.get("merged"):
        categories["reverified"].append("merged_candidate")
        steps.append({"kind": "reverify", "target": "merged_candidate", "status": "planned", "reason": "merge exists but delivery evidence is missing"})
    elif specs is None:
        categories["new_work"].append("remaining_acceptance")
        steps.append({"kind": "resume", "target": "remaining_acceptance", "status": "planned", "reason": "continue the normal Runner delivery loop after backfill and reverify"})
    state = "needs_input" if any(step.get("status") == "needs_input" for step in steps) else "planned"
    return {"schema_version": "spec-runner-frontier/v1", "state": state, "categories": categories, "steps": steps, "digest": digest({"report": report.get("digest"), "steps": steps})}


def write_takeover_record(*, control_root: Path, takeover_key: str, report: dict[str, Any], frontier: dict[str, Any]) -> dict[str, object]:
    """Persist takeover intent and observation before any later mutable action."""
    if not takeover_key or any(character.isspace() for character in takeover_key):
        raise RunnerError("invalid_takeover_key", "takeover_key must be non-empty and contain no whitespace")
    control_root = control_root.expanduser().resolve()
    store = Store.open(control_root, create=True)
    try:
        return store.record_takeover(takeover_key=takeover_key, report=report, frontier=frontier)
    finally:
        store.close()
