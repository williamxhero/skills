from __future__ import annotations

import json
import math
import os
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import RunnerError

CONFIG_SCHEMA_VERSION = "spec-runner-config/v1"
DEFAULT_GIT_TIMEOUT_SECONDS = 120.0
DEFAULT_GITHUB_TIMEOUT_SECONDS = 120.0


def _positive_timeout(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunnerError("invalid_config", f"{field} must be a positive finite number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise RunnerError("invalid_config", f"{field} must be a positive finite number")
    return result


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _normalise_relative_path(value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise RunnerError("invalid_config", f"{field} must be a non-empty relative path")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise RunnerError("invalid_config", f"{field} must stay below the control root")
    return candidate


def canonical_repository(path_value: Any, *, timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS) -> Path:
    if not isinstance(path_value, str) or not path_value.strip():
        raise RunnerError("invalid_config", "repository_path must be a non-empty path")
    repository = Path(path_value).expanduser().resolve()
    if not repository.is_dir():
        raise RunnerError("invalid_repository", "repository_path is not an existing directory")
    try:
        result = subprocess.run(
            ["git", "-C", os.fspath(repository), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_positive_timeout(timeout_seconds, "repository Git timeout"),
        )
    except subprocess.TimeoutExpired as exc:
        raise RunnerError(
            "repository_git_timeout",
            "repository Git validation exceeded its bounded timeout",
            details={"args": ["rev-parse", "--show-toplevel"], "timeout_seconds": float(timeout_seconds)},
        ) from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RunnerError("invalid_repository", "repository_path is not a Git worktree") from exc
    git_root = Path(result.stdout.strip()).resolve()
    if repository != git_root:
        raise RunnerError(
            "invalid_repository",
            "repository_path must be the Git worktree root",
            details={"canonical_repository_path": os.fspath(git_root)},
        )
    return git_root


@dataclass(frozen=True)
class RunnerConfig:
    repository_path: Path
    target_ref: str
    artifact_root: Path
    execution_backend: str
    allowed_stages: tuple[str, ...]
    model_name: str
    effort: str
    authorization_roots: tuple[Path, ...]
    delivery_plan: Path | None
    skill_config: Path | None
    skill_roots: tuple[Path, ...]
    workflow_mode: str
    digest: str
    acceptance_checks: tuple[dict[str, Any], ...] = ()
    acceptance_ids: tuple[str, ...] = ()
    github_repository: str | None = None
    github_required_checks: tuple[str, ...] = ()
    github_receipt_root: Path | None = None
    github_base: str | None = None
    github_merge_authorized: bool = False
    github_timeout_seconds: float = DEFAULT_GITHUB_TIMEOUT_SECONDS
    github_required_approvals: int = 0
    github_require_branch_protection: bool = False
    # SR-08 added mandatory production acceptance checks after some runs had
    # already persisted their config fingerprint.  Keep the fingerprint of
    # the same config with an empty acceptance section so those runs can be
    # resumed through the public CLI after the required checks are supplied.
    legacy_acceptance_digest: str = ""
    acceptance_timeout_compatible_digest: str = ""
    legacy_github_policy_digest: str = ""
    github_policy_compatible: bool = False
    acceptance_paths: tuple[str, ...] = ()
    git_timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS

    @classmethod
    def from_file(cls, config_file: Path, control_root: Path) -> "RunnerConfig":
        try:
            raw = config_file.read_bytes()
        except FileNotFoundError as exc:
            raise RunnerError("missing_config", f"config file does not exist: {config_file}") from exc
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RunnerError("invalid_config", "config must be UTF-8 JSON") from exc
        if not isinstance(document, dict):
            raise RunnerError("invalid_config", "config root must be an object")
        if document.get("schema_version") != CONFIG_SCHEMA_VERSION:
            raise RunnerError("invalid_config", f"schema_version must be {CONFIG_SCHEMA_VERSION}")

        raw_git = document.get("git")
        if raw_git is None:
            git_timeout_seconds = DEFAULT_GIT_TIMEOUT_SECONDS
        elif isinstance(raw_git, dict):
            git_timeout_seconds = _positive_timeout(
                raw_git.get("timeout_seconds", DEFAULT_GIT_TIMEOUT_SECONDS),
                "git.timeout_seconds",
            )
        else:
            raise RunnerError("invalid_config", "git must be an object when configured")

        repository_path = canonical_repository(
            document.get("repository_path"), timeout_seconds=git_timeout_seconds
        )
        target_ref = document.get("target_ref")
        if not isinstance(target_ref, str) or not target_ref.strip():
            raise RunnerError("invalid_config", "target_ref must be a non-empty string")
        artifact_root = _normalise_relative_path(document.get("artifact_root"), "artifact_root")
        control_root = control_root.resolve()
        artifact_absolute = (control_root / artifact_root).resolve()
        if not _is_within(artifact_absolute, control_root):
            raise RunnerError("invalid_config", "artifact_root escapes the control root")

        backend = document.get("execution_backend")
        if backend not in {"deterministic_test", "codex_sdk"}:
            raise RunnerError(
                "unsupported_backend",
                "execution_backend must be deterministic_test or codex_sdk",
            )
        stages = document.get("allowed_stages")
        if not isinstance(stages, list) or not stages or any(not isinstance(x, str) or not x for x in stages):
            raise RunnerError("invalid_config", "allowed_stages must be a non-empty list of names")
        model = document.get("model")
        if not isinstance(model, dict) or not isinstance(model.get("name"), str) or not model["name"]:
            raise RunnerError("invalid_config", "model.name must be a non-empty string")
        if not isinstance(model.get("effort"), str) or not model["effort"]:
            raise RunnerError("invalid_config", "model.effort must be a non-empty string")
        authorization = document.get("authorization")
        roots = authorization.get("artifact_roots") if isinstance(authorization, dict) else None
        if not isinstance(roots, list) or not roots:
            raise RunnerError("invalid_config", "authorization.artifact_roots must be a non-empty list")
        authorization_roots = tuple(_normalise_relative_path(root, "authorization.artifact_roots") for root in roots)
        if not any(_is_within(artifact_root, root) for root in authorization_roots):
            raise RunnerError("invalid_config", "artifact_root is outside authorization.artifact_roots")
        delivery = document.get("delivery")
        delivery_plan: Path | None = None
        if delivery is not None:
            if not isinstance(delivery, dict) or "plan" not in delivery:
                raise RunnerError("invalid_config", "delivery.plan is required when delivery is configured")
            delivery_plan = _normalise_relative_path(delivery.get("plan"), "delivery.plan")
            if not (control_root / delivery_plan).is_file():
                raise RunnerError("invalid_config", "delivery.plan does not exist below control_root")
        skills = document.get("skills", {})
        if skills is None:
            skills = {}
        if not isinstance(skills, dict):
            raise RunnerError("invalid_config", "skills must be an object when configured")
        skill_config: Path | None = None
        if skills.get("config") is not None:
            skill_config = _normalise_relative_path(skills["config"], "skills.config")
            if not (control_root / skill_config).is_file():
                raise RunnerError("invalid_config", "skills.config does not exist below control_root")
        raw_skill_roots = skills.get("roots", [])
        if not isinstance(raw_skill_roots, list) or any(not isinstance(item, str) for item in raw_skill_roots):
            raise RunnerError("invalid_config", "skills.roots must be a list of paths")
        skill_roots = tuple(Path(item).expanduser() for item in raw_skill_roots)
        workflow = document.get("workflow", {})
        if workflow is None:
            workflow = {}
        if not isinstance(workflow, dict) or workflow.get("mode", "example") not in {"example", "production"}:
            raise RunnerError("invalid_config", "workflow.mode must be example or production")
        workflow_mode = str(workflow.get("mode", "example"))
        raw_acceptance = workflow.get("acceptance", {})
        if raw_acceptance is None:
            raw_acceptance = {}
        if not isinstance(raw_acceptance, dict):
            raise RunnerError("invalid_config", "workflow.acceptance must be an object")
        acceptance_ids = raw_acceptance.get("ids", [])
        checks = raw_acceptance.get("checks", [])
        raw_paths = raw_acceptance.get("write_scope", [])
        if not isinstance(acceptance_ids, list) or any(not isinstance(item, str) or not item.strip() for item in acceptance_ids):
            raise RunnerError("invalid_config", "workflow.acceptance.ids must be a list of non-empty strings")
        if not isinstance(checks, list) or any(not isinstance(item, dict) for item in checks):
            raise RunnerError("invalid_config", "workflow.acceptance.checks must be a list of objects")
        if not isinstance(raw_paths, list) or any(not isinstance(item, str) or not item.strip() for item in raw_paths):
            raise RunnerError("invalid_config", "workflow.acceptance.write_scope must be a list of repository-relative paths")
        acceptance_paths: list[str] = []
        for item in raw_paths:
            if "\\" in item:
                raise RunnerError("invalid_config", "workflow.acceptance.write_scope paths must use forward slashes")
            path = PurePosixPath(item)
            if path.is_absolute() or ".." in path.parts or not path.parts or str(path) in {".", ""} or ":" in item:
                raise RunnerError("invalid_config", "workflow.acceptance.write_scope paths must stay inside the repository")
            normalized_path = path.as_posix().rstrip("/")
            if normalized_path not in acceptance_paths:
                acceptance_paths.append(normalized_path)
        if workflow_mode == "production" and backend == "codex_sdk" and len(acceptance_paths) != 1:
            raise RunnerError("invalid_config", "production workflow requires exactly one workflow.acceptance.write_scope root")
        for check in checks:
            command = check.get("command")
            mapped = check.get("acceptance")
            if not isinstance(command, list) or not command or any(not isinstance(part, str) or not part for part in command):
                raise RunnerError("invalid_config", "workflow acceptance checks need command arrays")
            if not isinstance(mapped, list) or not mapped or any(item not in acceptance_ids for item in mapped):
                raise RunnerError("invalid_config", "workflow acceptance checks need valid acceptance IDs")
        github = document.get("github")
        if github is None:
            github = {}
        if not isinstance(github, dict):
            raise RunnerError("invalid_config", "github must be an object when configured")
        github_timeout_seconds = _positive_timeout(
            github.get("timeout_seconds", DEFAULT_GITHUB_TIMEOUT_SECONDS),
            "github.timeout_seconds",
        )
        github_repository = github.get("repository")
        if github_repository is not None and (not isinstance(github_repository, str) or not github_repository or "/" not in github_repository or any(character.isspace() for character in github_repository)):
            raise RunnerError("invalid_config", "github.repository must be owner/name")
        github_checks = github.get("required_checks", [])
        if not isinstance(github_checks, list) or any(not isinstance(item, str) or not item.strip() for item in github_checks):
            raise RunnerError("invalid_config", "github.required_checks must be a list of names")
        github_receipt = None
        if github.get("receipt_root") is not None:
            github_receipt = _normalise_relative_path(github["receipt_root"], "github.receipt_root")
        github_base = github.get("base", target_ref)
        if not isinstance(github_base, str) or not github_base.strip():
            raise RunnerError("invalid_config", "github.base must be a non-empty ref")
        github_authorized = github.get("merge_authorized", False)
        if not isinstance(github_authorized, bool):
            raise RunnerError("invalid_config", "github.merge_authorized must be boolean")
        github_required_approvals = github.get("required_approvals", 0)
        if (isinstance(github_required_approvals, bool)
                or not isinstance(github_required_approvals, int)
                or github_required_approvals < 0):
            raise RunnerError("invalid_config", "github.required_approvals must be a non-negative integer")
        github_require_branch_protection = github.get("require_branch_protection", False)
        if not isinstance(github_require_branch_protection, bool):
            raise RunnerError("invalid_config", "github.require_branch_protection must be boolean")

        normalized = {
            "schema_version": CONFIG_SCHEMA_VERSION,
            "repository_path": os.path.normcase(os.fspath(repository_path)),
            "target_ref": target_ref,
            "artifact_root": artifact_root.as_posix(),
            "execution_backend": backend,
            "allowed_stages": stages,
            "model": {"name": model["name"], "effort": model["effort"]},
            "authorization": {"artifact_roots": [root.as_posix() for root in authorization_roots]},
            "delivery": {"plan": delivery_plan.as_posix()} if delivery_plan else None,
            "skills": {"config": skill_config.as_posix() if skill_config else None, "roots": [os.fspath(item) for item in skill_roots]},
            "workflow": {"mode": workflow_mode, "acceptance": {"ids": acceptance_ids, "checks": checks, "write_scope": acceptance_paths}},
            "github": {"repository": github_repository, "required_checks": github_checks, "receipt_root": github_receipt.as_posix() if github_receipt else None, "base": github_base, "merge_authorized": github_authorized, "required_approvals": github_required_approvals, "require_branch_protection": github_require_branch_protection},
        }
        if "timeout_seconds" in github:
            normalized["github"]["timeout_seconds"] = github_timeout_seconds
        # Keep pre-timeout config digests stable so persisted runs remain
        # resumable while an explicit timeout binds new runs.
        if raw_git is not None:
            normalized["git"] = {"timeout_seconds": git_timeout_seconds}
        legacy_github_normalized = json.loads(_canonical_json(normalized))
        legacy_github_normalized["github"].pop("required_approvals", None)
        legacy_github_normalized["github"].pop("require_branch_protection", None)
        compatibility_normalized = json.loads(_canonical_json(normalized))
        compatibility_normalized["workflow"]["acceptance"] = {"ids": [], "checks": []}
        timeout_compatible_normalized = json.loads(_canonical_json(normalized))
        for check in timeout_compatible_normalized["workflow"]["acceptance"]["checks"]:
            check.pop("timeout_seconds", None)
        return cls(
            repository_path=repository_path,
            target_ref=target_ref,
            artifact_root=artifact_root,
            execution_backend=backend,
            allowed_stages=tuple(stages),
            model_name=model["name"],
            effort=model["effort"],
            authorization_roots=authorization_roots,
            delivery_plan=delivery_plan,
            skill_config=(control_root / skill_config).resolve() if skill_config else None,
            skill_roots=skill_roots,
            workflow_mode=workflow_mode,
            acceptance_checks=tuple(dict(item) for item in checks),
            acceptance_ids=tuple(acceptance_ids),
            digest=digest_bytes(_canonical_json(normalized).encode("utf-8")),
            legacy_acceptance_digest=digest_bytes(_canonical_json(compatibility_normalized).encode("utf-8")),
            acceptance_timeout_compatible_digest=digest_bytes(
                _canonical_json(timeout_compatible_normalized).encode("utf-8")
            ),
            legacy_github_policy_digest=digest_bytes(
                _canonical_json(legacy_github_normalized).encode("utf-8")
            ),
            github_policy_compatible=(github_required_approvals == 0 and not github_require_branch_protection),
            acceptance_paths=tuple(acceptance_paths),
            github_repository=github_repository,
            github_required_checks=tuple(github_checks),
            github_receipt_root=(control_root / github_receipt).resolve() if github_receipt else None,
            github_base=github_base if github_repository else None,
            github_merge_authorized=github_authorized,
            github_timeout_seconds=github_timeout_seconds,
            github_required_approvals=github_required_approvals,
            github_require_branch_protection=github_require_branch_protection,
            git_timeout_seconds=git_timeout_seconds,
        )


def read_brief(path: Path) -> tuple[str, str]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise RunnerError("missing_brief", f"brief file does not exist: {path}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RunnerError("invalid_brief", "brief must be UTF-8") from exc
    if not text.strip():
        raise RunnerError("invalid_brief", "brief must not be empty")
    return text, digest_bytes(raw)
