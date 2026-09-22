"""Audit SQLite control state before another action."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from control_db import ControlDB
from task_backend import (
    BackendError,
    list_tasks,
    probe_connector,
    read_applied_route,
    read_task,
)
from task_binding import AMBIGUOUS, BOUND, Candidate, bind_candidate
from dependency_readiness import readiness_from_db, structure_from_db


def probe_and_record(db, run_id, transport):
    """Probe the live backend and persist the complete capability receipt."""
    capability = probe_connector(transport)
    db.add_observation(
        run_id,
        "backend",
        "task-backend",
        {"operation": "capability_probe", "status": "succeeded", "evidence": capability},
    )
    return capability


def _verified_archived_audit_row(row):
    """Return true only for a terminal row with persisted archive readback.

    Native app-server inventories may age out archived thread ids. Such a row is
    already terminal audit evidence, not a live recovery target. Malformed or
    incomplete receipts deliberately remain live reconciliation blockers.
    """
    if row.get("lifecycle") not in {"archived", "completed"}:
        return False
    try:
        operations = json.loads(row.get("archive_operation_evidence") or "[]")
        readbacks = json.loads(row.get("archive_readback_evidence") or "[]")
    except (TypeError, json.JSONDecodeError):
        return False
    # Historical controller ledgers predate structured archive payloads. A
    # completed lifecycle is accepted only with both archive receipt lists;
    # this also contains recovery from older reconciliation runs that replaced
    # the local archived lifecycle with the backend's coarse completed status.
    return isinstance(operations, list) and bool(operations) and isinstance(readbacks, list) and bool(readbacks)


def _missing_registry_recovery(db, run_id, missing):
    """Classify a missing registry row without authorizing an external replay.

    An assigned bootstrap has already crossed the route and assignment
    barriers.  If a reconnect cannot find its formal backend record, the
    controller must preserve that boundary and ask for assignment
    reconciliation; treating the row as an unstarted create would permit a
    duplicate assignment.  Other missing rows retain the historical generic
    mismatch classification.
    """
    assigned = []
    for thread_id in missing:
        row = db.conn.execute(
            "SELECT state,assignment_receipt FROM thread_bootstraps "
            "WHERE run_id=? AND thread_id=?",
            (run_id, thread_id),
        ).fetchone()
        if row is None:
            continue
        if row[0] == "assigned" or row[1] is not None:
            assigned.append(thread_id)
    if not assigned:
        return {
            "errors": ["registry_threads_absent_from_backend"],
            "recovery": None,
        }
    recovery = {
        "classification": "assigned_backend_absent_after_reconnect",
        "missing_thread_ids": assigned,
        "local_action": "assignment_reconciliation_required",
        "assignment_replay_allowed": False,
        "evidence": ["controller:bootstrap:assigned", "backend:formal_identity_absent"],
    }
    errors = ["registry_assigned_thread_absent_from_backend"]
    if len(assigned) != len(missing):
        errors.append("registry_threads_absent_from_backend")
    return {"errors": errors, "recovery": recovery}


def reconcile_inventory(db, run_id, inventory):
    """Reconcile a backend inventory supplied by the selected task adapter.

    Inventory is deliberately an input boundary: the desktop/MCP/app-server
    adapter performs discovery, while this function owns identity gating and
    SQLite persistence. A partial inventory is inconclusive and never permits
    replacement creation.
    """
    if not isinstance(inventory, dict):
        return {"decision":"repair","status":"inconclusive","errors":["inventory_not_object"],"matches":[]}
    tasks=inventory.get("tasks")
    if not isinstance(tasks,list):
        return {"decision":"repair","status":"inconclusive","errors":["inventory_tasks_not_array"],"matches":[]}
    errors=[]; matches=[]
    if inventory.get("reconciliation_status") != "complete":
        errors.append("backend_inventory_inconclusive")
    all_registered=[dict(row) for row in db.conn.execute("SELECT * FROM threads WHERE run_id=? AND task_id IS NOT NULL",(run_id,))]
    # Terminal rows with both archive receipts remain durable audit evidence.
    # Do not rebind or observe them when an app-server happens to retain a
    # coarse historical record: that record cannot strengthen recovery identity
    # and must not overwrite the controller's archived lifecycle.
    archived_audit = [row for row in all_registered if _verified_archived_audit_row(row)]
    registered = [row for row in all_registered if row not in archived_audit]
    for task in tasks:
        if not isinstance(task,dict):
            errors.append("invalid_backend_task"); continue
        formal=task.get("formal_thread_id") or task.get("formalThreadId") or task.get("thread_id") or task.get("threadId")
        host=task.get("host_id") or task.get("hostId")
        client=task.get("client_thread_id") or task.get("clientThreadId")
        title=task.get("title")
        if formal and host and any(
            row.get("formal_thread_id") == formal and row.get("host_id") == host
            for row in archived_audit
        ):
            continue
        candidates=[]
        if formal and host:
            candidates=[row for row in registered if row.get("formal_thread_id")==formal and row.get("host_id")==host]
        if not candidates and client:
            candidates=[row for row in registered if row.get("client_thread_id")==client]
        if not candidates and title:
            candidates=[row for row in registered if row.get("title_token") and title.startswith(row["title_token"])]
        if len(candidates)>1:
            errors.append("identity_ambiguous"); matches.append({"task":formal or client,"status":AMBIGUOUS}); continue
        if not candidates:
            matches.append({"task":formal or client,"status":"backend_untracked"}); continue
        readback=task.get("readback") or task
        candidate=Candidate(formal_thread_id=readback.get("formal_thread_id") or readback.get("formalThreadId"),host_id=readback.get("host_id") or readback.get("hostId"),title=title,task_id=readback.get("task_id"),run_id=readback.get("run_id"),attempt_id=readback.get("attempt_id"),owner_id=readback.get("owner_id") or readback.get("owner"),cwd=readback.get("cwd"),project_id=readback.get("project_id"),lifecycle=readback.get("lifecycle"),readback_evidence=tuple(readback.get("readback_evidence") or readback.get("evidence") or ()))
        decision=bind_candidate(candidates[0],candidate)
        matches.append({"task":formal or client,"thread_id":candidates[0]["thread_id"],"status":decision.status,"reason":decision.reason})
        if decision.status==BOUND:
            db.bind_identity(run_id, candidates[0]["thread_id"], candidate.formal_thread_id, candidate.host_id, json.dumps(readback,ensure_ascii=False,sort_keys=True))
            db.observe_thread(run_id,candidates[0]["thread_id"],lifecycle=readback.get("lifecycle"),outcome=readback.get("outcome"),next_action=readback.get("next_action"),readback=json.dumps(readback,ensure_ascii=False,sort_keys=True))
        else:
            errors.append(decision.status)
    missing=[row["thread_id"] for row in registered if not any(item.get("thread_id")==row["thread_id"] for item in matches)]
    recovery = None
    if missing:
        classified = _missing_registry_recovery(db, run_id, missing)
        errors.extend(classified["errors"])
        recovery = classified["recovery"]
    result = {"decision":"allow" if not errors else "repair",
              "status":"complete" if not errors else ("inconclusive" if "backend_inventory_inconclusive" in errors else "mismatch"),
              "errors":sorted(set(errors)),"matches":matches}
    if recovery is not None:
        result["recovery"] = recovery
    return result


def reconcile_backend(db, run_id, backend, *, page_limit=100):
    """Discover the saved run from the live backend before judging recovery.

    Recovery is keyed in descending authority order: formal ID plus host, client
    handle, then the canonical title token. The backend performs the queries; the
    registry still performs every identity comparison and retains the readback.
    """
    run = db.conn.execute("SELECT execution_mode,controller_task_id FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if run is None:
        return {"decision": "repair", "status": "mismatch", "errors": ["run_not_found"], "matches": []}
    if run[0] == "single-ticket-line":
        if not run[1]:
            return {"decision": "repair", "status": "mismatch", "errors": ["controller_task_id_missing"], "matches": []}
        ledger = db.ticket_ledger_status(run_id)
        if not ledger["allow"]:
            return {"decision": "repair", "status": "inconclusive",
                    "errors": ledger["errors"], "matches": [],
                    "ticket_count": ledger.get("ticket_count", 0)}
        registered = [dict(row) for row in db.conn.execute(
            "SELECT * FROM threads WHERE run_id=? AND task_id IS NOT NULL "
            "AND (formal_thread_id=? OR thread_id=? OR client_thread_id=?)",
            (run_id, run[1], run[1], run[1]),
        )]
        if not registered:
            return {"decision": "repair", "status": "mismatch", "errors": ["controller_registry_identity_missing"], "matches": []}
        if len(registered) > 1:
            return {"decision": "repair", "status": "mismatch", "errors": ["controller_registry_identity_ambiguous"], "matches": []}
        try:
            inventory = list_tasks(backend, formal_thread_id=run[1], page_limit=page_limit)
        except (BackendError, OSError, ValueError) as exc:
            return {"decision": "repair", "status": "inconclusive", "errors": ["controller_query_failed:" + str(exc)], "matches": []}
        tasks = [task for task in inventory.get("tasks", []) if isinstance(task, dict)]
        matches = []
        errors = []
        for task in tasks:
            formal = task.get("formal_thread_id") or task.get("formalThreadId") or task.get("thread_id") or task.get("threadId")
            host = task.get("host_id") or task.get("hostId")
            if formal != run[1]:
                continue
            if not formal or not host:
                matches.append({"task": formal, "host_id": host, "status": "identity_unresolved",
                                "readback": False, "formal_identity": False})
                errors.append("controller_formal_identity_incomplete")
                continue
            try:
                # Inventory metadata is only an index. Always refresh the formal
                # record before comparing identity or persisting recovery.
                task["readback"] = read_task(backend, formal, host)
            except BackendError as exc:
                errors.append("controller_formal_readback_failed:" + str(exc))
                matches.append({"task": formal, "host_id": host, "status": "identity_unresolved",
                                "readback": False, "formal_identity": True})
                continue
            readback = task.get("readback")
            if not isinstance(readback, dict):
                matches.append({"task": formal, "host_id": host, "status": "identity_unresolved",
                                "readback": False, "formal_identity": bool(formal and host)})
                continue
            candidate = Candidate(
                formal_thread_id=readback.get("formal_thread_id") or readback.get("formalThreadId"),
                host_id=readback.get("host_id") or readback.get("hostId"),
                title=task.get("title"),
                task_id=readback.get("task_id"),
                run_id=readback.get("run_id"),
                attempt_id=readback.get("attempt_id"),
                owner_id=readback.get("owner_id") or readback.get("owner"),
                cwd=readback.get("cwd"),
                project_id=readback.get("project_id"),
                lifecycle=readback.get("lifecycle"),
                readback_evidence=tuple(readback.get("readback_evidence") or readback.get("evidence") or ()),
            )
            decision = bind_candidate(registered[0], candidate)
            matches.append({"task": formal, "host_id": host, "status": decision.status,
                            "reason": decision.reason, "readback": True,
                            "formal_identity": candidate.identified})
            if decision.status != BOUND:
                errors.append("controller_" + decision.status)
                continue
            try:
                route = read_applied_route(backend, candidate.formal_thread_id, candidate.host_id)
            except BackendError as exc:
                errors.append("controller_applied_route_failed:" + str(exc))
                continue
            # Identity, route, and the first queue action are one recovery
            # barrier. SQLite commits them together after all three gates pass.
            from next_action import next_action
            action = next_action(db, run_id)
            if action.get("kind") in {"repair_queue", "final_release"}:
                errors.append("controller_next_action_not_executable")
                continue
            db.persist_controller_recovery(
                run_id, registered[0]["thread_id"], readback, route, action,
            )
            matches[-1]["route_readback"] = route
            matches[-1]["next_action"] = action
        if inventory.get("reconciliation_status") != "complete": errors.append("backend_inventory_inconclusive")
        if len(matches) != 1: errors.append("controller_identity_unresolved" if not matches else "controller_identity_ambiguous")
        elif matches[0]["status"] != BOUND: errors.append("controller_formal_readback_incomplete")
        elif "route_readback" not in matches[0]: errors.append("controller_applied_route_incomplete")
        return {"decision": "allow" if not errors else "repair", "status": "complete" if not errors else "inconclusive", "errors": errors, "matches": matches}
    registered = [dict(row) for row in db.conn.execute(
        "SELECT * FROM threads WHERE run_id=? AND task_id IS NOT NULL", (run_id,)
    )]
    live_registered = [row for row in registered if not _verified_archived_audit_row(row)]
    if not live_registered:
        return {"decision": "allow", "status": "complete", "errors": [], "matches": []}
    discovered = {}
    errors = []
    for row in live_registered:
        queries = []
        if row.get("formal_thread_id") and row.get("host_id"):
            queries.append({"formal_thread_id": row["formal_thread_id"]})
        if row.get("client_thread_id"):
            queries.append({"client_thread_id": row["client_thread_id"]})
        if row.get("title_token"):
            queries.append({"title_prefix": row["title_token"]})
        if not queries:
            errors.append("registry_identity_index_missing")
            continue
        for query in queries:
            try:
                inventory = list_tasks(backend, page_limit=page_limit, **query)
            except (BackendError, OSError, ValueError) as exc:
                errors.append("backend_query_failed:" + str(exc))
                continue
            if inventory.get("reconciliation_status") != "complete":
                errors.append("backend_inventory_inconclusive")
            for task in inventory.get("tasks", []):
                if not isinstance(task, dict):
                    errors.append("invalid_backend_task")
                    continue
                formal = task.get("formal_thread_id") or task.get("formalThreadId") or task.get("thread_id") or task.get("threadId")
                host = task.get("host_id") or task.get("hostId")
                key = (formal, host, task.get("client_thread_id") or task.get("clientThreadId"), task.get("title"))
                discovered[key] = task
                if formal and host and "readback" not in task:
                    try:
                        task["readback"] = read_task(backend, formal, host)
                    except BackendError:
                        errors.append("formal_readback_failed")
    result = reconcile_inventory(db, run_id, {
        "reconciliation_status": "complete" if not errors else "inconclusive",
        "tasks": list(discovered.values()),
    })
    result["errors"] = sorted(set(result.get("errors", []) + errors))
    if result["errors"]:
        result["decision"] = "repair"
        result["status"] = "inconclusive" if "backend_inventory_inconclusive" in result["errors"] else "mismatch"
    return result


def adopt_controller(db, run_id, backend, *, thread_id: str, formal_thread_id: str,
                      host_id: str, task_id: str, attempt_id: str, owner_id: str,
                      cwd: str, project_id: str, identity_evidence: list[str] | None = None,
                      project_id_source: str | None = None,
                      project_canonical_path: str | None = None,
                      project_identity_evidence: list[str] | None = None,
                      expected_version: int | None = None):
    """Enroll an existing native thread, then cross the normal recovery barrier.

    The backend owns the native read and metadata write. SQLite receives the
    binding only after the returned formal readback and applied route match the
    registered controller. This path is intentionally explicit and cannot be
    reached through title/session discovery.
    """
    row = db.conn.execute(
        "SELECT * FROM threads WHERE run_id=? AND thread_id=? AND task_id IS NOT NULL",
        (run_id, thread_id),
    ).fetchone()
    if row is None:
        raise BackendError("controller thread is not registered with managed identity")
    params = {
        "formal_thread_id": formal_thread_id, "host_id": host_id, "task_id": task_id,
        "run_id": run_id, "attempt_id": attempt_id, "owner_id": owner_id,
        "cwd": cwd, "project_id": project_id, "identity_evidence": identity_evidence,
        "project_id_source": project_id_source,
        "project_canonical_path": project_canonical_path,
        "project_identity_evidence": project_identity_evidence,
    }
    if hasattr(backend, "call"):
        adopted = backend.call("adopt_thread", params)
    else:
        request = getattr(backend, "request", None)
        if not callable(request):
            raise BackendError("backend does not expose adoption")
        adopted = request("adopt_thread", params)
    if not isinstance(adopted, dict):
        raise BackendError("adoption returned no identity receipt")
    readback = read_task(backend, formal_thread_id, host_id)
    candidate = Candidate(
        formal_thread_id=readback.get("formal_thread_id"), host_id=readback.get("host_id"),
        task_id=readback.get("task_id"), run_id=readback.get("run_id"),
        attempt_id=readback.get("attempt_id"), owner_id=readback.get("owner_id"),
        cwd=readback.get("cwd"), project_id=readback.get("project_id"),
        lifecycle=readback.get("lifecycle"),
        readback_evidence=tuple(readback.get("readback_evidence", [])),
    )
    decision = bind_candidate(dict(row), candidate)
    if decision.status != BOUND:
        raise BackendError("adoption formal identity rejected: " + decision.reason)
    route = read_applied_route(backend, formal_thread_id, host_id)
    from next_action import next_action
    action = next_action(db, run_id)
    if action.get("kind") in {"repair_queue", "final_release"}:
        raise BackendError("adoption cannot cross an unavailable queue gate")
    db.persist_controller_recovery(run_id, thread_id, readback, route, action, expected_version)
    return {"decision": "allow", "status": "complete", "thread_id": thread_id,
            "adoption": adopted, "identity": readback, "route": route,
            "next_action": action}

def audit(db, run_id):
    errors=[]
    if not db.conn.execute("SELECT 1 FROM runs WHERE run_id=?",(run_id,)).fetchone(): return ["run_not_found"]
    if db.conn.execute("SELECT COUNT(*) FROM specs WHERE run_id=? AND status IN ('implementing','handoff_received','verifying','ready_to_merge')",(run_id,)).fetchone()[0] > 1: errors.append("multiple_active_specs")
    if db.conn.execute("SELECT COUNT(*) FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE s.run_id=? AND t.status='closed' AND (t.commits='[]' OR t.tests='[]')",(run_id,)).fetchone()[0]: errors.append("closed_ticket_missing_evidence")
    if db.conn.execute("SELECT COUNT(*) FROM threads WHERE run_id=? AND lifecycle='created' AND next_action IS NULL",(run_id,)).fetchone()[0]: errors.append("unassigned_created_thread")
    if db.conn.execute("SELECT COUNT(*) FROM actions WHERE run_id=? AND status IN ('pending','running')",(run_id,)).fetchone()[0] > 1: errors.append("multiple_pending_actions")
    structure = structure_from_db(db, run_id)
    errors.extend("dependency_" + item["code"] for item in structure["errors"])
    if structure["status"] == "valid":
        candidates = [row[0] for row in db.conn.execute("SELECT spec_id FROM specs WHERE run_id=? AND status NOT IN ('closed','cancelled') ORDER BY position", (run_id,))]
        if candidates:
            readiness = readiness_from_db(db, run_id, candidates[0])
            if readiness["status"] == "blocked":
                errors.append("dependency_blocked:" + candidates[0])
    return errors


def dependency_audit(db, run_id, spec_id=None):
    structure = structure_from_db(db, run_id)
    if structure["status"] != "valid":
        return {"decision": "repair", "status": "invalid", "errors": structure["errors"], "readiness": None}
    candidates = [row[0] for row in db.conn.execute("SELECT spec_id FROM specs WHERE run_id=? AND status NOT IN ('closed','cancelled') ORDER BY position", (run_id,))]
    target = spec_id or (candidates[0] if candidates else None)
    if target is None:
        return {"decision": "allow", "status": "ready", "errors": [], "readiness": None}
    readiness = readiness_from_db(db, run_id, target)
    return {"decision": "repair" if readiness["status"] == "blocked" else "allow", "status": readiness["status"], "errors": readiness.get("errors", []), "readiness": readiness}

def main():
    p=argparse.ArgumentParser(); p.add_argument('--db',type=Path,required=True); p.add_argument('--run-id',required=True); p.add_argument('--inventory',type=Path); p.add_argument('--backend-command'); a=p.parse_args(); db=ControlDB.open_existing(a.db)
    try:
        if a.backend_command:
            from task_backend import MCP_CONNECTOR, JsonLineTransport, TaskBackend
            transport = None
            try:
                transport = TaskBackend(MCP_CONNECTOR, JsonLineTransport(a.backend_command))
                probe_and_record(db, a.run_id, transport.transport)
                result=reconcile_backend(db,a.run_id,transport)
            except (BackendError, OSError, ValueError) as exc:
                result={"decision":"repair","status":"inconclusive","errors":["capability_handshake_failed:" + str(exc)],"matches":[]}
            finally:
                if transport is not None:
                    transport.transport.close()
            result['run_id']=a.run_id; print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 0 if result['decision']=='allow' else 1
        if a.inventory:
            try: result=reconcile_inventory(db,a.run_id,json.loads(a.inventory.read_text(encoding='utf-8')))
            except (OSError,UnicodeError,json.JSONDecodeError): result={'decision':'repair','status':'inconclusive','errors':['inventory_unreadable'],'matches':[]}
            result['run_id']=a.run_id; print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 0 if result['decision']=='allow' else 1
        errors=audit(db,a.run_id); dependency=dependency_audit(db,a.run_id); print(json.dumps({'run_id':a.run_id,'decision':'allow' if not errors else 'repair','errors':errors,'dependency':dependency},ensure_ascii=False)); return 0 if not errors else 1
    finally: db.close()
if __name__=='__main__': raise SystemExit(main())
