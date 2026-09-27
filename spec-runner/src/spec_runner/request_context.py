"""Durable input needed to reopen a Runner request after process exit."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .errors import RunnerError


SCHEMA_VERSION = "spec-runner-run-request/v1"
FILE_NAME = "run-request.json"


@dataclass(frozen=True)
class RequestContext:
    """The original public inputs needed to resume one durable run."""

    run_id: str
    launch_key: str
    brief_file: Path
    config_file: Path
    brief_digest: str
    config_digest: str

    def public(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "launch_key": self.launch_key,
            "brief_file": str(self.brief_file),
            "config_file": str(self.config_file),
            "brief_digest": self.brief_digest,
            "config_digest": self.config_digest,
        }


def context_path(artifact_directory: Path) -> Path:
    return artifact_directory / FILE_NAME


def write_context(path: Path, context: RequestContext) -> None:
    """Write the immutable request identity atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(context.public(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def read_context(path: Path, *, run_id: str) -> RequestContext:
    """Read and validate a request record without changing durable state."""
    if path.is_symlink() or not path.is_file():
        raise RunnerError(
            "answer_continuation_missing",
            "the waiting run has no durable request context",
            details={"run_id": run_id},
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError(
            "answer_continuation_invalid",
            "the durable request context is not readable JSON",
            details={"run_id": run_id},
        ) from exc
    if not isinstance(document, Mapping) or document.get("schema_version") != SCHEMA_VERSION:
        raise RunnerError(
            "answer_continuation_invalid",
            "the durable request context has an unsupported schema",
            details={"run_id": run_id},
        )
    keys = ("run_id", "launch_key", "brief_file", "config_file", "brief_digest", "config_digest")
    values = {key: document.get(key) for key in keys}
    if values["run_id"] != run_id or any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise RunnerError(
            "answer_continuation_invalid",
            "the durable request context does not match the waiting run",
            details={"run_id": run_id},
        )
    brief_file = Path(str(values["brief_file"])).expanduser()
    config_file = Path(str(values["config_file"])).expanduser()
    if not brief_file.is_absolute() or not config_file.is_absolute():
        raise RunnerError(
            "answer_continuation_invalid",
            "the durable request context must use absolute input paths",
            details={"run_id": run_id},
        )
    return RequestContext(
        run_id=run_id,
        launch_key=str(values["launch_key"]),
        brief_file=brief_file,
        config_file=config_file,
        brief_digest=str(values["brief_digest"]),
        config_digest=str(values["config_digest"]),
    )
