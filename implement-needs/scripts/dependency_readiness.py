"""Separate dependency-graph validity from current delivery readiness."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
from typing import Any


TERMINAL_SUCCESS = frozenset({"closed"})
TERMINAL_FAILURE = frozenset({"cancelled", "failed", "blocked"})


def _error(code: str, *, spec_id: str | None = None, blocker: str | None = None, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code}
    if spec_id is not None:
        result["spec_id"] = spec_id
    if blocker is not None:
        result["blocker"] = blocker
    result.update(details)
    return result


def _as_specs(specs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in specs:
        if not isinstance(item, Mapping):
            result.append({"spec_id": None, "blocked_by": [], "_invalid": True})
            continue
        blockers = item.get("blocked_by", [])
        if isinstance(blockers, str):
            blockers = [blockers]
        result.append({**dict(item), "blocked_by": list(blockers) if isinstance(blockers, list) else blockers})
    return result


def validate_spec_graph(specs: Iterable[Mapping[str, Any]], *, run_id: str | None = None) -> dict[str, Any]:
    """Validate references, ownership, cycles, and fixed ordering only.

    Delivery status is deliberately ignored here. A known predecessor that is
    still open is a valid graph edge, not a structural error.
    """
    rows = _as_specs(specs)
    errors: list[dict[str, Any]] = []
    nodes: dict[str, dict[str, Any]] = {}
    for row in rows:
        spec_id = row.get("spec_id")
        if not isinstance(spec_id, str) or not spec_id.strip():
            errors.append(_error("spec_id_invalid"))
            continue
        if spec_id in nodes:
            errors.append(_error("duplicate_spec_id", spec_id=spec_id))
        nodes[spec_id] = row
        if run_id is not None and row.get("run_id") not in (None, run_id):
            errors.append(_error("cross_run_dependency", spec_id=spec_id, actual_run_id=row.get("run_id"), expected_run_id=run_id))
        if not isinstance(row.get("blocked_by"), list) or any(not isinstance(item, str) or not item.strip() for item in row.get("blocked_by", [])):
            errors.append(_error("dependency_list_invalid", spec_id=spec_id))

    for spec_id, row in nodes.items():
        blockers = row.get("blocked_by", []) if isinstance(row.get("blocked_by"), list) else []
        for blocker in blockers:
            target = nodes.get(blocker)
            if target is None:
                errors.append(_error("unknown_dependency", spec_id=spec_id, blocker=blocker))
                continue
            if run_id is not None and target.get("run_id") not in (None, run_id):
                errors.append(_error("cross_run_dependency", spec_id=spec_id, blocker=blocker, actual_run_id=target.get("run_id"), expected_run_id=run_id))
            position = row.get("position")
            blocker_position = target.get("position")
            if isinstance(position, int) and isinstance(blocker_position, int) and blocker_position >= position:
                errors.append(_error("fixed_order_violation", spec_id=spec_id, blocker=blocker, spec_position=position, blocker_position=blocker_position))

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(spec_id: str, path: list[str]) -> None:
        if spec_id in visiting:
            cycle_start = path.index(spec_id) if spec_id in path else 0
            errors.append(_error("dependency_cycle", spec_id=spec_id, cycle=path[cycle_start:] + [spec_id]))
            return
        if spec_id in visited or spec_id not in nodes:
            return
        visiting.add(spec_id)
        blockers = nodes[spec_id].get("blocked_by", [])
        if isinstance(blockers, list):
            for blocker in blockers:
                visit(blocker, path + [spec_id])
        visiting.remove(spec_id)
        visited.add(spec_id)

    for spec_id in nodes:
        visit(spec_id, [])
    unique = []
    seen = set()
    for item in errors:
        key = repr(sorted(item.items()))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return {"status": "valid" if not unique else "invalid", "errors": unique, "spec_ids": list(nodes)}


def assess_spec_readiness(
    specs: Iterable[Mapping[str, Any]],
    spec_id: str,
    *,
    run_id: str | None = None,
    waived_dependencies: Iterable[str] = (),
) -> dict[str, Any]:
    """Return ready, waiting, or blocked for one candidate SPEC."""
    rows = _as_specs(specs)
    structure = validate_spec_graph(rows, run_id=run_id)
    if structure["status"] != "valid":
        return {"status": "blocked", "reason": "structural_error", "target": spec_id, "errors": structure["errors"], "blockers": []}
    by_id = {row.get("spec_id"): row for row in rows}
    target = by_id.get(spec_id)
    if target is None:
        return {"status": "blocked", "reason": "unknown_spec", "target": spec_id, "errors": [_error("unknown_spec", spec_id=spec_id)], "blockers": []}
    waived = set(waived_dependencies)
    waiting = []
    blocked = []
    for blocker_id in target.get("blocked_by", []):
        blocker = by_id[blocker_id]
        blocker_status = blocker.get("status")
        if blocker_status in TERMINAL_SUCCESS or blocker_id in waived:
            continue
        edge = {"spec_id": spec_id, "blocker": blocker_id, "blocker_status": blocker_status}
        if blocker_status in TERMINAL_FAILURE:
            edge["reason"] = "failed_predecessor" if blocker_status == "failed" else "cancelled_predecessor"
            blocked.append(edge)
        else:
            edge["reason"] = "predecessor_not_delivered"
            waiting.append(edge)
    if blocked:
        return {"status": "blocked", "reason": "unsatisfied_predecessor", "target": spec_id, "errors": [], "blockers": blocked}
    if waiting:
        return {"status": "waiting", "reason": "predecessor_not_delivered", "target": spec_id, "errors": [], "blockers": waiting, "next_check": waiting[0]["blocker"]}
    return {"status": "ready", "reason": "all_predecessors_satisfied", "target": spec_id, "errors": [], "blockers": []}


def readiness_from_db(db, run_id: str, spec_id: str) -> dict[str, Any]:
    rows = [dict(row) for row in db.conn.execute("SELECT * FROM specs WHERE run_id=? ORDER BY position", (run_id,))]
    for row in rows:
        try:
            row["blocked_by"] = json.loads(row.get("blocked_by") or "[]")
        except (TypeError, json.JSONDecodeError):
            row["blocked_by"] = row.get("blocked_by")
    waived = [row[0] for row in db.conn.execute(
        "SELECT entity_id FROM evidence_refs WHERE run_id=? AND entity_type='spec' AND evidence_kind='dependency_waiver'",
        (run_id,),
    )]
    return assess_spec_readiness(rows, spec_id, run_id=run_id, waived_dependencies=waived)
