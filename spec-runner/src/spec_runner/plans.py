"""Versioned planning artifacts and existing-plan intake.

The module deliberately keeps plans as data.  Publishing, model invocation and
Git side effects live in their own adapters, so a plan cannot smuggle an action
through a title or Markdown body.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import RunnerError
from .tracker import PlanSnapshot

SPEC_PLAN_SCHEMA = "spec-runner-spec-plan/v1"
TICKET_PLAN_SCHEMA = "spec-runner-ticket-plan/v1"
DELIVERY_PLAN_SCHEMA = "spec-runner-delivery-plan/v1"


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
    if not keys or any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", key) for key in keys) or len({key.casefold() for key in keys}) != len(keys):
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


def _ordered(items: list[dict[str, Any]]) -> None:
    """Respect the supplied execution queue; never silently sort it."""
    seen: set[str] = set()
    for item in items:
        if set(item.get("blocked_by") or []) - seen:
            raise RunnerError("plan_order_conflict", "a prerequisite appears after its dependent in the explicit queue")
        seen.add(item["key"])


def validate_spec_plan(document: dict[str, Any]) -> dict[str, Any]:
    if document.get("schema_version") != SPEC_PLAN_SCHEMA:
        raise RunnerError("invalid_spec_plan", f"schema_version must be {SPEC_PLAN_SCHEMA}")
    requirements = document.get("requirements")
    if not isinstance(requirements, list) or not requirements or any(not isinstance(value, str) or not value.strip() for value in requirements) or len(set(requirements)) != len(requirements):
        raise RunnerError("invalid_spec_plan", "requirements must be non-empty requirement IDs")
    specs = _list(document.get("specs"), "specs")
    keys = _keys(specs, "specs")
    _acyclic(specs, keys)
    _ordered(specs)
    covered: set[str] = set()
    for spec in specs:
        if any(not isinstance(spec.get(field), str) or not spec[field].strip() for field in ("title", "body")):
            raise RunnerError("invalid_spec_plan", "each SPEC needs title and body")
        route = spec.get("route")
        if not isinstance(route, dict) or not all(isinstance(route.get(x), str) and route[x] for x in ("model", "effort", "reason")):
            raise RunnerError("invalid_spec_plan", "each SPEC needs a model, effort, and reason")
        mapping = spec.get("covers", [])
        if not isinstance(mapping, list) or not mapping or any(item not in requirements for item in mapping):
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
    _ordered(tickets)
    if spec_key.casefold() in {key.casefold() for key in keys}:
        raise RunnerError("invalid_ticket_plan", "a ticket key cannot overwrite its parent SPEC")
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


def validate_delivery_plan(document: dict[str, Any]) -> dict[str, Any]:
    """Validate the trusted, mechanical whole-SPEC delivery contract."""
    if document.get("schema_version") != DELIVERY_PLAN_SCHEMA:
        raise RunnerError("invalid_delivery_plan", f"schema_version must be {DELIVERY_PLAN_SCHEMA}")
    specs = _list(document.get("specs"), "specs")
    keys = _keys(specs, "specs")
    _acyclic(specs, keys)
    for spec in specs:
        if not isinstance(spec.get("acceptance_version"), str) or not spec["acceptance_version"]:
            raise RunnerError("invalid_delivery_plan", "each SPEC needs acceptance_version")
        acceptance = spec.get("acceptance")
        if not isinstance(acceptance, list) or not acceptance or any(not isinstance(value, str) or not value for value in acceptance):
            raise RunnerError("invalid_delivery_plan", "each SPEC needs acceptance IDs")
        implementation = spec.get("implementation", [])
        if not isinstance(implementation, list) or any(not isinstance(command, list) or not command or any(not isinstance(part, str) or not part for part in command) for command in implementation):
            raise RunnerError("invalid_delivery_plan", "implementation must contain argument arrays")
        checks = spec.get("checks")
        if not isinstance(checks, list) or not checks:
            raise RunnerError("invalid_delivery_plan", "each SPEC needs trusted checks")
        for check in checks:
            if not isinstance(check, dict) or not isinstance(check.get("command"), list) or not check["command"]:
                raise RunnerError("invalid_delivery_plan", "checks need command arrays")
            if not isinstance(check.get("acceptance"), list) or not check["acceptance"]:
                raise RunnerError("invalid_delivery_plan", "checks need acceptance mappings")
        review_file = spec.get("review_file")
        if not isinstance(review_file, str) or not review_file or Path(review_file).is_absolute() or ".." in Path(review_file).parts:
            raise RunnerError("invalid_delivery_plan", "review_file must be a safe relative path")
    result = dict(document)
    result["digest"] = digest({key: value for key, value in document.items() if key != "digest"})
    return result
