"""Evidence-first cleanup for run-owned helper threads."""
from __future__ import annotations

import json
from typing import Any, Mapping


class CleanupError(RuntimeError):
    pass


PROTECTED_KINDS = {"controller", "user", "主任务"}
NON_TERMINAL_OBSERVATIONS = {"working", "idle", "notLoaded", "created", "assigned", "route_verified"}


def classify_inventory(*, run_id: str, registry: list[Mapping[str, Any]], host_tasks: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Classify both orphan directions using only formal id + host id."""
    registered = {(row.get("formal_thread_id"), row.get("host_id")): row for row in registry
                  if row.get("formal_thread_id") and row.get("host_id")}
    discovered = {}
    for task in host_tasks:
        key = (task.get("formal_thread_id") or task.get("formalThreadId"), task.get("host_id") or task.get("hostId"))
        if all(key):
            discovered[key] = task
    orphan_registry = [row for key, row in registered.items() if key not in discovered]
    orphan_host = [task for key, task in discovered.items() if key not in registered]
    stale = []
    for key, row in registered.items():
        task = discovered.get(key)
        lifecycle = (task or {}).get("lifecycle") or (task or {}).get("status")
        if lifecycle in NON_TERMINAL_OBSERVATIONS and row.get("kind") not in PROTECTED_KINDS:
            stale.append({"thread_id": row.get("thread_id"), "formal_thread_id": key[0], "host_id": key[1], "lifecycle": lifecycle})
    return {"decision": "allow" if not orphan_registry and not orphan_host and not stale else "repair",
            "registry_absent_from_host": orphan_registry, "host_unregistered": orphan_host,
            "stale": stale, "run_id": run_id,
            "evidence": ["formal_thread_id+host_id:inventory"]}


def eligible_for_cleanup(entry: Mapping[str, Any], *, run_id: str, current_controller: Mapping[str, Any] | None = None) -> bool:
    if entry.get("run_id") != run_id or entry.get("kind") in PROTECTED_KINDS:
        return False
    if current_controller and entry.get("formal_thread_id") == current_controller.get("formal_thread_id"):
        return False
    return all(entry.get(field) for field in ("formal_thread_id", "host_id", "owner_id"))


def archive_with_readback(*, archive, readback, entry: Mapping[str, Any]) -> dict[str, Any]:
    if not entry.get("formal_thread_id") or not entry.get("host_id"):
        raise CleanupError("formal identity is required for cleanup")
    operation = archive(formal_thread_id=entry["formal_thread_id"], host_id=entry["host_id"])
    state = readback(formal_thread_id=entry["formal_thread_id"], host_id=entry["host_id"])
    if not isinstance(state, Mapping) or state.get("archived") is not True:
        raise CleanupError("archive readback is incomplete")
    return {"thread_id": entry.get("thread_id"), "formal_thread_id": entry["formal_thread_id"],
            "host_id": entry["host_id"], "operation": dict(operation or {}),
            "archive_readback": dict(state), "status": "archived",
            "evidence": ["archive_operation", "archive_readback"]}


def cleanup_threads(*, entries: list[Mapping[str, Any]], run_id: str, archive, readback,
                    current_controller: Mapping[str, Any] | None = None) -> dict[str, Any]:
    receipts, errors = [], []
    for entry in entries:
        if not eligible_for_cleanup(entry, run_id=run_id, current_controller=current_controller):
            continue
        try:
            receipts.append(archive_with_readback(archive=archive, readback=readback, entry=entry))
        except Exception as exc:  # preserve a retryable cleanup action
            errors.append({"thread_id": entry.get("thread_id"), "error": str(exc), "retryable": True})
    return {"run_id": run_id, "complete": not errors,
            "receipts": receipts, "errors": errors,
            "next_action": None if not errors else {"kind": "cleanup_threads", "target": run_id}}


def record_cleanup_receipt(db, run_id: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Persist cleanup evidence only after the caller has read archived:true."""
    if receipt.get("status") != "archived" or receipt.get("archive_readback", {}).get("archived") is not True:
        raise CleanupError("cleanup receipt requires archived:true readback")
    thread_id = receipt.get("thread_id")
    if not thread_id:
        raise CleanupError("cleanup receipt requires registry thread id")
    row = db.conn.execute("SELECT run_id FROM threads WHERE thread_id=?", (thread_id,)).fetchone()
    if row is None or row[0] != run_id:
        raise CleanupError("cleanup thread is not run-owned")
    existing = db.conn.execute(
        "SELECT lifecycle,archive_readback_evidence FROM threads WHERE thread_id=? AND run_id=?",
        (thread_id, run_id),
    ).fetchone()
    if existing and existing[0] == "archived":
        try:
            readbacks = json.loads(existing[1] or "[]")
        except (TypeError, ValueError):
            readbacks = []
        if any(isinstance(item, Mapping) and item.get("archived") is True for item in readbacks):
            return {"thread_id": thread_id, "status": "archived", "created": False,
                    "registry_transition": "already_archived", "evidence": receipt["evidence"]}
    version = db.business_version(run_id)
    gate = {"schema_version": 1,
            "expected": {"run_id": run_id, "target_id": thread_id, "candidate_sha": "cleanup",
                         "environment": "controller", "business_version": version},
            "actor": {"id": "cleanup-controller", "authorized": True},
            "source": {"kind": "archive-readback", "trust": "verified"},
            "readback": {"status": "verified", "run_id": run_id, "target_id": thread_id,
                         "candidate_sha": "cleanup", "environment": "controller", "archived": True},
            "archive_operation": True, "archive_readback": True}
    db.update_thread(run_id, thread_id, "archived", outcome="cleanup_verified",
                     operation=receipt["operation"], readback=receipt["archive_readback"], gate=gate)
    return {"thread_id": thread_id, "status": "archived", "created": True,
            "registry_transition": "archived", "evidence": receipt["evidence"]}
