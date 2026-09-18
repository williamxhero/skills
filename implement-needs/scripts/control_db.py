"""SQLite state store for the Implement Needs controller."""
from __future__ import annotations
import hashlib
import json, sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, initiative TEXT NOT NULL, requirement TEXT NOT NULL, status TEXT NOT NULL, current_action TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS specs(spec_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), title TEXT NOT NULL, status TEXT NOT NULL, position INTEGER NOT NULL, blocked_by TEXT NOT NULL DEFAULT '[]', acceptance TEXT NOT NULL DEFAULT '[]', generation INTEGER NOT NULL DEFAULT 0, UNIQUE(run_id,position));
CREATE TABLE IF NOT EXISTS tickets(ticket_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL REFERENCES specs(spec_id), title TEXT NOT NULL, status TEXT NOT NULL, blocked_by TEXT NOT NULL DEFAULT '[]', commits TEXT NOT NULL DEFAULT '[]', tests TEXT NOT NULL DEFAULT '[]', issue_url TEXT, UNIQUE(spec_id,title));
CREATE TABLE IF NOT EXISTS threads(thread_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), kind TEXT NOT NULL, spec_id TEXT REFERENCES specs(spec_id), lifecycle TEXT NOT NULL, outcome TEXT NOT NULL DEFAULT 'unknown', next_action TEXT, last_observed_at TEXT NOT NULL, archive_operation_evidence TEXT NOT NULL DEFAULT '[]', archive_readback_evidence TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS actions(action_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), kind TEXT NOT NULL, target TEXT NOT NULL, status TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, attempts INTEGER NOT NULL DEFAULT 0, result TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(event_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL, observed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions(decision_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), subject TEXT NOT NULL, selected TEXT NOT NULL, recommendation TEXT, evidence TEXT NOT NULL DEFAULT '[]', rationale TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runtime_observations(
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    observation_key TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    scope TEXT NOT NULL,
    unit TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    duration_ms REAL,
    source TEXT NOT NULL,
    usage TEXT NOT NULL DEFAULT '{}',
    metadata TEXT NOT NULL DEFAULT '{}',
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runtime_observations_run_phase ON runtime_observations(run_id, phase, observed_at);
CREATE TABLE IF NOT EXISTS operation_intents(
    intent_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    operation TEXT NOT NULL,
    target TEXT NOT NULL,
    generation INTEGER NOT NULL,
    input_digest TEXT NOT NULL,
    normalized_parameters TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    external_request_id TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    result TEXT NOT NULL DEFAULT '{}',
    reconciliation_evidence TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id,operation,target,generation,input_digest)
);
CREATE INDEX IF NOT EXISTS operation_intents_run_status ON operation_intents(run_id,status,updated_at);
CREATE TABLE IF NOT EXISTS action_claims(
    action_id INTEGER PRIMARY KEY REFERENCES actions(action_id),
    owner_id TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    supports_fencing INTEGER NOT NULL DEFAULT 0,
    outcome_reconciled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS delivery_proofs(
    proof_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    artifact_ref TEXT NOT NULL,
    evidence TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE(entity_type,entity_id,artifact_type,artifact_ref)
);
CREATE TABLE IF NOT EXISTS dependency_waivers(
    waiver_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dependent_type TEXT NOT NULL,
    dependent_id TEXT NOT NULL,
    blocker_type TEXT NOT NULL,
    blocker_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    authorization_source TEXT NOT NULL,
    scope TEXT NOT NULL,
    evidence TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE(dependent_type,dependent_id,blocker_type,blocker_id)
);
CREATE INDEX IF NOT EXISTS dependency_waivers_lookup ON dependency_waivers(dependent_type,dependent_id,blocker_type,blocker_id);
CREATE TABLE IF NOT EXISTS recovery_states(
    recovery_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    action_id INTEGER NOT NULL REFERENCES actions(action_id),
    category TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    retry_owner TEXT NOT NULL,
    budget_version TEXT NOT NULL,
    max_attempts INTEGER NOT NULL,
    deadline_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_strategy_digest TEXT NOT NULL,
    last_progress_marker TEXT NOT NULL,
    last_evidence_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(action_id,fingerprint)
);
CREATE INDEX IF NOT EXISTS recovery_states_action ON recovery_states(action_id,status,updated_at);
CREATE TABLE IF NOT EXISTS runtime_snapshots(
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
    state_version INTEGER NOT NULL,
    event_cursor INTEGER NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS unresolved_exceptions(
    exception_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    fingerprint TEXT NOT NULL,
    category TEXT NOT NULL,
    summary TEXT NOT NULL,
    log_uri TEXT,
    details TEXT NOT NULL DEFAULT '{}',
    resolved INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    UNIQUE(run_id,fingerprint)
);
CREATE INDEX IF NOT EXISTS unresolved_exceptions_run ON unresolved_exceptions(run_id,resolved,last_seen);
CREATE TABLE IF NOT EXISTS external_waits(
    external_request_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    action_id INTEGER,
    event_cursor INTEGER NOT NULL,
    wake_condition TEXT NOT NULL,
    next_safe_check_at TEXT NOT NULL,
    status TEXT NOT NULL,
    last_result_digest TEXT,
    poll_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS terminal_validations(
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
    state_version INTEGER NOT NULL,
    decision TEXT NOT NULL,
    evidence TEXT NOT NULL,
    validated_at TEXT NOT NULL
);
"""

def now() -> str: return datetime.now(timezone.utc).isoformat()


@contextmanager
def transaction(conn):
    """Use a real SQLite write transaction even though the connection is autocommit."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _json_object(value, name):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _timestamp(value, name):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty ISO-8601 string")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be ISO-8601") from exc
    return value


def _canonical_json(value, name="parameters"):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be JSON serializable") from exc


def _evidence(value):
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError("reconciliation evidence must be a non-empty string list")
    return value


def _time_after(seconds):
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 1:
        raise ValueError("lease_seconds must be a positive integer")
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _is_expired(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) <= datetime.now(timezone.utc)


class ActionClaimConflict(RuntimeError):
    """Another executor currently owns the action."""


class UnsafeLeaseTakeover(RuntimeError):
    """A timed-out executor might still perform an unfenced external effect."""

class ControlDB:
    def __init__(self, path: str | Path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.conn=sqlite3.connect(self.path,timeout=30,isolation_level=None); self.conn.row_factory=sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL"); self.conn.execute("PRAGMA foreign_keys=ON"); self.conn.executescript(SCHEMA)
    def close(self): self.conn.close()
    def event(self,run_id,entity_type,entity_id,event_type,payload):
        self.conn.execute("INSERT INTO events(run_id,entity_type,entity_id,event_type,payload,observed_at) VALUES(?,?,?,?,?,?)",(run_id,entity_type,entity_id,event_type,json.dumps(payload,ensure_ascii=False,sort_keys=True),now()))
    def create_run(self,run_id,initiative,requirement):
        stamp=now()
        with self.conn:
            self.conn.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)",(run_id,initiative,requirement,"active",None,stamp,stamp)); self.event(run_id,"run",run_id,"run_created",{})
    def set_action(self,run_id,kind,target,status="pending"):
        key=f"{run_id}:{kind}:{target}"; row=self.conn.execute("SELECT action_id FROM actions WHERE idempotency_key=?",(key,)).fetchone()
        if row: return row[0]
        stamp=now()
        with self.conn:
            cur=self.conn.execute("INSERT INTO actions(run_id,kind,target,status,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(run_id,kind,target,status,key,stamp,stamp)); self.conn.execute("UPDATE runs SET current_action=?,updated_at=? WHERE run_id=?",(f"{kind}:{target}",stamp,run_id)); return cur.lastrowid
    def finish_action(self,action_id,status,result=None,error=None):
            with transaction(self.conn):
                row = self.conn.execute("SELECT run_id FROM actions WHERE action_id=?", (action_id,)).fetchone()
                if not row:
                    raise ValueError("unknown action")
                stamp = now()
                self.conn.execute("UPDATE actions SET status=?,result=?,error=?,attempts=attempts+1,updated_at=? WHERE action_id=?",(status,json.dumps(result,ensure_ascii=False) if result is not None else None,error,stamp,action_id))
                self.conn.execute("DELETE FROM action_claims WHERE action_id=?", (action_id,))
                self.event(row["run_id"], "action", str(action_id), "action_finished", {"status": status, "error": error})
    def add_spec(self,run_id,spec_id,title,position,blocked_by=None,acceptance=None):
        with self.conn:
            self.conn.execute("INSERT INTO specs(spec_id,run_id,title,status,position,blocked_by,acceptance) VALUES(?,?,?,?,?,?,?)",(spec_id,run_id,title,"planned",position,json.dumps(blocked_by or []),json.dumps(acceptance or []))); self.event(run_id,"spec",spec_id,"spec_created",{"title":title})
    def add_ticket(self,spec_id,ticket_id,title,blocked_by=None,issue_url=None):
        run_id=self.conn.execute("SELECT run_id FROM specs WHERE spec_id=?",(spec_id,)).fetchone()[0]
        with self.conn:
            self.conn.execute("INSERT INTO tickets(ticket_id,spec_id,title,status,blocked_by,issue_url) VALUES(?,?,?,?,?,?)",(ticket_id,spec_id,title,"planned",json.dumps(blocked_by or []),issue_url)); self.event(run_id,"ticket",ticket_id,"ticket_created",{"spec_id":spec_id})
    def add_thread(self,run_id,thread_id,kind,spec_id=None):
        with self.conn:
            self.conn.execute("INSERT INTO threads(thread_id,run_id,kind,spec_id,lifecycle,last_observed_at) VALUES(?,?,?,?,?,?)",(thread_id,run_id,kind,spec_id,"created",now())); self.event(run_id,"thread",thread_id,"thread_registered",{"kind":kind,"spec_id":spec_id})
    def add_observation(self,run_id,entity_type,entity_id,observation):
        with self.conn:
            self.event(run_id,entity_type,entity_id,"external_observation",observation)
    def record_runtime_observation(
        self, run_id, observation_key, entity_type, entity_id, phase, status,
        scope="run", unit="count", started_at=None, ended_at=None,
        duration_ms=None, source="controller", usage=None, metadata=None,
    ):
        """Persist one idempotent runtime observation and its audit event."""
        if not observation_key or not isinstance(observation_key, str):
            raise ValueError("observation_key is required")
        if not phase or not isinstance(phase, str):
            raise ValueError("phase is required")
        if not scope or not unit or not source:
            raise ValueError("scope, unit, and source are required")
        started_at = _timestamp(started_at, "started_at")
        ended_at = _timestamp(ended_at, "ended_at")
        if duration_ms is not None:
            duration_ms = float(duration_ms)
            if duration_ms < 0:
                raise ValueError("duration_ms must be non-negative")
        usage = _json_object(usage, "usage")
        metadata = _json_object(metadata, "metadata")
        payload = {
            "observation_key": observation_key, "phase": phase, "status": status,
            "scope": scope, "unit": unit, "started_at": started_at,
            "ended_at": ended_at, "duration_ms": duration_ms, "source": source,
            "usage": usage, "metadata": metadata,
        }
        existing = self.conn.execute(
            "SELECT observation_id,run_id,entity_type,entity_id,phase,status,scope,unit,started_at,ended_at,duration_ms,source,usage,metadata "
            "FROM runtime_observations WHERE observation_key=?", (observation_key,)
        ).fetchone()
        if existing:
            existing_payload = dict(existing)
            existing_payload["usage"] = json.loads(existing_payload["usage"])
            existing_payload["metadata"] = json.loads(existing_payload["metadata"])
            expected = {key: existing_payload[key] for key in (
                "phase", "status", "scope", "unit", "started_at", "ended_at",
                "duration_ms", "source", "usage", "metadata",
            )}
            actual = {key: payload[key] for key in expected}
            if existing["run_id"] != run_id or existing["entity_type"] != entity_type or existing["entity_id"] != entity_id or expected != actual:
                raise ValueError("observation_key already exists with different data")
            return {"observation_id": existing["observation_id"], "created": False, **payload}
        stamp = now()
        with transaction(self.conn):
            cursor = self.conn.execute(
                "INSERT INTO runtime_observations(run_id,observation_key,entity_type,entity_id,phase,status,scope,unit,started_at,ended_at,duration_ms,source,usage,metadata,observed_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, observation_key, entity_type, entity_id, phase, status,
                 scope, unit, started_at, ended_at, duration_ms, source,
                 json.dumps(usage, ensure_ascii=False, sort_keys=True),
                 json.dumps(metadata, ensure_ascii=False, sort_keys=True), stamp),
            )
            self.event(run_id, entity_type, entity_id, "runtime_observation_recorded", payload)
        return {"observation_id": cursor.lastrowid, "created": True, **payload}
    def create_operation_intent(
        self, run_id, operation, target, parameters, generation=0, idempotency_key=None,
    ):
        """Create or recover a stable external-operation intent.

        The key identifies a logical request, not an individual network attempt.
        A retry therefore returns the same row and request id. Changed parameters,
        targets, or generations produce a new logical intent unless a caller
        explicitly reuses a key, in which case the mismatch is rejected.
        """
        if not isinstance(operation, str) or not operation.strip():
            raise ValueError("operation is required")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("target is required")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
            raise ValueError("generation must be a non-negative integer")
        normalized = _canonical_json(parameters)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        key = idempotency_key or f"{run_id}:{operation}:{target}:{generation}:{digest}"
        request_id = "in-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        stamp = now()
        with transaction(self.conn):
            existing = self.conn.execute(
                "SELECT * FROM operation_intents WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                if any((existing[field] != expected) for field, expected in {
                    "run_id": run_id, "operation": operation, "target": target,
                    "generation": generation, "input_digest": digest,
                    "normalized_parameters": normalized,
                }.items()):
                    raise ValueError("idempotency key already exists with different intent")
                return {"intent": dict(existing), "created": False}
            cursor = self.conn.execute(
                "INSERT INTO operation_intents(run_id,operation,target,generation,input_digest,normalized_parameters,idempotency_key,external_request_id,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, operation, target, generation, digest, normalized, key,
                 request_id, "prepared", stamp, stamp),
            )
            self.event(run_id, "operation_intent", str(cursor.lastrowid), "operation_intent_created", {
                "operation": operation, "target": target, "generation": generation,
                "input_digest": digest, "idempotency_key": key,
                "external_request_id": request_id,
            })
            row = self.conn.execute("SELECT * FROM operation_intents WHERE intent_id=?", (cursor.lastrowid,)).fetchone()
            return {"intent": dict(row), "created": True}
    def start_operation_intent(self, intent_id, executor_id):
        if not isinstance(executor_id, str) or not executor_id.strip():
            raise ValueError("executor_id is required")
        with transaction(self.conn):
            row = self.conn.execute("SELECT * FROM operation_intents WHERE intent_id=?", (intent_id,)).fetchone()
            if not row:
                raise ValueError("unknown operation intent")
            if row["status"] == "outcome_unknown":
                raise ValueError("unknown outcome must be reconciled before retry")
            if row["status"] not in {"prepared", "reconciled_not_found"}:
                raise ValueError(f"operation intent cannot start from {row['status']}")
            stamp = now()
            self.conn.execute(
                "UPDATE operation_intents SET status='executing',attempts=attempts+1,updated_at=? WHERE intent_id=?",
                (stamp, intent_id),
            )
            self.event(row["run_id"], "operation_intent", str(intent_id), "operation_intent_started", {
                "executor_id": executor_id, "external_request_id": row["external_request_id"],
            })
            return dict(self.conn.execute("SELECT * FROM operation_intents WHERE intent_id=?", (intent_id,)).fetchone())
    def mark_operation_unknown(self, intent_id, reason, evidence):
        evidence = _evidence(evidence)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("unknown outcome reason is required")
        with transaction(self.conn):
            row = self.conn.execute("SELECT * FROM operation_intents WHERE intent_id=?", (intent_id,)).fetchone()
            if not row:
                raise ValueError("unknown operation intent")
            if row["status"] not in {"executing", "outcome_unknown"}:
                raise ValueError(f"operation intent cannot become unknown from {row['status']}")
            current = json.loads(row["reconciliation_evidence"] or "[]")
            current.extend(evidence)
            stamp = now()
            self.conn.execute(
                "UPDATE operation_intents SET status='outcome_unknown',result=?,reconciliation_evidence=?,updated_at=? WHERE intent_id=?",
                (json.dumps({"reason": reason}, ensure_ascii=False, sort_keys=True), json.dumps(sorted(set(current))), stamp, intent_id),
            )
            self.event(row["run_id"], "operation_intent", str(intent_id), "operation_outcome_unknown", {
                "reason": reason, "evidence": evidence,
            })
    def reconcile_operation_intent(self, intent_id, outcome, evidence, result=None):
        evidence = _evidence(evidence)
        if outcome not in {"not_found", "succeeded", "failed"}:
            raise ValueError("reconciliation outcome must be not_found, succeeded, or failed")
        with transaction(self.conn):
            row = self.conn.execute("SELECT * FROM operation_intents WHERE intent_id=?", (intent_id,)).fetchone()
            if not row:
                raise ValueError("unknown operation intent")
            if row["status"] != "outcome_unknown":
                raise ValueError(f"operation intent is not awaiting reconciliation: {row['status']}")
            status = "reconciled_not_found" if outcome == "not_found" else outcome
            current = json.loads(row["reconciliation_evidence"] or "[]")
            current.extend(evidence)
            stamp = now()
            self.conn.execute(
                "UPDATE operation_intents SET status=?,result=?,reconciliation_evidence=?,updated_at=? WHERE intent_id=?",
                (status, _canonical_json(result or {"outcome": outcome}), json.dumps(sorted(set(current))), stamp, intent_id),
            )
            self.event(row["run_id"], "operation_intent", str(intent_id), "operation_reconciled", {
                "outcome": outcome, "evidence": evidence,
            })
            return dict(self.conn.execute("SELECT * FROM operation_intents WHERE intent_id=?", (intent_id,)).fetchone())
    def record_delivery_proof(self, entity_type, entity_id, artifact_type, artifact_ref, evidence):
        if not all(isinstance(value, str) and value.strip() for value in (entity_type, entity_id, artifact_type, artifact_ref)):
            raise ValueError("delivery proof fields are required")
        evidence = _evidence(evidence)
        existing = self.conn.execute(
            "SELECT * FROM delivery_proofs WHERE entity_type=? AND entity_id=? AND artifact_type=? AND artifact_ref=?",
            (entity_type, entity_id, artifact_type, artifact_ref),
        ).fetchone()
        if existing:
            if json.loads(existing["evidence"]) != evidence:
                raise ValueError("delivery proof already exists with different evidence")
            return {"proof": dict(existing), "created": False}
        stamp = now()
        with transaction(self.conn):
            cursor = self.conn.execute(
                "INSERT INTO delivery_proofs(entity_type,entity_id,artifact_type,artifact_ref,evidence,observed_at) VALUES(?,?,?,?,?,?)",
                (entity_type, entity_id, artifact_type, artifact_ref, json.dumps(evidence, ensure_ascii=False), stamp),
            )
        return {"proof": dict(self.conn.execute("SELECT * FROM delivery_proofs WHERE proof_id=?", (cursor.lastrowid,)).fetchone()), "created": True}
    def waive_dependency(self, dependent_type, dependent_id, blocker_type, blocker_id, reason, authorization_source, scope, evidence):
        if not all(isinstance(value, str) and value.strip() for value in (dependent_type, dependent_id, blocker_type, blocker_id, reason, authorization_source, scope)):
            raise ValueError("dependency waiver fields are required")
        evidence = _evidence(evidence)
        fields = (dependent_type, dependent_id, blocker_type, blocker_id)
        existing = self.conn.execute(
            "SELECT * FROM dependency_waivers WHERE dependent_type=? AND dependent_id=? AND blocker_type=? AND blocker_id=?", fields
        ).fetchone()
        if existing:
            expected = {"reason": reason, "authorization_source": authorization_source, "scope": scope, "evidence": evidence}
            actual = {"reason": existing["reason"], "authorization_source": existing["authorization_source"], "scope": existing["scope"], "evidence": json.loads(existing["evidence"])}
            if actual != expected:
                raise ValueError("dependency waiver already exists with different details")
            return {"waiver": dict(existing), "created": False}
        stamp = now()
        with transaction(self.conn):
            cursor = self.conn.execute(
                "INSERT INTO dependency_waivers(dependent_type,dependent_id,blocker_type,blocker_id,reason,authorization_source,scope,evidence,observed_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (*fields, reason, authorization_source, scope, json.dumps(evidence, ensure_ascii=False), stamp),
            )
        return {"waiver": dict(self.conn.execute("SELECT * FROM dependency_waivers WHERE waiver_id=?", (cursor.lastrowid,)).fetchone()), "created": True}
    def claim_action(self, action_id, owner_id, lease_seconds=60, supports_fencing=False, outcome_reconciled=False):
        """Atomically give one executor permission to run an action.

        The default is deliberately conservative: an expired claim cannot be
        reclaimed automatically because the old executor might still be making an
        external request. Only a fencing-capable backend with a reconciled outcome
        can transfer ownership.
        """
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("owner_id is required")
        expires_at = _time_after(lease_seconds)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            action = self.conn.execute("SELECT run_id,status FROM actions WHERE action_id=?", (action_id,)).fetchone()
            if not action:
                raise ValueError("unknown action")
            if action["status"] not in {"pending", "running"}:
                raise ValueError(f"action cannot be claimed from {action['status']}")
            claim = self.conn.execute("SELECT * FROM action_claims WHERE action_id=?", (action_id,)).fetchone()
            if claim:
                if not _is_expired(claim["lease_expires_at"]):
                    raise ActionClaimConflict(f"action {action_id} is owned by {claim['owner_id']}")
                if not (supports_fencing and outcome_reconciled):
                    raise UnsafeLeaseTakeover(
                        "expired action claim needs fencing support and outcome reconciliation before takeover"
                    )
                self.conn.execute("DELETE FROM action_claims WHERE action_id=?", (action_id,))
                self.event(action["run_id"], "action", str(action_id), "action_claim_relinquished", {
                    "previous_owner_id": claim["owner_id"], "reason": "expired_fenced_and_reconciled",
                })
            stamp = now()
            self.conn.execute(
                "INSERT INTO action_claims(action_id,owner_id,claimed_at,lease_expires_at,supports_fencing,outcome_reconciled) VALUES(?,?,?,?,?,?)",
                (action_id, owner_id, stamp, expires_at, int(supports_fencing), int(outcome_reconciled)),
            )
            self.conn.execute("UPDATE actions SET status='running',updated_at=? WHERE action_id=?", (stamp, action_id))
            self.event(action["run_id"], "action", str(action_id), "action_claimed", {
                "owner_id": owner_id, "lease_expires_at": expires_at,
                "supports_fencing": bool(supports_fencing), "outcome_reconciled": bool(outcome_reconciled),
            })
            result = dict(self.conn.execute("SELECT * FROM action_claims WHERE action_id=?", (action_id,)).fetchone())
            self.conn.execute("COMMIT")
            return result
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
    def assert_action_effect_permitted(self, action_id, owner_id):
        claim = self.conn.execute("SELECT * FROM action_claims WHERE action_id=?", (action_id,)).fetchone()
        if not claim or claim["owner_id"] != owner_id:
            raise ActionClaimConflict("action effect is not owned by this executor")
        if _is_expired(claim["lease_expires_at"]):
            raise UnsafeLeaseTakeover("action effect permission expired; reconcile before continuing")
        return dict(claim)
    def update_spec(self,spec_id,status):
        from transitions import SPEC_TRANSITIONS, transition
        row=self.conn.execute("SELECT run_id,status FROM specs WHERE spec_id=?",(spec_id,)).fetchone()
        if not row: raise ValueError("unknown spec")
        transition(SPEC_TRANSITIONS,row[1],status)
        with self.conn:
            self.conn.execute("UPDATE specs SET status=? WHERE spec_id=?",(status,spec_id)); self.event(row[0],"spec",spec_id,"spec_state_changed",{"status":status})
    def update_ticket(self,ticket_id,status,commits=None,tests=None):
        row=self.conn.execute("SELECT t.status,s.run_id FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE t.ticket_id=?",(ticket_id,)).fetchone()
        if not row: raise ValueError("unknown ticket")
        allowed={"planned":{"ready","blocked"},"ready":{"implementing","blocked"},"implementing":{"verified","blocked"},"verified":{"merged","blocked"},"merged":{"closed"},"blocked":{"ready","implementing","cancelled"},"closed":set(),"cancelled":set()}
        if status not in allowed.get(row[0],set()): raise ValueError(f"illegal ticket transition: {row[0]} -> {status}")
        current = self.conn.execute("SELECT commits,tests FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        delivery_evidence = (commits if commits is not None else json.loads(current["commits"])) + (tests if tests is not None else json.loads(current["tests"]))
        if status == "closed" and not delivery_evidence:
            raise ValueError("closed ticket requires delivery evidence")
        with transaction(self.conn):
            self.conn.execute("UPDATE tickets SET status=?,commits=COALESCE(?,commits),tests=COALESCE(?,tests) WHERE ticket_id=?",(status,json.dumps(commits) if commits is not None else None,json.dumps(tests) if tests is not None else None,ticket_id)); self.event(row[1],"ticket",ticket_id,"ticket_state_changed",{"status":status,"commits":commits,"tests":tests})
            if status == "closed":
                self.conn.execute(
                    "INSERT OR IGNORE INTO delivery_proofs(entity_type,entity_id,artifact_type,artifact_ref,evidence,observed_at) VALUES(?,?,?,?,?,?)",
                    ("ticket", ticket_id, "delivery", f"ticket:{ticket_id}:closed", json.dumps(delivery_evidence, ensure_ascii=False), now()),
                )

    def advance_local_action(self, run_id, kind, target, next_status):
        """Commit a uniquely determined local transition and its receipt together."""
        if kind not in {"advance_spec", "advance_ticket"}:
            raise ValueError("unsupported local action")
        key = f"{run_id}:{kind}:{target}"
        with transaction(self.conn):
            action = self.conn.execute("SELECT * FROM actions WHERE idempotency_key=?", (key,)).fetchone()
            if kind == "advance_spec":
                entity = self.conn.execute("SELECT run_id,status FROM specs WHERE spec_id=?", (target,)).fetchone()
                if not entity or entity["run_id"] != run_id:
                    raise ValueError("unknown spec")
                from transitions import SPEC_TRANSITIONS, transition
                transitions = SPEC_TRANSITIONS
            else:
                entity = self.conn.execute(
                    "SELECT s.run_id,t.status FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE t.ticket_id=?", (target,)
                ).fetchone()
                if not entity or entity["run_id"] != run_id:
                    raise ValueError("unknown ticket")
                transitions = {"planned":{"ready","blocked"},"ready":{"implementing","blocked"},"implementing":{"verified","blocked"},"verified":{"merged","blocked"},"merged":{"closed"},"blocked":{"ready","implementing","cancelled"},"closed":set(),"cancelled":set()}
            if action and action["status"] == "succeeded":
                return int(action["action_id"])
            if action and entity["status"] == next_status:
                action_id = int(action["action_id"])
                self.conn.execute("UPDATE actions SET status='succeeded',result=?,error=NULL,attempts=attempts+1,updated_at=? WHERE action_id=?", (json.dumps({"next_status": next_status}, ensure_ascii=False), now(), action_id))
                self.event(run_id, "action", str(action_id), "action_reconciled", {"status": "succeeded", "next_status": next_status})
                return action_id
            if action and action["status"] not in {"pending", "running"}:
                raise ValueError(f"local action cannot resume from {action['status']}")
            if action:
                action_id = int(action["action_id"])
            else:
                stamp = now()
                cursor = self.conn.execute(
                    "INSERT INTO actions(run_id,kind,target,status,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (run_id, kind, target, "pending", key, stamp, stamp),
                )
                action_id = int(cursor.lastrowid)
                self.conn.execute("UPDATE runs SET current_action=?,updated_at=? WHERE run_id=?", (f"{kind}:{target}", stamp, run_id))
            if kind == "advance_spec":
                transition(transitions, entity["status"], next_status)
                self.conn.execute("UPDATE specs SET status=? WHERE spec_id=?", (next_status, target))
                self.event(run_id, "spec", target, "spec_state_changed", {"status": next_status})
            else:
                if next_status not in transitions.get(entity["status"], set()):
                    raise ValueError(f"illegal ticket transition: {entity['status']} -> {next_status}")
                self.conn.execute("UPDATE tickets SET status=? WHERE ticket_id=?", (next_status, target))
                self.event(run_id, "ticket", target, "ticket_state_changed", {"status": next_status, "commits": None, "tests": None})
            self.conn.execute("UPDATE actions SET status='succeeded',result=?,attempts=attempts+1,updated_at=? WHERE action_id=?", (json.dumps({"next_status": next_status}, ensure_ascii=False), now(), action_id))
            self.event(run_id, "action", str(action_id), "action_finished", {"status": "succeeded", "error": None})
            return action_id
    def update_thread(self,run_id,thread_id,lifecycle,outcome="unknown",next_action=None,operation=None,readback=None):
        with self.conn:
            row=self.conn.execute("SELECT lifecycle FROM threads WHERE thread_id=? AND run_id=?",(thread_id,run_id)).fetchone()
            if not row: raise ValueError("unknown thread")
            from transitions import THREAD_TRANSITIONS, transition
            transition(THREAD_TRANSITIONS,row[0],lifecycle)
            current=self.conn.execute("SELECT archive_operation_evidence,archive_readback_evidence FROM threads WHERE thread_id=?",(thread_id,)).fetchone()
            operations=json.loads(current[0]); readbacks=json.loads(current[1])
            if operation: operations.append(operation)
            if readback: readbacks.append(readback)
            self.conn.execute("UPDATE threads SET lifecycle=?,outcome=?,next_action=?,last_observed_at=?,archive_operation_evidence=?,archive_readback_evidence=? WHERE thread_id=? AND run_id=?",(lifecycle,outcome,next_action,now(),json.dumps(operations),json.dumps(readbacks),thread_id,run_id)); self.event(run_id,"thread",thread_id,"thread_state_changed",{"lifecycle":lifecycle,"outcome":outcome})
    def decide(self,run_id,subject,selected,recommendation,evidence,rationale):
        with self.conn:
            self.conn.execute("INSERT INTO decisions(run_id,subject,selected,recommendation,evidence,rationale,created_at) VALUES(?,?,?,?,?,?,?)",(run_id,subject,json.dumps(selected,ensure_ascii=False),json.dumps(recommendation,ensure_ascii=False),json.dumps(evidence,ensure_ascii=False),rationale,now())); self.event(run_id,"decision",subject,"controller_approved",{"selected":selected,"recommendation":recommendation,"evidence":evidence,"rationale":rationale})
    def snapshot(self,run_id):
        row=lambda q,p: [dict(x) for x in self.conn.execute(q,p)]
        run=self.conn.execute("SELECT * FROM runs WHERE run_id=?",(run_id,)).fetchone()
        return {"run":dict(run) if run else None,"specs":row("SELECT * FROM specs WHERE run_id=? ORDER BY position",(run_id,)),"tickets":row("SELECT t.* FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE s.run_id=?",(run_id,)),"threads":row("SELECT * FROM threads WHERE run_id=?",(run_id,)),"actions":row("SELECT * FROM actions WHERE run_id=? ORDER BY action_id",(run_id,))}

    def event_cursor(self, run_id):
        row = self.conn.execute("SELECT COALESCE(MAX(event_id),0) FROM events WHERE run_id=?", (run_id,)).fetchone()
        return int(row[0])

    def events_since(self, run_id, event_cursor=0):
        if not isinstance(event_cursor, int) or isinstance(event_cursor, bool) or event_cursor < 0:
            raise ValueError("event_cursor must be a non-negative integer")
        rows = self.conn.execute(
            "SELECT * FROM events WHERE run_id=? AND event_id>? ORDER BY event_id", (run_id, event_cursor)
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            result.append(item)
        return result

    def save_snapshot(self, run_id, payload, expected_event_cursor=None, expected_state_version=None):
        """Save a compact state snapshot only if the caller read the current cursor."""
        if not isinstance(payload, dict):
            raise ValueError("snapshot payload must be a JSON object")
        if expected_event_cursor is not None and (
            not isinstance(expected_event_cursor, int) or isinstance(expected_event_cursor, bool)
        ):
            raise ValueError("expected_event_cursor must be an integer")
        if expected_state_version is not None and (
            not isinstance(expected_state_version, int) or isinstance(expected_state_version, bool)
        ):
            raise ValueError("expected_state_version must be an integer")
        with transaction(self.conn):
            cursor = self.event_cursor(run_id)
            if expected_event_cursor is not None and expected_event_cursor != cursor:
                raise ValueError(f"stale snapshot cursor: expected {expected_event_cursor}, current {cursor}")
            if expected_state_version is not None and expected_state_version != cursor:
                raise ValueError(f"stale snapshot version: expected {expected_state_version}, current {cursor}")
            stamp = now()
            encoded = _canonical_json(payload, "snapshot")
            self.conn.execute(
                "INSERT INTO runtime_snapshots(run_id,state_version,event_cursor,payload,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET state_version=excluded.state_version, "
                "event_cursor=excluded.event_cursor,payload=excluded.payload,updated_at=excluded.updated_at",
                (run_id, cursor, cursor, encoded, stamp, stamp),
            )
            self.event(run_id, "run", run_id, "runtime_snapshot_saved", {"state_version": cursor, "event_cursor": cursor})
            # The snapshot event is intentionally outside the captured cursor. Return the new cursor.
            cursor = self.event_cursor(run_id)
            self.conn.execute(
                "UPDATE runtime_snapshots SET state_version=?,event_cursor=? WHERE run_id=?",
                (cursor, cursor, run_id),
            )
            return {"run_id": run_id, "state_version": cursor, "event_cursor": cursor, "payload": payload}

    def read_snapshot(self, run_id):
        row = self.conn.execute("SELECT * FROM runtime_snapshots WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def record_exception(self, run_id, fingerprint, category, summary, log_uri=None, details=None):
        if not all(isinstance(value, str) and value.strip() for value in (fingerprint, category, summary)):
            raise ValueError("fingerprint, category and summary are required")
        stamp = now()
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO unresolved_exceptions(run_id,fingerprint,category,summary,log_uri,details,first_seen,last_seen) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(run_id,fingerprint) DO UPDATE SET category=excluded.category, "
                "summary=excluded.summary,log_uri=excluded.log_uri,details=excluded.details,resolved=0,last_seen=excluded.last_seen",
                (run_id, fingerprint, category, summary, log_uri, _canonical_json(details or {}, "details"), stamp, stamp),
            )
            row = self.conn.execute("SELECT * FROM unresolved_exceptions WHERE run_id=? AND fingerprint=?", (run_id, fingerprint)).fetchone()
            self.event(run_id, "exception", str(row["exception_id"]), "exception_unresolved", {"fingerprint": fingerprint, "category": category})
            return dict(row)

    def resolve_exception(self, run_id, fingerprint, evidence):
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("resolution evidence is required")
        with transaction(self.conn):
            row = self.conn.execute("SELECT * FROM unresolved_exceptions WHERE run_id=? AND fingerprint=?", (run_id, fingerprint)).fetchone()
            if not row:
                raise ValueError("unknown exception")
            self.conn.execute("UPDATE unresolved_exceptions SET resolved=1,last_seen=?,details=? WHERE exception_id=?", (now(), _canonical_json({"resolution_evidence": evidence}, "details"), row["exception_id"]))
            self.event(run_id, "exception", str(row["exception_id"]), "exception_resolved", {"fingerprint": fingerprint, "evidence": evidence})
            return dict(self.conn.execute("SELECT * FROM unresolved_exceptions WHERE exception_id=?", (row["exception_id"],)).fetchone())

    def unresolved_exceptions(self, run_id):
        rows = self.conn.execute("SELECT * FROM unresolved_exceptions WHERE run_id=? AND resolved=0 ORDER BY exception_id", (run_id,)).fetchall()
        return [dict(row) for row in rows]

    def record_external_wait(self, external_request_id, run_id, event_cursor, wake_condition, next_safe_check_at, action_id=None):
        if not external_request_id or not wake_condition or not next_safe_check_at:
            raise ValueError("external wait requires request id, wake condition and next safe check time")
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO external_waits(external_request_id,run_id,action_id,event_cursor,wake_condition,next_safe_check_at,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?, 'waiting',?,?) ON CONFLICT(external_request_id) DO UPDATE SET event_cursor=excluded.event_cursor, "
                "wake_condition=excluded.wake_condition,next_safe_check_at=excluded.next_safe_check_at,status='waiting',updated_at=excluded.updated_at",
                (external_request_id, run_id, action_id, event_cursor, wake_condition, next_safe_check_at, now(), now()),
            )
            return dict(self.conn.execute("SELECT * FROM external_waits WHERE external_request_id=?", (external_request_id,)).fetchone())

    def poll_external_wait(self, external_request_id, result, changed, evidence=None):
        with transaction(self.conn):
            row = self.conn.execute("SELECT * FROM external_waits WHERE external_request_id=?", (external_request_id,)).fetchone()
            if not row:
                raise ValueError("unknown external request")
            digest = hashlib.sha256(_canonical_json(result, "poll result").encode()).hexdigest()
            changed = bool(changed) and digest != row["last_result_digest"] and row["status"] == "waiting"
            status = "ready" if changed else row["status"]
            self.conn.execute("UPDATE external_waits SET status=?,last_result_digest=?,poll_count=poll_count+1,updated_at=? WHERE external_request_id=?", (status, digest, now(), external_request_id))
            if changed:
                self.event(row["run_id"], "external_wait", external_request_id, "external_wait_changed", {"evidence": evidence or [], "result_digest": digest})
            return {"external_request_id": external_request_id, "changed": bool(changed), "semantic_round": bool(changed), "status": status, "result_digest": digest, "evidence": evidence or []}

    def record_terminal_validation(self, run_id, decision, evidence):
        if decision != "allow":
            raise ValueError("terminal validation decision must be allow")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("terminal validation evidence is required")
        with transaction(self.conn):
            cursor = self.event_cursor(run_id)
            self.conn.execute(
                "INSERT INTO terminal_validations(run_id,state_version,decision,evidence,validated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET state_version=excluded.state_version,decision=excluded.decision, "
                "evidence=excluded.evidence,validated_at=excluded.validated_at",
                (run_id, cursor, decision, _canonical_json(evidence, "evidence"), now()),
            )
            self.event(run_id, "run", run_id, "terminal_validation_recorded", {"decision": decision, "evidence": evidence})
            cursor = self.event_cursor(run_id)
            self.conn.execute("UPDATE terminal_validations SET state_version=? WHERE run_id=?", (cursor, run_id))
            return {"run_id": run_id, "decision": decision, "state_version": cursor, "evidence": evidence}

    def terminal_validation_satisfied(self, run_id):
        row = self.conn.execute("SELECT * FROM terminal_validations WHERE run_id=?", (run_id,)).fetchone()
        return bool(row and row["decision"] == "allow" and row["state_version"] == self.event_cursor(run_id))
