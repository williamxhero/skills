from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from .errors import RunnerError

CONFIG_SCHEMA_VERSION = "spec-runner-config/v1"


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


def canonical_repository(path_value: Any) -> Path:
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
        )
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
    digest: str

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

        repository_path = canonical_repository(document.get("repository_path"))
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
        }
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
            digest=digest_bytes(_canonical_json(normalized).encode("utf-8")),
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
