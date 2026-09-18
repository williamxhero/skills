"""Immutable run authorization snapshots and side-effect-free scope checks."""
from __future__ import annotations

import fnmatch
import hashlib
import json
from typing import Any


AUTHORIZATION_SCHEMA_VERSION = 1
REQUIRED_FIELDS = frozenset({
    "repository", "target_ref", "allowed_paths", "allowed_tasks",
    "allowed_actions", "deployment_target", "full_project_submission",
})


class AuthorizationError(ValueError):
    def __init__(self, code: str, details: dict[str, Any] | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(f"{code}: {self.details}")


def _reject(code: str, **details: Any) -> None:
    raise AuthorizationError(code, details)


def validate_authorization(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        _reject("authorization_missing")
    if value.get("schema_version") != AUTHORIZATION_SCHEMA_VERSION:
        _reject("authorization_schema_version", expected=AUTHORIZATION_SCHEMA_VERSION)
    missing = sorted(REQUIRED_FIELDS - set(value))
    if missing:
        _reject("authorization_incomplete", missing=missing)
    repository = value["repository"]
    if not isinstance(repository, dict) or not isinstance(repository.get("id"), str) or not repository["id"].strip():
        _reject("authorization_repository_missing")
    if not isinstance(value["target_ref"], str) or not value["target_ref"].strip():
        _reject("authorization_target_ref_missing")
    for field in ("allowed_paths", "allowed_tasks", "allowed_actions"):
        items = value[field]
        if not isinstance(items, list) or any(not isinstance(item, str) or not item.strip() for item in items):
            _reject("authorization_list_invalid", field=field)
        if len(set(items)) != len(items):
            _reject("authorization_list_duplicate", field=field)
    target = value["deployment_target"]
    if target is not None and (not isinstance(target, str) or not target.strip()):
        _reject("authorization_deployment_invalid")
    if not isinstance(value["full_project_submission"], bool):
        _reject("authorization_full_project_invalid")
    if value["full_project_submission"] and "full-project-sync" not in value["allowed_actions"]:
        _reject("authorization_full_project_action_missing")
    return value


def authorization_digest(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_scope(
    authorization: Any,
    *,
    action: str,
    path: str | None = None,
    target_ref: str | None = None,
    environment: str | None = None,
    full_project: bool = False,
) -> dict[str, Any]:
    value = validate_authorization(authorization)
    if action not in value["allowed_actions"]:
        _reject("authorization_action_denied", action=action)
    if target_ref is not None and target_ref != value["target_ref"]:
        _reject("authorization_ref_denied", expected=value["target_ref"], actual=target_ref)
    if path is not None and not any(fnmatch.fnmatchcase(path, pattern) for pattern in value["allowed_paths"]):
        _reject("authorization_path_denied", path=path)
    if environment is not None and environment != value["deployment_target"]:
        _reject("authorization_environment_denied", expected=value["deployment_target"], actual=environment)
    if full_project and not value["full_project_submission"]:
        _reject("authorization_full_project_denied")
    return {
        "decision": "allow",
        "authorization_digest": authorization_digest(value),
        "repository": value["repository"],
        "target_ref": value["target_ref"],
        "action": action,
        "path": path,
        "environment": environment,
        "full_project": full_project,
    }
