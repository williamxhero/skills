"""SQLite state store for the Implement Needs controller."""
from __future__ import annotations
import json, sqlite3
from datetime import datetime, timezone
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
"""

def now() -> str: return datetime.now(timezone.utc).isoformat()


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
            with self.conn: self.conn.execute("UPDATE actions SET status=?,result=?,error=?,attempts=attempts+1,updated_at=? WHERE action_id=?",(status,json.dumps(result,ensure_ascii=False) if result is not None else None,error,now(),action_id))
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
        with self.conn:
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
        with self.conn:
            self.conn.execute("UPDATE tickets SET status=?,commits=COALESCE(?,commits),tests=COALESCE(?,tests) WHERE ticket_id=?",(status,json.dumps(commits) if commits is not None else None,json.dumps(tests) if tests is not None else None,ticket_id)); self.event(row[1],"ticket",ticket_id,"ticket_state_changed",{"status":status,"commits":commits,"tests":tests})
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
