"""Versioned planning artifacts and existing-plan intake.

The module deliberately keeps plans as data.  Publishing, model invocation and
Git side effects live in their own adapters, so a plan cannot smuggle an action
through a title or Markdown body.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import RunnerError
from .tracker import PlanSnapshot

SPEC_PLAN_SCHEMA = "spec-runner-spec-plan/v1"
TICKET_PLAN_SCHEMA = "spec-runner-ticket-plan/v1"


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError("invalid_plan", "plan must be a UTF-8 JSON object") from exc
    if not isinstance(parsed, dict):
        raise RunnerError("invalid_plan", "plan root must be an object")
    return parsed


def _list(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise RunnerError("invalid_plan", f"{field} must be a list of objects")
    return value


def _keys(items: list[dict[str, Any]], field: str) -> set[str]:
    keys = [item.get("key") for item in items]
    if any(not isinstance(key, str) or not key.strip() for key in keys) or len(set(keys)) != len(keys):
        raise RunnerError("invalid_plan", f"{field} keys must be non-empty and unique")
    return set(keys)


def _acyclic(items: list[dict[str, Any]], keys: set[str], *, relation: str = "blocked_by") -> None:
    edges: dict[str, set[str]] = {}
    for item in items:
        value = item.get(relation, [])
        if value is None:
            value = []
        if not isinstance(value, list) or any(not isinstance(item_key, str) for item_key in value):
            raise RunnerError("invalid_plan", f"{relation} must be a list of keys")
        unknown = set(value) - keys
        if unknown:
            raise RunnerError("unknown_plan_dependency", f"unknown {relation}: {sorted(unknown)}")
        edges[item["key"]] = set(value)
    visited: set[str] = set()
    active: set[str] = set()
    def visit(key: str) -> None:
        if key in active:
            raise RunnerError("plan_cycle", f"dependency cycle contains {key}")
        if key not in visited:
            active.add(key)
            for parent in edges[key]:
                visit(parent)
            active.remove(key)
            visited.add(key)
    for key in keys:
        visit(key)


def validate_spec_plan(document: dict[str, Any]) -> dict[str, Any]:
    if document.get("schema_version") != SPEC_PLAN_SCHEMA:
        raise RunnerError("invalid_spec_plan", f"schema_version must be {SPEC_PLAN_SCHEMA}")
    requirements = document.get("requirements")
    if not isinstance(requirements, list) or any(not isinstance(value, str) or not value for value in requirements):
        raise RunnerError("invalid_spec_plan", "requirements must be non-empty requirement IDs")
    specs = _list(document.get("specs"), "specs")
    keys = _keys(specs, "specs")
    _acyclic(specs, keys)
    covered: set[str] = set()
    for spec in specs:
        if not isinstance(spec.get("title"), str) or not isinstance(spec.get("body"), str):
            raise RunnerError("invalid_spec_plan", "each SPEC needs title and body")
        route = spec.get("route")
        if not isinstance(route, dict) or not all(isinstance(route.get(x), str) and route[x] for x in ("model", "effort", "reason")):
            raise RunnerError("invalid_spec_plan", "each SPEC needs a model, effort, and reason")
        mapping = spec.get("covers", [])
        if not isinstance(mapping, list) or any(item not in requirements for item in mapping):
            raise RunnerError("invalid_spec_plan", "SPEC covers must reference requirements")
        covered.update(mapping)
    missing = set(requirements) - covered
    if missing:
        raise RunnerError("plan_coverage_missing", "requirements have no owning SPEC", details={"missing": sorted(missing)})
    result = dict(document)
    result["digest"] = digest({key: value for key, value in document.items() if key != "digest"})
    return result


def validate_ticket_plan(document: dict[str, Any], *, expected_spec_key: str | None = None, expected_base_sha: str | None = None) -> dict[str, Any]:
    if document.get("schema_version") != TICKET_PLAN_SCHEMA:
        raise RunnerError("invalid_ticket_plan", f"schema_version must be {TICKET_PLAN_SCHEMA}")
    spec_key = document.get("spec_key")
    if not isinstance(spec_key, str) or not spec_key:
        raise RunnerError("invalid_ticket_plan", "spec_key is required")
    if expected_spec_key and spec_key != expected_spec_key:
        raise RunnerError("stale_ticket_plan", "ticket plan belongs to another SPEC")
    base_sha = document.get("base_sha")
    if not isinstance(base_sha, str) or len(base_sha) < 7:
        raise RunnerError("invalid_ticket_plan", "base_sha is required")
    if expected_base_sha and base_sha != expected_base_sha:
        raise RunnerError("stale_ticket_plan", "ticket plan base SHA is no longer current")
    tickets = _list(document.get("tickets"), "tickets")
    keys = _keys(tickets, "tickets")
    _acyclic(tickets, keys)
    for ticket in tickets:
        if not isinstance(ticket.get("body"), str) or not ticket["body"].strip():
            raise RunnerError("invalid_ticket_plan", "each ticket requires a body")
        acceptance = ticket.get("acceptance")
        if not isinstance(acceptance, list) or not acceptance or any(not isinstance(value, str) or not value for value in acceptance):
            raise RunnerError("invalid_ticket_plan", "each ticket needs acceptance IDs")
    result = dict(document)
    result["digest"] = digest({key: value for key, value in document.items() if key != "digest"})
    return result


def intake_snapshot(snapshot: PlanSnapshot, *, entry_key: str) -> dict[str, Any]:
    """Adopt an explicit existing plan without inferring work from issue state."""
    records = {record.key: record for record in snapshot.records}
    if entry_key not in records:
        raise RunnerError("unknown_plan_entry", f"entry key does not exist: {entry_key}")
    selected = records[entry_key]
    descendants = [record for record in snapshot.records if record.parent == entry_key]
    specs = [record for record in descendants if record.kind.lower() in {"spec", "issue"}]
    if not specs:
        # A direct SPEC entry is valid even when it has no tickets yet.
        specs = [selected]
    ticket_map: dict[str, list[str]] = {spec.key: [] for spec in specs}
    for record in snapshot.records:
        if record.parent in ticket_map:
            ticket_map[record.parent].append(record.key)
    ordered = sorted(specs, key=lambda record: record.key)
    return {
        "schema_version": "spec-runner-plan-intake/v1",
        "source_snapshot_digest": snapshot.digest,
        "entry_key": entry_key,
        "relation_mode": snapshot.relation_mode,
        "specs": [
            {"key": spec.key, "revision": spec.revision, "digest": spec.digest, "tickets": sorted(ticket_map[spec.key]), "state": "tickets_ready" if ticket_map[spec.key] else "planned"}
            for spec in ordered
        ],
        "digest": digest({"snapshot": snapshot.digest, "entry": entry_key}),
    }


def detect_source_change(previous_digest: str, current: PlanSnapshot) -> dict[str, object]:
    return {"changed": previous_digest != current.digest, "previous_digest": previous_digest, "current_digest": current.digest, "action": "change_request" if previous_digest != current.digest else "none"}
