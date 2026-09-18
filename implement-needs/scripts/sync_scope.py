"""Run-scoped synchronization planning and candidate-bound readback checks."""
from __future__ import annotations

import fnmatch
from typing import Any

from authorization import AuthorizationError, authorization_digest, check_scope, validate_authorization


class SyncScopeError(ValueError):
    def __init__(self, code: str, details: dict[str, Any] | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(f"{code}: {self.details}")


def _allowed(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def build_sync_plan(authorization: Any, changed_paths: list[str], *, requested_paths: list[str] | None = None, full_project: bool = False) -> dict[str, Any]:
    value = validate_authorization(authorization)
    action = "full-project-sync" if full_project else "run-scoped-sync"
    check_scope(value, action=action, full_project=full_project)
    if not isinstance(changed_paths, list) or any(not isinstance(path, str) or not path.strip() for path in changed_paths):
        raise SyncScopeError("sync_paths_invalid")
    requested = [] if requested_paths is None else requested_paths
    if not isinstance(requested, list) or any(not isinstance(path, str) or not path.strip() for path in requested):
        raise SyncScopeError("sync_requested_paths_invalid")
    if full_project:
        sync_paths = list(changed_paths)
        preserved = []
    else:
        out_of_scope_requested = [path for path in requested if not _allowed(path, value["allowed_paths"])]
        if out_of_scope_requested:
            raise SyncScopeError("sync_scope_expansion_denied", {"paths": out_of_scope_requested})
        sync_paths = [path for path in changed_paths if _allowed(path, value["allowed_paths"])]
        preserved = [path for path in changed_paths if path not in sync_paths]
    return {
        "decision": "allow",
        "mode": "full-project" if full_project else "run-scoped",
        "authorization_digest": authorization_digest(value),
        "target_ref": value["target_ref"],
        "sync_paths": sync_paths,
        "preserved_unrelated": preserved,
    }


def validate_sync_readback(readback: Any, *, candidate_sha: str, target_ref: str, authorization_digest_value: str, repository_id: str, full_project: bool) -> dict[str, Any]:
    if not isinstance(readback, dict):
        raise SyncScopeError("sync_readback_missing")
    required = {"status", "candidate_sha", "authorization_digest", "target_ref", "repository_id", "local_head", "remote_head", "evidence", "full_project"}
    missing = sorted(required - set(readback))
    if missing:
        raise SyncScopeError("sync_readback_incomplete", {"missing": missing})
    if readback["status"] != "verified":
        raise SyncScopeError("sync_readback_unverified", {"status": readback["status"]})
    if readback["candidate_sha"] != candidate_sha or readback["local_head"] != candidate_sha or readback["remote_head"] != candidate_sha:
        raise SyncScopeError("sync_candidate_mismatch", {"candidate_sha": candidate_sha, "local_head": readback.get("local_head"), "remote_head": readback.get("remote_head")})
    if readback["target_ref"] != target_ref:
        raise SyncScopeError("sync_ref_mismatch", {"expected": target_ref, "actual": readback["target_ref"]})
    if readback["repository_id"] != repository_id:
        raise SyncScopeError("sync_repository_mismatch", {"expected": repository_id, "actual": readback["repository_id"]})
    if readback["authorization_digest"] != authorization_digest_value:
        raise SyncScopeError("sync_authorization_mismatch")
    if not isinstance(readback["full_project"], bool):
        raise SyncScopeError("sync_mode_invalid")
    if readback["full_project"] is not full_project:
        raise SyncScopeError("sync_mode_mismatch", {"expected": full_project, "actual": readback["full_project"]})
    if not isinstance(readback["evidence"], list) or not readback["evidence"]:
        raise SyncScopeError("sync_readback_evidence_missing")
    return readback
