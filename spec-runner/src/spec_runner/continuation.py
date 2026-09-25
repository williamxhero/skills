"""Small, verifiable business handoff bundles for clean recovery threads."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import RunnerError


SCHEMA_VERSION = "spec-runner-continuation-bundle/v1"
_FORBIDDEN_KEYS = {"encrypted_content", "reasoning", "opaque_compaction", "response_chain", "credentials", "secrets"}


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _safe(value: Any, *, field: str) -> Any:
    if isinstance(value, Mapping):
        if any(str(key).lower() in _FORBIDDEN_KEYS for key in value):
            raise RunnerError("continuation_forbidden_material", f"continuation bundle cannot include {field} hidden or encrypted material")
        return {str(key): _safe(item, field=f"{field}.{key}") for key, item in value.items()}
    if isinstance(value, list):
        return [_safe(item, field=field) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and len(value) > 20_000:
            raise RunnerError("continuation_field_too_large", f"continuation field is too large: {field}")
        return value
    raise RunnerError("continuation_value_invalid", f"continuation field is not JSON compatible: {field}")


def _objects(value: Any, *, field: str) -> list[dict[str, Any]]:
    checked = _safe(value, field=field)
    if not isinstance(checked, list) or any(not isinstance(item, dict) for item in checked):
        raise RunnerError("continuation_shape_invalid", f"continuation {field} must be a list of objects")
    return checked


@dataclass(frozen=True)
class ContinuationBundle:
    run_id: str
    spec_key: str
    stage: str
    generation: int
    input_revision: str
    requirements: list[dict[str, Any]]
    confirmed_decisions: list[dict[str, Any]]
    tickets: list[dict[str, Any]]
    dependencies: list[dict[str, Any]]
    workspace: dict[str, Any]
    verified_items: list[dict[str, Any]]
    remaining_items: list[dict[str, Any]]
    tests: list[dict[str, Any]]
    review: list[dict[str, Any]]
    unconfirmed_operations: list[dict[str, Any]]
    authorization: dict[str, Any]
    last_verified_progress: str | None = None
    source_refs: list[dict[str, Any]] | None = None

    def public(self) -> dict[str, Any]:
        body = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id, "spec_key": self.spec_key, "stage": self.stage,
            "generation": self.generation, "input_revision": self.input_revision,
            "requirements": self.requirements, "confirmed_decisions": self.confirmed_decisions,
            "tickets": self.tickets, "dependencies": self.dependencies,
            "workspace": self.workspace, "verified_items": self.verified_items,
            "remaining_items": self.remaining_items, "tests": self.tests,
            "review": self.review, "unconfirmed_operations": self.unconfirmed_operations,
            "authorization": self.authorization, "last_verified_progress": self.last_verified_progress,
            "source_refs": self.source_refs or [],
        }
        body["bundle_digest"] = _digest(body)
        return body


def build_bundle(document: Mapping[str, Any]) -> ContinuationBundle:
    if document.get("schema_version") not in {None, SCHEMA_VERSION}:
        raise RunnerError("continuation_schema_invalid", "unexpected continuation bundle schema")
    required = ("run_id", "spec_key", "stage", "input_revision")
    if any(not isinstance(document.get(field), str) or not str(document[field]).strip() for field in required):
        raise RunnerError("continuation_identity_missing", "continuation bundle needs run, SPEC, stage, and input revision")
    generation = document.get("generation", 0)
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise RunnerError("continuation_generation_invalid", "continuation generation must be a non-negative integer")
    list_fields = ("requirements", "confirmed_decisions", "tickets", "dependencies", "verified_items", "remaining_items", "tests", "review", "unconfirmed_operations", "source_refs")
    values = {field: _objects(document.get(field, []), field=field) for field in list_fields}
    values.update({field: _safe(document.get(field, {}), field=field) for field in ("workspace", "authorization")})
    if not isinstance(values["workspace"], dict) or not isinstance(values["authorization"], dict):
        raise RunnerError("continuation_shape_invalid", "continuation workspace and authorization must be objects")
    bundle = ContinuationBundle(
        run_id=str(document["run_id"]), spec_key=str(document["spec_key"]), stage=str(document["stage"]),
        generation=generation, input_revision=str(document["input_revision"]),
        requirements=values["requirements"], confirmed_decisions=values["confirmed_decisions"],
        tickets=values["tickets"], dependencies=values["dependencies"], workspace=values["workspace"],
        verified_items=values["verified_items"], remaining_items=values["remaining_items"],
        tests=values["tests"], review=values["review"], unconfirmed_operations=values["unconfirmed_operations"],
        authorization=values["authorization"], last_verified_progress=_safe(document.get("last_verified_progress"), field="last_verified_progress"),
        source_refs=values["source_refs"],
    )
    supplied = document.get("bundle_digest")
    if supplied is not None and supplied != bundle.public()["bundle_digest"]:
        raise RunnerError("continuation_digest_mismatch", "continuation bundle digest does not match its contents")
    return bundle


def write_bundle_atomic(path: Path, bundle: ContinuationBundle) -> dict[str, Any]:
    document = bundle.public()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(path)
    return document


def read_bundle(path: Path) -> ContinuationBundle:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError("continuation_read_failed", "continuation bundle is not readable UTF-8 JSON") from exc
    if not isinstance(document, Mapping):
        raise RunnerError("continuation_schema_invalid", "continuation bundle root must be an object")
    return build_bundle(document)
