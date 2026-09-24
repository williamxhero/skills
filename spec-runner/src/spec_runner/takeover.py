"""Evidence-first takeover of ordinary threads and partially completed work."""
from __future__ import annotations

import json
import hashlib
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


def _git_bytes(path: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(["git", "-C", os.fspath(path), *args], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RunnerError("takeover_repository_invalid", "takeover repository is not a readable Git worktree") from exc
    return result.stdout


def _file_digest(path: Path) -> tuple[str, int, str]:
    if path.is_symlink():
        content = os.readlink(path).encode("utf-8", errors="surrogateescape")
        return hashlib.sha256(content).hexdigest(), len(content), "symlink"
    if not path.is_file():
        return hashlib.sha256(b"").hexdigest(), 0, "other"
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size, "file"


def _sensitive_path(relative: str) -> bool:
    parts = [part.lower() for part in relative.replace("\\", "/").split("/")]
    return (
        any(part in {".env", ".env.local", ".env.production", "credentials", "credentials.json", "secrets.json"} for part in parts)
        or any(token in part for part in parts for token in ("secret", "password", "credential", "access_token", "api_key"))
        or any(part.endswith((".pem", ".key", ".p12", ".pfx")) for part in parts)
    )


def _path_is_within(path: Path, repository: Path) -> bool:
    """Compare filesystem paths after resolving aliases and symlinks."""
    repository_path = os.path.normcase(os.path.realpath(os.fspath(repository)))
    candidate_path = os.path.normcase(os.path.realpath(os.fspath(path)))
    try:
        return os.path.commonpath((repository_path, candidate_path)) == repository_path
    except ValueError:
        return False


def _status_entries(repository: Path) -> tuple[list[dict[str, object]], int]:
    entries = _git_bytes(repository, "status", "--porcelain=v1", "--untracked-files=all", "-z").split(b"\0")
    projected: list[dict[str, object]] = []
    redacted = 0
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        status = entry[:2].decode("ascii", errors="replace")
        path = entry[3:].decode("utf-8", errors="surrogateescape")
        original_path = None
        if "R" in status or "C" in status:
            if index < len(entries):
                original_path = entries[index].decode("utf-8", errors="surrogateescape")
                index += 1
        if _sensitive_path(path) or (original_path is not None and _sensitive_path(original_path)):
            redacted += 1
            continue
        projected.append({"path": path, "index_status": status[0], "worktree_status": status[1], "original_path": original_path})
    return projected, redacted


def _working_tree_snapshot(repository: Path) -> dict[str, object]:
    """Capture identifiers and digests without copying user files or secrets."""
    status_entries, redacted_path_count = _status_entries(repository)
    index_entries = []
    for raw in _git_bytes(repository, "ls-files", "-s", "-z").split(b"\0"):
        if not raw:
            continue
        metadata, path_bytes = raw.split(b"\t", 1)
        fields = metadata.decode("ascii", errors="replace").split()
        if len(fields) < 3:
            continue
        relative = path_bytes.decode("utf-8", errors="surrogateescape")
        if _sensitive_path(relative):
            continue
        index_entries.append({"mode": fields[0], "blob_sha": fields[1], "stage": fields[2], "path": relative})
    untracked = []
    for path_bytes in _git_bytes(repository, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
        if not path_bytes:
            continue
        relative = path_bytes.decode("utf-8", errors="surrogateescape")
        if _sensitive_path(relative):
            continue
        candidate = (repository / relative).resolve()
        if not _path_is_within(candidate, repository):
            raise RunnerError("takeover_path_escape", "untracked path escapes repository")
        file_sha, size, kind = _file_digest(repository / relative)
        untracked.append({"path": relative, "sha256": file_sha, "size": size, "kind": kind})
    staged_diff = _git_bytes(repository, "diff", "--cached", "--binary")
    unstaged_diff = _git_bytes(repository, "diff", "--binary")
    return {
        "consistency": "observed_without_source_stop_proof",
        "index": index_entries,
        "untracked": untracked,
        "changed": [str(entry["path"]) for entry in status_entries],
        "status_entries": status_entries,
        "redacted_path_count": redacted_path_count,
        "staged_diff_sha256": hashlib.sha256(staged_diff).hexdigest(),
        "unstaged_diff_sha256": hashlib.sha256(unstaged_diff).hexdigest(),
        "snapshot_digest": digest({"status_entries": status_entries, "redacted_path_count": redacted_path_count, "index": index_entries, "untracked": untracked, "staged": hashlib.sha256(staged_diff).hexdigest(), "unstaged": hashlib.sha256(unstaged_diff).hexdigest()}),
    }


def inventory_from_thread_observation(
    *,
    observation: dict[str, object],
    repository: Path,
    handover_policy: str = "require_stop_confirmation",
    scope: list[str] | None = None,
) -> dict[str, object]:
    """Build the smallest takeover input from an actual SDK observation."""
    thread_id = observation.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise RunnerError("invalid_thread_observation", "source observation has no stable thread id")
    completeness = observation.get("completeness")
    if not isinstance(completeness, dict):
        raise RunnerError("invalid_thread_observation", "source observation has no completeness record")
    changed_paths = []
    snapshot = _working_tree_snapshot(repository)
    changed_paths = [str(path) for path in snapshot.get("changed", []) if isinstance(path, str)]
    if scope is not None:
        normalized_scope = [item.replace("\\", "/").strip("/") for item in scope if item.strip()]
        outside = sorted(path for path in changed_paths if not any(path == root or path.startswith(root + "/") for root in normalized_scope))
    else:
        normalized_scope = []
        outside = []
    thread_status = observation.get("thread_status")
    active_flags = observation.get("active_flags")
    source_active = thread_status in {"active", "inProgress", "running"}
    if isinstance(active_flags, list) and active_flags:
        source_active = True
    facts = {
        "requirements_material": observation.get("business_items", []),
        "source_observation": observation,
        "working_tree": snapshot,
        "partial_code": bool(changed_paths),
        "scope": normalized_scope,
        "scope_outside_paths": outside,
        "source_history_complete": completeness.get("state") == "complete",
    }
    if outside:
        facts["scope_error"] = "working tree contains paths outside the authorized scope"
    elif normalized_scope and snapshot.get("redacted_path_count"):
        facts["scope_error"] = "redacted working-tree paths prevent proving the complete authorized scope"
    return {
        "schema_version": "spec-runner-takeover-input/v1",
        "repository_path": os.fspath(repository.resolve()),
        "handover_policy": handover_policy,
        "source_threads": [{
            "id": thread_id,
            "ownership": "unknown",
            "active": source_active,
            "stop_confirmed": False,
            "observation": observation,
            "lineage": observation.get("thread", {}).get("forked_from_id") if isinstance(observation.get("thread"), dict) else None,
        }],
        "artifacts": [{"path": path} for path in sorted(set(changed_paths))],
        "facts": facts,
    }


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
    snapshot = _working_tree_snapshot(repository)
    branch = _git(repository, "branch", "--show-current")
    try:
        latest_commit = _git(repository, "log", "-1", "--format=%H%x00%s")
    except RunnerError:
        latest_commit = ""
    dirty = bool(snapshot["status_entries"] or snapshot["redacted_path_count"])
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
        observation = thread.get("observation")
        if isinstance(observation, dict) and observation.get("thread_status") == "active":
            active = True
        if ownership not in {"confirmed", "unknown", "preserve"}:
            raise RunnerError("invalid_takeover_inventory", "thread ownership must be confirmed, unknown, or preserve")
        observation_complete = True
        if isinstance(observation, dict):
            completeness = observation.get("completeness")
            observation_complete = isinstance(completeness, dict) and completeness.get("state") == "complete"
        handover_evidence = thread.get("handover_evidence")
        handover_ready = _valid_handover_evidence(identifier, handover_evidence)
        if not observation_complete:
            unresolved.append({"thread_id": identifier, "reason": "source_history_incomplete"})
        elif handover_ready:
            adopted.append({
                "thread_id": identifier,
                "state": "released",
                "handover": handover_evidence,
            })
        elif ownership != "confirmed":
            unresolved.append({"thread_id": identifier, "reason": "thread_ownership_unconfirmed"})
        else:
            unresolved.append(
                {
                    "thread_id": identifier,
                    "reason": "waiting_handover" if handover_policy == "wait_then_takeover" else "source_stop_unproven",
                    "policy": handover_policy,
                }
            )
    artifacts = inventory.get("artifacts", [])
    if not isinstance(artifacts, list) or any(not isinstance(item, dict) for item in artifacts):
        raise RunnerError("invalid_takeover_inventory", "artifacts must be a list of objects")
    classifications = []
    for artifact in artifacts:
        path = artifact.get("path")
        if not isinstance(path, str):
            raise RunnerError("invalid_takeover_inventory", "artifact path is required")
        candidate = (repository / path).resolve()
        if not _path_is_within(candidate, repository):
            raise RunnerError("takeover_path_escape", "takeover artifact escapes repository")
        classifications.append({"path": path, "exists": candidate.exists(), "state": "adopted" if candidate.exists() else "missing_evidence"})
    facts = inventory.get("facts", {})
    if not isinstance(facts, dict):
        raise RunnerError("invalid_takeover_inventory", "facts must be an object")
    if facts.get("scope_error"):
        unresolved.append({"reason": "scope_outside_authorization", "detail": str(facts["scope_error"])})
    # Historical claims are intentionally not converted into current verification.
    return {
        "schema_version": "spec-runner-takeover-report/v1",
        "repository": os.fspath(repository),
        "head_sha": head,
        "repository_snapshot": {
            "branch": branch or None,
            "head_sha": head,
            "latest_commit": latest_commit or None,
            "dirty": dirty,
            "changed_paths": snapshot["changed"],
            "redacted_path_count": snapshot["redacted_path_count"],
            "snapshot_digest": snapshot["snapshot_digest"],
        },
        "working_tree": {"dirty": dirty, "snapshot_digest": snapshot["snapshot_digest"]},
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
        "digest": digest({"repo": os.fspath(repository), "snapshot": {"branch": branch, "head": head, "snapshot_digest": snapshot["snapshot_digest"]}, "threads": source_threads, "artifacts": classifications, "facts": facts, "handover_policy": handover_policy}),
    }


def _valid_handover_evidence(thread_id: str, evidence: object) -> bool:
    """Accept only a complete SDK handover readback, never caller booleans."""
    if not isinstance(evidence, dict):
        return False
    limits = evidence.get("evidence_limits")
    readback = evidence.get("readback")
    return (
        evidence.get("schema_version") == "spec-runner-sdk-thread-interrupt/v1"
        and evidence.get("accepted") is True
        and evidence.get("thread_id") == thread_id
        and evidence.get("source_writer_state") == "stopped"
        and evidence.get("dispatcher_state") == "quiesced"
        and isinstance(limits, dict)
        and limits.get("source_stop_confirmed") is True
        and limits.get("dispatcher_quiesced") is True
        and limits.get("ownership_transferred") is True
        and isinstance(readback, dict)
        and readback.get("source_thread_id") == thread_id
        and readback.get("observed_status") in {"completed", "idle", "archived"}
    )


def completion_action(report: dict[str, Any]) -> dict[str, object]:
    """Choose only a mechanical next category; no hidden LLM control loop."""
    if report.get("next_state") == "waiting_handover":
        return {"state": "waiting_handover", "reason": "the selected handover policy requires a real stop confirmation before a new writer starts"}
    if report.get("next_state") == "blocked":
        return {"state": "blocked", "reason": "takeover ownership or active-writer evidence is incomplete"}
    facts = report.get("historical_facts", {})
    if _authoritative_delivery_present(report):
        return {"state": "cleanup_pending", "implementation_calls": 0, "merge_calls": 0}
    if isinstance(facts, dict) and (facts.get("merged") or facts.get("verification_receipt")):
        return {
            "state": "reverify_delivery",
            "implementation_calls": 0,
            "merge_calls": 0,
            "requires": "authoritative repository, tracker, and acceptance readback",
        }
    return {"state": "resume_delivery", "implementation_calls": 0, "merge_calls": 0, "requires": "normal Runner stage loop"}


def perform_cleanup(report: dict[str, Any], *, prior_cleanup: dict[str, object] | None = None) -> dict[str, object]:
    """Execute cleanup only after current delivery evidence is revalidated.

    A historical merge receipt or arbitrary verification object is not a path
    authorization. Cleanup is attempted only when candidate, review, merge,
    and repository readbacks bind to the current repository, and the delivery
    helper independently verifies workspace ownership.
    """
    facts = report.get("historical_facts", {})
    if not _authoritative_delivery_present(report) or not isinstance(facts, dict):
        raise RunnerError(
            "takeover_cleanup_not_authorized",
            "cleanup requires authoritative delivery, ownership, and cleanup readbacks; caller supplied historical facts are insufficient",
        )
    targets = facts.get("cleanup_targets", [])
    if not isinstance(targets, list):
        raise RunnerError("invalid_takeover_cleanup", "cleanup_targets must be a list")
    adopted_threads = report.get("adopted_threads", [])
    if not isinstance(adopted_threads, list):
        raise RunnerError("invalid_takeover_cleanup", "adopted_threads must be a list")
    if not targets and not adopted_threads:
        return {"outcome": "pending", "reason": "cleanup_targets_missing", "attempted": 0, "results": []}
    from .delivery import cleanup_managed_workspace
    from .codex_adapter import CodexAdapter

    repository = Path(str(report["repository"])).resolve()
    results: list[dict[str, object]] = []
    for target in targets:
        if not isinstance(target, dict) or not all(isinstance(target.get(field), str) and target[field].strip() for field in ("workspace_root", "workspace")):
            raise RunnerError("invalid_takeover_cleanup", "each cleanup target needs workspace_root and workspace")
        workspace_root = Path(str(target["workspace_root"])).expanduser().resolve()
        workspace = Path(str(target["workspace"])).expanduser().resolve()
        manifest_value = target.get("manifest")
        manifest = Path(str(manifest_value)).expanduser().resolve() if isinstance(manifest_value, str) and manifest_value.strip() else None
        results.append(cleanup_managed_workspace(repository=repository, workspace_root=workspace_root, workspace=workspace, manifest=manifest))
    thread_results: list[dict[str, object]] = []
    prior_thread_values = prior_cleanup.get("thread_results", []) if isinstance(prior_cleanup, dict) else []
    prior_threads = {
        str(item.get("thread_id")): item
        for item in (prior_thread_values if isinstance(prior_thread_values, list) else [])
        if isinstance(item, dict) and isinstance(item.get("thread_id"), str)
    }
    for item in adopted_threads:
        if not isinstance(item, dict) or not isinstance(item.get("thread_id"), str) or not item["thread_id"].strip():
            raise RunnerError("invalid_takeover_cleanup", "each adopted thread needs a stable thread_id")
        previous = prior_threads.get(str(item["thread_id"]))
        if isinstance(previous, dict) and previous.get("outcome") == "archived" and isinstance(previous.get("receipt"), dict):
            receipt = previous["receipt"]
            if receipt.get("thread_id") == item["thread_id"] and receipt.get("archived") is True:
                thread_results.append({**previous, "replayed": True})
                continue
        try:
            receipt = CodexAdapter().archive_and_readback(
                thread_id=str(item["thread_id"]), repository_path=repository,
            )
            if not isinstance(receipt, dict) or receipt.get("thread_id") != item["thread_id"] or receipt.get("archived") is not True:
                raise RunnerError("takeover_archive_readback_failed", "source thread archive readback did not match its identity")
            thread_results.append({"thread_id": item["thread_id"], "outcome": "archived", "receipt": receipt})
        except RunnerError as exc:
            thread_results.append({"thread_id": item["thread_id"], "outcome": "pending", "error_code": exc.code})
    outcome = "cleaned" if (
        all(item.get("outcome") == "cleaned" for item in results)
        and all(item.get("outcome") == "archived" for item in thread_results)
    ) else "pending"
    return {
        "outcome": outcome,
        "attempted": len(results) + len(thread_results),
        "results": results,
        "thread_results": thread_results,
    }


def _authoritative_delivery_present(report: dict[str, Any]) -> bool:
    """Require current Git readback before treating a delivery as cleanupable."""
    facts = report.get("historical_facts", {})
    if not isinstance(facts, dict):
        return False
    delivery = facts.get("authoritative_delivery")
    if not isinstance(delivery, dict):
        return False
    candidate_sha = delivery.get("candidate_sha")
    merge_sha = delivery.get("merge_sha")
    candidate = delivery.get("candidate_receipt")
    review = delivery.get("review")
    merge = delivery.get("merge")
    if (
        not isinstance(candidate_sha, str) or len(candidate_sha) != 40
        or not isinstance(merge_sha, str) or len(merge_sha) != 40
        or not isinstance(candidate, dict) or candidate.get("outcome") != "verified" or candidate.get("candidate_sha") != candidate_sha
        or not isinstance(review, dict) or review.get("approved") is not True or review.get("candidate_sha") != candidate_sha
        or not isinstance(merge, dict) or merge.get("outcome") != "merged" or merge.get("merge_sha") != merge_sha
    ):
        return False
    repository = Path(str(report.get("repository", ""))).resolve()
    try:
        if _git(repository, "rev-parse", candidate_sha) != candidate_sha:
            return False
        if _git(repository, "rev-parse", merge_sha) != merge_sha:
            return False
        subprocess.run(["git", "-C", os.fspath(repository), "merge-base", "--is-ancestor", merge_sha, "HEAD"], check=True, capture_output=True)
    except (RunnerError, OSError, subprocess.CalledProcessError):
        return False
    return True


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
            if state in {"completed", "merged", "delivered"}:
                categories["reverified"].append(key)
                categories["remaining"].append(key)
                steps.append({"kind": "reverify", "target": key, "status": "planned", "reason": "caller supplied completion claims require authoritative delivery readback"})
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

    if not facts.get("requirements"):
        source_material = facts.get("requirements_material")
        material_available = isinstance(source_material, list) and bool(source_material)
        categories["backfilled"].append("requirement_scope")
        steps.append({
            "kind": "backfill",
            "target": "requirement_scope",
            "status": "planned" if material_available else "needs_input",
            "reason": (
                "source business material is available for semantic scope reconstruction"
                if material_available else
                "original requirement scope is not present in the inventory"
            ),
        })
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
    if facts.get("merged") or facts.get("verification_receipt"):
        categories["reverified"].append("merged_candidate")
        steps.append({"kind": "reverify", "target": "merged_candidate", "status": "planned", "reason": "caller supplied delivery claims require authoritative repository and acceptance readback"})
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


def record_takeover_transition(
    *,
    control_root: Path,
    takeover_key: str,
    state: str,
    event_key: str,
    payload: dict[str, object],
) -> dict[str, object]:
    """Persist one resumable takeover state transition under the same key."""
    control_root = control_root.expanduser().resolve()
    store = Store.open(control_root, create=True)
    try:
        result = store.update_takeover_record(
            takeover_key=takeover_key,
            state=state,
            event_key=event_key,
            payload=payload,
        )
        result["transitions"] = store.takeover_transitions(takeover_key)
        return result
    finally:
        store.close()


def refresh_takeover_evidence(
    *,
    control_root: Path,
    takeover_key: str,
    report: dict[str, Any],
    frontier: dict[str, Any],
    event_key: str,
    payload: dict[str, object],
) -> dict[str, object]:
    """Replace the observed report/frontier after a durable handover readback."""
    control_root = control_root.expanduser().resolve()
    store = Store.open(control_root, create=True)
    try:
        result = store.refresh_takeover_evidence(
            takeover_key=takeover_key,
            report=report,
            frontier=frontier,
            event_key=event_key,
            payload=payload,
        )
        result["transitions"] = store.takeover_transitions(takeover_key)
        return result
    finally:
        store.close()
