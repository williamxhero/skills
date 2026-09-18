"""Executable startup contract validation and canonical dependency resolution."""
from __future__ import annotations

from typing import Any


STARTUP_SCHEMA_VERSION = 1
REQUIRED_SECTIONS = frozenset({"run_id", "runtime", "skill_root", "target_repository", "tracker", "host", "permissions", "dependencies"})


class StartupContractError(ValueError):
    def __init__(self, code: str, details: dict[str, Any] | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(f"{code}: {self.details}")


def _reject(code: str, **details: Any) -> None:
    raise StartupContractError(code, details)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _reject("startup_field_missing", field=field)
    return value


def resolve_dependencies(dependencies: Any, available: Any) -> list[dict[str, Any]]:
    if not isinstance(dependencies, list):
        _reject("startup_dependencies_invalid")
    if not isinstance(available, list):
        _reject("blocked_missing_dependency", reason="dependency_catalog_missing")
    catalog = {}
    for item in available:
        if not isinstance(item, dict):
            continue
        canonical = item.get("canonical_name")
        if not isinstance(canonical, str) or not canonical.strip():
            continue
        keys = [canonical, *item.get("aliases", [])] if isinstance(item.get("aliases", []), list) else [canonical]
        for key in keys:
            if isinstance(key, str) and key.strip():
                catalog.setdefault(key, []).append(item)
    resolved = []
    for requested in dependencies:
        if not isinstance(requested, dict):
            _reject("startup_dependency_invalid")
        canonical = _text(requested.get("canonical_name"), "dependencies.canonical_name")
        aliases = requested.get("aliases", [])
        if not isinstance(aliases, list) or any(not isinstance(alias, str) or not alias.strip() for alias in aliases):
            _reject("startup_dependency_aliases_invalid", canonical_name=canonical)
        candidates = catalog.get(canonical, [])
        if not candidates:
            for alias in aliases:
                candidates.extend(catalog.get(alias, []))
        unique = {id(item): item for item in candidates}
        if not unique:
            _reject("blocked_missing_dependency", canonical_name=canonical)
        if len(unique) != 1:
            _reject("blocked_ambiguous_dependency", canonical_name=canonical)
        item = next(iter(unique.values()))
        for field in ("path", "digest", "adapter"):
            _text(item.get(field), f"dependency.{canonical}.{field}")
        if item.get("canonical_name") != canonical:
            _reject("dependency_canonical_mismatch", expected=canonical, actual=item.get("canonical_name"))
        for field in ("path", "digest", "version", "adapter"):
            if field in requested and requested[field] != item.get(field):
                _reject(
                    "dependency_mismatch",
                    canonical_name=canonical,
                    field=field,
                    expected=requested[field],
                    actual=item.get(field),
                )
        resolved.append({
            "canonical_name": canonical,
            "aliases": list(aliases),
            "path": item["path"],
            "digest": item["digest"],
            **({"version": item["version"]} if "version" in item else {}),
            "adapter": item["adapter"],
        })
    return resolved


def validate_startup_contract(contract: Any, *, run_id: str, available_dependencies: Any = None) -> dict[str, Any]:
    if not isinstance(contract, dict):
        _reject("startup_contract_missing")
    if contract.get("schema_version") != STARTUP_SCHEMA_VERSION:
        _reject("startup_schema_version", expected=STARTUP_SCHEMA_VERSION)
    missing = sorted(REQUIRED_SECTIONS - set(contract))
    if missing:
        _reject("startup_contract_incomplete", missing=missing)
    if contract.get("run_id") != run_id:
        _reject("startup_run_mismatch", expected=run_id, actual=contract.get("run_id"))
    for field in ("runtime", "target_repository", "tracker", "host", "permissions"):
        if not isinstance(contract[field], dict):
            _reject("startup_section_invalid", field=field)
    _text(contract["runtime"].get("python"), "runtime.python")
    _text(contract["runtime"].get("platform"), "runtime.platform")
    _text(contract["skill_root"], "skill_root")
    for field in ("id", "path", "branch"):
        _text(contract["target_repository"].get(field), f"target_repository.{field}")
    _text(contract["tracker"].get("mode"), "tracker.mode")
    _text(contract["host"].get("id"), "host.id")
    if not isinstance(contract["host"].get("capabilities"), list):
        _reject("startup_host_capabilities_invalid")
    if not isinstance(contract["permissions"].get("allowed_actions"), list):
        _reject("startup_permissions_invalid")
    resolved = resolve_dependencies(contract["dependencies"], available_dependencies if available_dependencies is not None else contract.get("dependency_catalog", []))
    result = dict(contract)
    result["dependencies"] = resolved
    result["status"] = "verified"
    return result
