"""SQLite state store for the Implement Needs controller."""
from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from evidence_gate import EvidenceGateError, verify_terminal_contract
from authorization import AuthorizationError, authorization_digest, check_scope, validate_authorization
from sync_scope import validate_sync_readback
from startup_contract import StartupContractError, validate_startup_contract
from run_state import (
    PHASE_TRANSITIONS,
    RunStateError,
    validate_phase_receipt,
    validate_result_receipt,
)

# Bump when the threads identity columns change; migrations stay additive so an
# existing run database keeps every audit record it already holds.
SCHEMA_VERSION = 9

# Identity columns added by schema version 2. The legacy ``thread_id`` column is
# retained and reinterpreted as the current backend thread id.
THREAD_IDENTITY_COLUMNS = (
    ("task_id", "TEXT"),
    ("attempt_id", "TEXT"),
    ("nonce", "TEXT"),
    ("client_thread_id", "TEXT"),
    ("formal_thread_id", "TEXT"),
    ("host_id", "TEXT"),
    ("owner_id", "TEXT"),
    ("cwd", "TEXT"),
    ("project_id", "TEXT"),
    ("title_token", "TEXT"),
    ("identity_readback", "TEXT"),
    ("route_readback", "TEXT"),
    ("route_receipt", "TEXT"),
)

RUN_MODE_COLUMNS = (
    ("execution_mode", "TEXT NOT NULL DEFAULT 'whole-spec'"),
    ("controller_task_id", "TEXT"),
    ("queue_definition", "TEXT NOT NULL DEFAULT '[]'"),
)

RUN_STATE_COLUMNS = (
    ("run_phase", "TEXT NOT NULL DEFAULT 'initialized'"),
    ("terminal_result", "TEXT"),
    ("stop_reason", "TEXT"),
    ("recovery_action", "TEXT"),
)

TICKET_QUEUE_COLUMNS = (
    ("queue_position", "INTEGER"),
    ("acceptance", "TEXT NOT NULL DEFAULT '[]'"),
)

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, initiative TEXT NOT NULL, requirement TEXT NOT NULL, status TEXT NOT NULL, current_action TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, execution_mode TEXT NOT NULL DEFAULT 'whole-spec', controller_task_id TEXT, queue_definition TEXT NOT NULL DEFAULT '[]', business_version INTEGER NOT NULL DEFAULT 0, run_phase TEXT NOT NULL DEFAULT 'initialized', terminal_result TEXT, stop_reason TEXT, recovery_action TEXT);
CREATE TABLE IF NOT EXISTS specs(spec_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), title TEXT NOT NULL, status TEXT NOT NULL, position INTEGER NOT NULL, blocked_by TEXT NOT NULL DEFAULT '[]', acceptance TEXT NOT NULL DEFAULT '[]', generation INTEGER NOT NULL DEFAULT 0, UNIQUE(run_id,position));
CREATE TABLE IF NOT EXISTS tickets(ticket_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL REFERENCES specs(spec_id), title TEXT NOT NULL, status TEXT NOT NULL, blocked_by TEXT NOT NULL DEFAULT '[]', commits TEXT NOT NULL DEFAULT '[]', tests TEXT NOT NULL DEFAULT '[]', acceptance TEXT NOT NULL DEFAULT '[]', issue_url TEXT, queue_position INTEGER, UNIQUE(spec_id,title));
CREATE TABLE IF NOT EXISTS threads(thread_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), kind TEXT NOT NULL, spec_id TEXT REFERENCES specs(spec_id), lifecycle TEXT NOT NULL, outcome TEXT NOT NULL DEFAULT 'unknown', next_action TEXT, last_observed_at TEXT NOT NULL, archive_operation_evidence TEXT NOT NULL DEFAULT '[]', archive_readback_evidence TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS actions(action_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), kind TEXT NOT NULL, target TEXT NOT NULL, status TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, attempts INTEGER NOT NULL DEFAULT 0, result TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(event_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL, observed_at TEXT NOT NULL, business_version INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS observations(observation_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, payload TEXT NOT NULL, observed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence_refs(evidence_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, evidence_kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS phase_receipts(receipt_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), from_phase TEXT NOT NULL, to_phase TEXT NOT NULL, result TEXT NOT NULL, payload TEXT NOT NULL, business_version INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS run_authorizations(run_id TEXT PRIMARY KEY REFERENCES runs(run_id), payload TEXT NOT NULL, authorization_digest TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidate_freezes(freeze_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), candidate_sha TEXT NOT NULL, merge_sha TEXT, authorization_digest TEXT NOT NULL, status TEXT NOT NULL, invalidation_reason TEXT, payload TEXT NOT NULL, business_version INTEGER NOT NULL, created_at TEXT NOT NULL, invalidated_at TEXT);
CREATE TABLE IF NOT EXISTS candidate_evidence(evidence_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), freeze_id INTEGER NOT NULL REFERENCES candidate_freezes(freeze_id), evidence_kind TEXT NOT NULL, candidate_sha TEXT NOT NULL, authorization_digest TEXT NOT NULL, payload TEXT NOT NULL, business_version INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS startup_contracts(run_id TEXT PRIMARY KEY REFERENCES runs(run_id), payload TEXT NOT NULL, status TEXT NOT NULL, contract_digest TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions(decision_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), subject TEXT NOT NULL, selected TEXT NOT NULL, recommendation TEXT, evidence TEXT NOT NULL DEFAULT '[]', rationale TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schema_meta(version INTEGER NOT NULL, migrated_at TEXT NOT NULL);
"""

# A live attempt identity is unique per run. The registry ``run_id`` column is the
# identity run. Rows without a task identity (legacy or pre-bootstrap) are exempt
# so migration never invents identity values.
UNIQUE_ATTEMPT_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS threads_attempt_identity"
    " ON threads(run_id, task_id, attempt_id)"
    " WHERE task_id IS NOT NULL;"
)

def now() -> str: return datetime.now(timezone.utc).isoformat()


class ActionConflict(RuntimeError):
    """A second executable action would violate the run's single-action gate."""


class StaleState(RuntimeError):
    """The caller attempted to write using an obsolete business version."""

    code = "stale_state"

    def __init__(self, run_id: str, expected: int, actual: int):
        self.run_id, self.expected, self.actual = run_id, expected, actual
        super().__init__(f"stale_state: run {run_id} expected version {expected}, current version {actual}")


class DatabaseModeError(ValueError):
    """The requested database mode is incompatible with the path or schema."""


_EVIDENCE_URI = re.compile(r"^[a-z][a-z0-9+.-]*:(?:/{0,2})\S+$", re.IGNORECASE)
_COMMIT_SCHEMES = frozenset(("commit", "git", "https", "pr", "sha"))
_TEST_SCHEMES = frozenset(("check", "ci", "https", "pytest", "test"))
_READBACK_SCHEMES = frozenset(("github", "https"))
_ACCEPTANCE_SCHEMES = frozenset(("acceptance", "file", "github", "https"))


def _valid_evidence(value: object, schemes: frozenset[str]) -> bool:
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if not isinstance(item, str) or not item.strip() or not _EVIDENCE_URI.match(item.strip()):
            return False
        if item.split(":", 1)[0].lower() not in schemes:
            return False
    return True

class ControlDB:
    MODES = frozenset(("create", "open-existing", "read-only"))

    def __init__(self, path: str | Path, mode: str = "create"):
        if mode not in self.MODES:
            raise DatabaseModeError(f"unsupported database mode: {mode}")
        self.path=Path(path); self.mode=mode
        if mode == "create":
            self.path.parent.mkdir(parents=True,exist_ok=True)
            self.conn=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        else:
            if not self.path.exists():
                raise FileNotFoundError(self.path)
            if not self.path.is_file():
                raise DatabaseModeError(f"database path is not a file: {self.path}")
            if mode == "read-only":
                # immutable=1 prevents SQLite from creating WAL/SHM sidecars for
                # a diagnostic reader.  The controller only uses this mode for a
                # closed, local database snapshot, never for a live writer.
                uri = self.path.resolve().as_uri() + "?mode=ro&immutable=1"
                self.conn=sqlite3.connect(uri,uri=True,timeout=30,isolation_level=None)
            else:
                self.conn=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        self.conn.row_factory=sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        if mode == "create":
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
        else:
            self._require_existing_schema()
        try:
            if mode != "read-only":
                self.migrate()
            self._validate_schema_integrity(read_only=(mode == "read-only"))
        except Exception:
            # Release the handle before propagating: a rejected database must not
            # stay locked, and callers never receive the object to close.
            self.conn.close()
            raise
    def close(self): self.conn.close()

    @classmethod
    def open_existing(cls, path: str | Path) -> "ControlDB":
        return cls(path, mode="open-existing")

    @classmethod
    def read_only(cls, path: str | Path) -> "ControlDB":
        return cls(path, mode="read-only")

    @contextmanager
    def transaction(self):
        """Run a business mutation in an explicit SQLite transaction.

        SQLite connections in this module are autocommit connections.  Relying on
        ``with conn`` therefore does not provide a rollback boundary for a later
        write.  Nested callers share the outer transaction and only the owner
        commits or rolls back.
        """
        if self.mode == "read-only":
            raise sqlite3.OperationalError("read-only database")
        owner = not self.conn.in_transaction
        if owner:
            self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self
        except BaseException:
            if owner and self.conn.in_transaction:
                self.conn.rollback()
            raise
        else:
            if owner and self.conn.in_transaction:
                self.conn.commit()

    def _require_existing_schema(self) -> None:
        # These are the legacy tables required before an additive migration can
        # run.  New tables are checked after migration by _validate_schema_integrity.
        required = {"runs", "specs", "tickets", "threads", "actions", "events", "decisions", "schema_meta"}
        found = {row[0] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = sorted(required - found)
        if missing:
            self.conn.close()
            raise DatabaseModeError("existing database schema is incomplete: " + ", ".join(missing))

    def _validate_schema_integrity(self, *, read_only: bool) -> None:
        row = self.conn.execute("SELECT version FROM schema_meta ORDER BY rowid DESC LIMIT 1").fetchone()
        if row is None or row[0] != SCHEMA_VERSION:
            raise DatabaseModeError(f"database schema must be version {SCHEMA_VERSION}")
        run_columns = {item[1] for item in self.conn.execute("PRAGMA table_info(runs)")}
        event_columns = {item[1] for item in self.conn.execute("PRAGMA table_info(events)")}
        if "business_version" not in run_columns or "business_version" not in event_columns:
            raise DatabaseModeError("database schema lacks business-version columns")
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='observations'").fetchone():
            raise DatabaseModeError("database schema lacks observations table")
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_refs'").fetchone():
            raise DatabaseModeError("database schema lacks evidence_refs table")
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='phase_receipts'").fetchone():
            raise DatabaseModeError("database schema lacks phase_receipts table")
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='run_authorizations'").fetchone():
            raise DatabaseModeError("database schema lacks run_authorizations table")
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='candidate_freezes'").fetchone():
            raise DatabaseModeError("database schema lacks candidate_freezes table")
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='candidate_evidence'").fetchone():
            raise DatabaseModeError("database schema lacks candidate_evidence table")
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='startup_contracts'").fetchone():
            raise DatabaseModeError("database schema lacks startup_contracts table")
        run_columns = {item[1] for item in self.conn.execute("PRAGMA table_info(runs)")}
        if not {"run_phase", "terminal_result", "stop_reason", "recovery_action"}.issubset(run_columns):
            raise DatabaseModeError("database schema lacks run lifecycle columns")
        for run in self.conn.execute("SELECT run_id,business_version FROM runs"):
            latest = self.conn.execute("SELECT COALESCE(MAX(business_version),0) FROM events WHERE run_id=?", (run[0],)).fetchone()[0]
            if int(run[1]) != int(latest):
                raise DatabaseModeError(f"business event continuity failed for run {run[0]}")
        if read_only:
            # The read-only branch must never repair a malformed store.  The
            # caller gets a deterministic error instead of a hidden migration.
            return
    def migrate(self):
        """Add identity columns in place and record the schema version.

        Migration is additive and never backfills identity: an existing row keeps
        its audit columns and stays untracked until a formal readback binds it.
        """
        existing={row[1] for row in self.conn.execute("PRAGMA table_info(threads)")}
        current=self.conn.execute("SELECT version FROM schema_meta ORDER BY rowid DESC LIMIT 1").fetchone()
        # Refuse a database written by newer code before the up-to-date early
        # return, otherwise the guard below it is unreachable.
        if current is not None and current[0] > SCHEMA_VERSION:
            raise ValueError(f"database schema {current[0]} is newer than {SCHEMA_VERSION}")
        if current is not None and current[0] >= SCHEMA_VERSION:
            return
        with self.transaction():
            for name,declaration in THREAD_IDENTITY_COLUMNS:
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE threads ADD COLUMN {name} {declaration}")
            if "run_identity" in existing:
                self.conn.execute("ALTER TABLE threads DROP COLUMN run_identity")
            self.conn.executescript(UNIQUE_ATTEMPT_INDEX)
            run_columns={row[1] for row in self.conn.execute("PRAGMA table_info(runs)")}
            for name,declaration in RUN_MODE_COLUMNS:
                if name not in run_columns:
                    self.conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {declaration}")
            run_columns={row[1] for row in self.conn.execute("PRAGMA table_info(runs)")}
            for name,declaration in RUN_STATE_COLUMNS:
                if name not in run_columns:
                    self.conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {declaration}")
            ticket_columns={row[1] for row in self.conn.execute("PRAGMA table_info(tickets)")}
            for name,declaration in TICKET_QUEUE_COLUMNS:
                if name not in ticket_columns:
                    self.conn.execute(f"ALTER TABLE tickets ADD COLUMN {name} {declaration}")
            run_columns={row[1] for row in self.conn.execute("PRAGMA table_info(runs)")}
            if "business_version" not in run_columns:
                self.conn.execute("ALTER TABLE runs ADD COLUMN business_version INTEGER NOT NULL DEFAULT 0")
            event_columns={row[1] for row in self.conn.execute("PRAGMA table_info(events)")}
            if "business_version" not in event_columns:
                self.conn.execute("ALTER TABLE events ADD COLUMN business_version INTEGER NOT NULL DEFAULT 0")
            self.conn.execute("CREATE TABLE IF NOT EXISTS observations(observation_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, payload TEXT NOT NULL, observed_at TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS evidence_refs(evidence_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, evidence_kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS phase_receipts(receipt_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), from_phase TEXT NOT NULL, to_phase TEXT NOT NULL, result TEXT NOT NULL, payload TEXT NOT NULL, business_version INTEGER NOT NULL, created_at TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS run_authorizations(run_id TEXT PRIMARY KEY REFERENCES runs(run_id), payload TEXT NOT NULL, authorization_digest TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS candidate_freezes(freeze_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), candidate_sha TEXT NOT NULL, merge_sha TEXT, authorization_digest TEXT NOT NULL, status TEXT NOT NULL, invalidation_reason TEXT, payload TEXT NOT NULL, business_version INTEGER NOT NULL, created_at TEXT NOT NULL, invalidated_at TEXT)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS candidate_evidence(evidence_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), freeze_id INTEGER NOT NULL REFERENCES candidate_freezes(freeze_id), evidence_kind TEXT NOT NULL, candidate_sha TEXT NOT NULL, authorization_digest TEXT NOT NULL, payload TEXT NOT NULL, business_version INTEGER NOT NULL, created_at TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS startup_contracts(run_id TEXT PRIMARY KEY REFERENCES runs(run_id), payload TEXT NOT NULL, status TEXT NOT NULL, contract_digest TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            self.conn.execute(
                "INSERT OR IGNORE INTO run_authorizations(run_id,payload,authorization_digest,status,created_at,updated_at) "
                "SELECT run_id, ?, NULL, 'unconfigured', created_at, updated_at FROM runs",
                (json.dumps({"status": "unconfigured"}, ensure_ascii=False, sort_keys=True),),
            )
            self.conn.execute("INSERT INTO schema_meta(version,migrated_at) VALUES(?,?)",(SCHEMA_VERSION,now()))
    def business_version(self, run_id: str) -> int:
        row = self.conn.execute("SELECT business_version FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("run not found")
        return int(row[0])

    def _check_version(self, run_id: str, expected_version: int | None) -> int:
        actual = self.business_version(run_id)
        if expected_version is not None and expected_version != actual:
            raise StaleState(run_id, expected_version, actual)
        return actual

    def _business_event(self,run_id,entity_type,entity_id,event_type,payload):
        """Append an event and advance the run version inside the caller's tx."""
        current = self.business_version(run_id)
        next_version = current + 1
        self.conn.execute("UPDATE runs SET business_version=?,updated_at=? WHERE run_id=?",(next_version,now(),run_id))
        self.conn.execute("INSERT INTO events(run_id,entity_type,entity_id,event_type,payload,observed_at,business_version) VALUES(?,?,?,?,?,?,?)",(run_id,entity_type,entity_id,event_type,json.dumps(payload,ensure_ascii=False,sort_keys=True),now(),next_version))
        return next_version

    def _record_verified_gate(self, run_id, entity_type, entity_id, gate):
        self.conn.execute("INSERT INTO evidence_refs(run_id,entity_type,entity_id,evidence_kind,payload,created_at) VALUES(?,?,?,?,?,?)",(run_id,entity_type,entity_id,"verified_transition_gate",json.dumps(gate,ensure_ascii=False,sort_keys=True),now()))

    def event(self,run_id,entity_type,entity_id,event_type,payload,expected_version=None):
        """Public compatibility wrapper for one atomic business event."""
        with self.transaction():
            self._check_version(run_id, expected_version)
            return self._business_event(run_id,entity_type,entity_id,event_type,payload)
    def create_run(self,run_id,initiative,requirement,execution_mode="whole-spec",controller_task_id=None,queue_definition=None,authorization=None):
        if execution_mode not in {"whole-spec", "single-ticket-line"}:
            raise ValueError("unsupported execution mode")
        if execution_mode == "single-ticket-line" and not controller_task_id:
            raise ValueError("single-ticket-line requires controller_task_id")
        if (str(initiative).strip() in {"#444", "444"} or "#444" in str(requirement)) and execution_mode == "single-ticket-line" and controller_task_id != "codex://threads/01a09a58-dfcd-72b0-a5d7-c359eefce9a2":
            raise ValueError("issue #444 has one designated controller task")
        auth = validate_authorization(authorization) if authorization is not None else None
        stamp=now()
        with self.transaction():
            self.conn.execute("INSERT INTO runs(run_id,initiative,requirement,status,current_action,created_at,updated_at,execution_mode,controller_task_id,queue_definition,business_version,run_phase,terminal_result,stop_reason,recovery_action) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(run_id,initiative,requirement,"active",None,stamp,stamp,execution_mode,controller_task_id,json.dumps(queue_definition or [],ensure_ascii=False),0,"initialized",None,None,None))
            payload = auth if auth is not None else {"status": "unconfigured"}
            self.conn.execute(
                "INSERT INTO run_authorizations(run_id,payload,authorization_digest,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (run_id, json.dumps(payload, ensure_ascii=False, sort_keys=True), authorization_digest(auth) if auth is not None else None, "configured" if auth is not None else "unconfigured", stamp, stamp),
            )
            self._business_event(run_id,"run",run_id,"run_created",{"execution_mode":execution_mode,"controller_task_id":controller_task_id,"run_phase":"initialized","authorization_status":"configured" if auth is not None else "unconfigured"})

    def authorization(self, run_id):
        row = self.conn.execute("SELECT payload,status,authorization_digest FROM run_authorizations WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise AuthorizationError("authorization_missing", {"run_id": run_id})
        return {"payload": json.loads(row[0]), "status": row[1], "authorization_digest": row[2]}

    def configure_authorization(self, run_id, authorization, expected_version=None):
        auth = validate_authorization(authorization)
        digest = authorization_digest(auth)
        with self.transaction():
            self._check_version(run_id, expected_version)
            row = self.conn.execute("SELECT status,authorization_digest FROM run_authorizations WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise AuthorizationError("authorization_missing", {"run_id": run_id})
            if row[0] == "configured":
                if row[1] == digest:
                    return {"run_id": run_id, "changed": False, "authorization_digest": digest}
                raise AuthorizationError("authorization_immutable", {"run_id": run_id})
            self.conn.execute(
                "UPDATE run_authorizations SET payload=?,authorization_digest=?,status='configured',updated_at=? WHERE run_id=?",
                (json.dumps(auth, ensure_ascii=False, sort_keys=True), digest, now(), run_id),
            )
            version = self._business_event(run_id, "run", run_id, "run_authorization_configured", {"authorization_digest": digest})
            return {"run_id": run_id, "changed": True, "authorization_digest": digest, "business_version": version}

    def authorize(self, run_id, **kwargs):
        record = self.authorization(run_id)
        if record["status"] != "configured":
            raise AuthorizationError("authorization_unconfigured", {"run_id": run_id})
        decision = check_scope(record["payload"], **kwargs)
        if decision["authorization_digest"] != record["authorization_digest"]:
            raise AuthorizationError("authorization_digest_mismatch", {"run_id": run_id})
        return decision

    def startup_contract(self, run_id):
        row = self.conn.execute(
            "SELECT payload,status,contract_digest FROM startup_contracts WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise StartupContractError("startup_contract_missing", {"run_id": run_id})
        return {
            "payload": json.loads(row[0]),
            "status": row[1],
            "contract_digest": row[2],
        }

    def record_startup_contract(self, run_id, contract, available_dependencies=None, expected_version=None):
        validated = validate_startup_contract(
            contract,
            run_id=run_id,
            available_dependencies=available_dependencies,
        )
        canonical = json.dumps(validated, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self.transaction():
            current = self._check_version(run_id, expected_version)
            row = self.conn.execute(
                "SELECT status,contract_digest FROM startup_contracts WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row is not None and row[0] == "verified":
                if row[1] == digest:
                    return {
                        "run_id": run_id,
                        "changed": False,
                        "status": "verified",
                        "contract_digest": digest,
                        "business_version": current,
                    }
                raise StartupContractError("startup_contract_immutable", {"run_id": run_id})
            stamp = now()
            self.conn.execute(
                "INSERT INTO startup_contracts(run_id,payload,status,contract_digest,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET payload=excluded.payload,status=excluded.status,contract_digest=excluded.contract_digest,updated_at=excluded.updated_at",
                (run_id, canonical, "verified", digest, stamp, stamp),
            )
            version = self._business_event(
                run_id,
                "run",
                run_id,
                "startup_contract_recorded",
                {"contract_digest": digest, "status": "verified"},
            )
            return {
                "run_id": run_id,
                "changed": True,
                "status": "verified",
                "contract_digest": digest,
                "business_version": version,
            }

    def _candidate_active(self, run_id):
        row = self.conn.execute(
            "SELECT * FROM candidate_freezes WHERE run_id=? AND status='active' ORDER BY freeze_id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError("no active candidate freeze")
        return row

    @staticmethod
    def _validate_candidate_payload(payload, candidate_sha, authorization_digest, expected_version):
        if not isinstance(payload, dict):
            raise ValueError("candidate evidence must be an object")
        required = {"status", "candidate_sha", "authorization_digest", "business_version", "evidence"}
        missing = sorted(required - set(payload))
        if missing:
            raise ValueError("candidate evidence incomplete: " + ", ".join(missing))
        if payload["status"] != "verified":
            raise ValueError("candidate evidence is not verified")
        if payload["candidate_sha"] != candidate_sha:
            raise ValueError("candidate evidence candidate mismatch")
        if payload["authorization_digest"] != authorization_digest:
            raise ValueError("candidate evidence authorization mismatch")
        if payload["business_version"] != expected_version:
            raise StaleState("candidate", payload["business_version"], expected_version)
        if not isinstance(payload["evidence"], list) or not payload["evidence"]:
            raise ValueError("candidate evidence references are required")

    def freeze_candidate(self, run_id, candidate_sha, evidence, merge_sha=None, expected_version=None):
        if not isinstance(candidate_sha, str) or not candidate_sha.strip():
            raise ValueError("candidate SHA is required")
        with self.transaction():
            current = self._check_version(run_id, expected_version)
            decision = self.authorize(run_id, action="freeze-candidate")
            self._validate_candidate_payload(evidence, candidate_sha, decision["authorization_digest"], current)
            active = self.conn.execute(
                "SELECT freeze_id,candidate_sha,authorization_digest FROM candidate_freezes WHERE run_id=? AND status='active' LIMIT 1",
                (run_id,),
            ).fetchone()
            if active is not None:
                if active[1] == candidate_sha and active[2] == decision["authorization_digest"]:
                    return {"run_id": run_id, "changed": False, "freeze_id": active[0], "candidate_sha": candidate_sha}
                raise ValueError("candidate freeze already active; invalidate it before freezing a new candidate")
            stamp = now()
            cur = self.conn.execute(
                "INSERT INTO candidate_freezes(run_id,candidate_sha,merge_sha,authorization_digest,status,payload,business_version,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (run_id, candidate_sha, merge_sha, decision["authorization_digest"], "active", json.dumps(evidence, ensure_ascii=False, sort_keys=True), current, stamp),
            )
            version = self._business_event(run_id, "candidate", candidate_sha, "candidate_frozen", {"candidate_sha": candidate_sha, "merge_sha": merge_sha, "authorization_digest": decision["authorization_digest"]})
            self.conn.execute("UPDATE candidate_freezes SET business_version=? WHERE freeze_id=?", (version, cur.lastrowid))
            return {"run_id": run_id, "changed": True, "freeze_id": cur.lastrowid, "candidate_sha": candidate_sha, "business_version": version}

    def record_candidate_evidence(self, run_id, evidence_kind, payload, expected_version=None):
        if evidence_kind not in {"test", "package", "deployment", "synchronization", "final-readback"}:
            raise ValueError("unsupported candidate evidence kind")
        with self.transaction():
            current = self._check_version(run_id, expected_version)
            active = self._candidate_active(run_id)
            self._validate_candidate_payload(payload, active["candidate_sha"], active["authorization_digest"], current)
            cur = self.conn.execute(
                "INSERT INTO candidate_evidence(run_id,freeze_id,evidence_kind,candidate_sha,authorization_digest,payload,business_version,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (run_id, active["freeze_id"], evidence_kind, active["candidate_sha"], active["authorization_digest"], json.dumps(payload, ensure_ascii=False, sort_keys=True), current, now()),
            )
            version = self._business_event(run_id, "candidate", active["candidate_sha"], "candidate_evidence_recorded", {"evidence_kind": evidence_kind, "evidence_id": cur.lastrowid, "candidate_sha": active["candidate_sha"]})
            self.conn.execute("UPDATE candidate_evidence SET business_version=? WHERE evidence_id=?", (version, cur.lastrowid))
            return {"run_id": run_id, "evidence_id": cur.lastrowid, "candidate_sha": active["candidate_sha"], "business_version": version}

    def record_synchronization(self, run_id, readback, expected_version=None):
        with self.transaction():
            current = self._check_version(run_id, expected_version)
            active = self._candidate_active(run_id)
            authorization = self.authorization(run_id)
            if authorization["status"] != "configured":
                raise AuthorizationError("authorization_unconfigured", {"run_id": run_id})
            payload = validate_sync_readback(
                readback,
                candidate_sha=active["candidate_sha"],
                target_ref=validate_authorization(authorization["payload"])["target_ref"],
                authorization_digest_value=active["authorization_digest"],
                repository_id=validate_authorization(authorization["payload"])["repository"]["id"],
                full_project=bool(readback.get("full_project")),
            )
            payload = dict(payload)
            payload["business_version"] = current
            action = "full-project-sync" if payload["full_project"] else "run-scoped-sync"
            self.authorize(run_id, action=action, full_project=payload["full_project"])
            return self.record_candidate_evidence(run_id, "synchronization", payload, current)

    def invalidate_candidate(self, run_id, reason, observed_candidate_sha=None, expected_version=None):
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("candidate invalidation reason is required")
        with self.transaction():
            self._check_version(run_id, expected_version)
            self.authorize(run_id, action="invalidate-candidate")
            active = self._candidate_active(run_id)
            self.conn.execute(
                "UPDATE candidate_freezes SET status='invalidated',invalidation_reason=?,invalidated_at=? WHERE freeze_id=?",
                (reason, now(), active["freeze_id"]),
            )
            version = self._business_event(run_id, "candidate", active["candidate_sha"], "candidate_invalidated", {"candidate_sha": active["candidate_sha"], "observed_candidate_sha": observed_candidate_sha, "reason": reason})
            return {"run_id": run_id, "candidate_sha": active["candidate_sha"], "observed_candidate_sha": observed_candidate_sha, "business_version": version}

    def candidate_snapshot(self, run_id):
        return {
            "freeze": [dict(row) for row in self.conn.execute("SELECT * FROM candidate_freezes WHERE run_id=? ORDER BY freeze_id", (run_id,))],
            "evidence": [dict(row) for row in self.conn.execute("SELECT * FROM candidate_evidence WHERE run_id=? ORDER BY evidence_id", (run_id,))],
        }

    def advance_run_phase(self, run_id, target_phase, receipt, expected_version=None):
        """Advance exactly one run phase after a bound phase readback."""
        with self.transaction():
            current_version = self._check_version(run_id, expected_version)
            row = self.conn.execute(
                "SELECT run_phase,terminal_result FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise ValueError("run not found")
            if row[1] is not None:
                raise RunStateError("run_terminal", {"result": row[1], "run_phase": row[0]})
            if target_phase not in PHASE_TRANSITIONS.get(row[0], set()):
                raise RunStateError("illegal_run_phase_transition", {"from_phase": row[0], "to_phase": target_phase})
            if target_phase == "implementing":
                spec_count = self.conn.execute("SELECT COUNT(*) FROM specs WHERE run_id=?", (run_id,)).fetchone()[0]
                if spec_count == 0:
                    raise RunStateError("implementation_requires_specs", {"run_id": run_id})
            if target_phase == "final_verification":
                spec_count = self.conn.execute("SELECT COUNT(*) FROM specs WHERE run_id=?", (run_id,)).fetchone()[0]
                open_specs = self.conn.execute(
                    "SELECT COUNT(*) FROM specs WHERE run_id=? AND status NOT IN ('closed','cancelled')", (run_id,)
                ).fetchone()[0]
                if spec_count == 0 or open_specs:
                    raise RunStateError(
                        "final_verification_requires_complete_specs",
                        {"spec_count": spec_count, "open_spec_count": open_specs},
                    )
            validate_phase_receipt(
                receipt, run_id=run_id, from_phase=row[0], to_phase=target_phase,
                expected_version=current_version,
            )
            status = "completed" if target_phase == "completed" else "active"
            terminal = "completed" if target_phase == "completed" else None
            self.conn.execute(
                "UPDATE runs SET run_phase=?,status=?,terminal_result=?,stop_reason=NULL,recovery_action=NULL,current_action=NULL,updated_at=? WHERE run_id=?",
                (target_phase, status, terminal, now(), run_id),
            )
            next_version = self._business_event(
                run_id, "run", run_id, "run_phase_changed",
                {"from_phase": row[0], "to_phase": target_phase, "receipt": receipt},
            )
            self.conn.execute(
                "INSERT INTO phase_receipts(run_id,from_phase,to_phase,result,payload,business_version,created_at) VALUES(?,?,?,?,?,?,?)",
                (run_id, row[0], target_phase, "completed", json.dumps(receipt, ensure_ascii=False, sort_keys=True), next_version, now()),
            )
            return {"run_id": run_id, "from_phase": row[0], "run_phase": target_phase, "business_version": next_version}

    def set_run_result(self, run_id, result, reason, receipt, expected_version=None):
        """Persist blocked, user-stopped, or verified no-change without fake success."""
        if result not in {"blocked", "user_stopped", "no_change"}:
            raise RunStateError("unsupported_run_result", {"result": result})
        with self.transaction():
            current_version = self._check_version(run_id, expected_version)
            row = self.conn.execute(
                "SELECT run_phase,terminal_result FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise ValueError("run not found")
            if row[1] is not None:
                raise RunStateError("run_terminal", {"result": row[1], "run_phase": row[0]})
            if result == "no_change":
                if row[0] != "planning":
                    raise RunStateError("no_change_requires_planning", {"run_phase": row[0]})
                if self.conn.execute(
                    "SELECT COUNT(*) FROM specs WHERE run_id=?", (run_id,)
                ).fetchone()[0] != 0:
                    raise RunStateError("no_change_requires_empty_specs", {"run_id": run_id})
            validate_result_receipt(
                receipt, run_id=run_id, phase=row[0], result=result,
                expected_version=current_version,
            )
            if reason != receipt["reason"]:
                raise RunStateError("result_reason_mismatch", {"expected": receipt["reason"], "actual": reason})
            recovery = receipt.get("recovery_action")
            self.conn.execute(
                "UPDATE runs SET status=?,terminal_result=?,stop_reason=?,recovery_action=?,current_action=NULL,updated_at=? WHERE run_id=?",
                (result, result, reason, recovery, now(), run_id),
            )
            next_version = self._business_event(
                run_id, "run", run_id, "run_result_recorded",
                {"result": result, "reason": reason, "recovery_action": recovery, "receipt": receipt},
            )
            self.conn.execute(
                "INSERT INTO phase_receipts(run_id,from_phase,to_phase,result,payload,business_version,created_at) VALUES(?,?,?,?,?,?,?)",
                (run_id, row[0], row[0], result, json.dumps(receipt, ensure_ascii=False, sort_keys=True), next_version, now()),
            )
            return {"run_id": run_id, "run_phase": row[0], "result": result, "business_version": next_version}

    def resume_run(self, run_id, expected_version=None):
        with self.transaction():
            self._check_version(run_id, expected_version)
            row = self.conn.execute("SELECT run_phase,terminal_result FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("run not found")
            if row[1] not in {"blocked", "user_stopped"}:
                raise RunStateError("run_not_resumable", {"result": row[1]})
            self.conn.execute(
                "UPDATE runs SET status='active',terminal_result=NULL,stop_reason=NULL,recovery_action=NULL,updated_at=? WHERE run_id=?",
                (now(), run_id),
            )
            version = self._business_event(run_id, "run", run_id, "run_resumed", {"run_phase": row[0]})
            return {"run_id": run_id, "run_phase": row[0], "business_version": version}
    def migrate_run_to_single_ticket_line(self, run_id, queue_definition, expected_version=None):
        """Convert an existing run without inventing controller identity.

        This is an additive recovery operation for runs created before the global
        ticket-line mode existed. It refuses to race an executable action and never
        changes ticket, thread, or evidence rows. The controller identity remains
        null until a live backend readback proves it.
        """
        if not isinstance(queue_definition, list) or not queue_definition:
            raise ValueError("single-ticket-line queue must be a non-empty list")
        if any(not isinstance(item, str) or not item.strip() for item in queue_definition):
            raise ValueError("single-ticket-line queue entries must be non-empty strings")
        if len(set(queue_definition)) != len(queue_definition):
            raise ValueError("single-ticket-line queue entries must be unique")
        with self.transaction():
            self._check_version(run_id, expected_version)
            row = self.conn.execute(
                "SELECT execution_mode,controller_task_id,queue_definition,current_action "
                "FROM runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError("run not found")
            active = self.conn.execute(
                "SELECT action_id,kind,target FROM actions WHERE run_id=? "
                "AND status IN ('pending','running') ORDER BY action_id LIMIT 1",
                (run_id,),
            ).fetchone()
            if active is not None:
                raise ActionConflict(
                    f"action {active[0]} already owns run {run_id}: {active[1]}:{active[2]}"
                )
            try:
                existing_queue = json.loads(row[2] or "[]")
            except (TypeError, json.JSONDecodeError):
                existing_queue = None
            if row[0] == "single-ticket-line":
                if row[1] is not None:
                    raise ValueError("single-ticket-line controller identity is already bound")
                if existing_queue != queue_definition:
                    raise ValueError("single-ticket-line queue is already defined differently")
                return {"run_id": run_id, "changed": False, "execution_mode": row[0],
                        "controller_task_id": None, "queue_definition": queue_definition,
                        "next_action": "repair_queue"}
            if row[0] != "whole-spec":
                raise ValueError(f"unsupported existing execution mode: {row[0]}")
            stamp = now()
            self.conn.execute(
                "UPDATE runs SET execution_mode='single-ticket-line',controller_task_id=NULL,"
                "queue_definition=?,current_action=?,updated_at=? WHERE run_id=?",
                (json.dumps(queue_definition, ensure_ascii=False), f"repair_queue:{run_id}", stamp, run_id),
            )
            self._business_event(run_id, "run", run_id, "run_mode_migrated", {
                "from": row[0], "to": "single-ticket-line", "controller_task_id": None,
                "queue_definition": queue_definition, "previous_current_action": row[3],
                "reason": "single-line recovery requires verified controller identity",
            })
            return {"run_id": run_id, "changed": True, "execution_mode": "single-ticket-line",
                    "controller_task_id": None, "queue_definition": queue_definition,
                    "next_action": "repair_queue"}
    def set_action(self,run_id,kind,target,status="pending",expected_version=None):
        with self.transaction():
            self._check_version(run_id, expected_version)
            key=f"{run_id}:{kind}:{target}"; row=self.conn.execute("SELECT action_id FROM actions WHERE idempotency_key=?",(key,)).fetchone()
            if row: return row[0]
            active=self.conn.execute("SELECT action_id,kind,target FROM actions WHERE run_id=? AND status IN ('pending','running') ORDER BY action_id LIMIT 1",(run_id,)).fetchone()
            if active:
                raise ActionConflict(f"action {active[0]} already owns run {run_id}: {active[1]}:{active[2]}")
            stamp=now()
            cur=self.conn.execute("INSERT INTO actions(run_id,kind,target,status,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(run_id,kind,target,status,key,stamp,stamp))
            self.conn.execute("UPDATE runs SET current_action=?,updated_at=? WHERE run_id=?",(f"{kind}:{target}",stamp,run_id))
            self._business_event(run_id,"action",str(cur.lastrowid),"action_created",{"kind":kind,"target":target,"status":status})
            return cur.lastrowid
    def finish_action(self,action_id,status,result=None,error=None,expected_version=None,gate=None):
        with self.transaction():
            row=self.conn.execute("SELECT run_id FROM actions WHERE action_id=?",(action_id,)).fetchone()
            if row is None: raise ValueError("unknown action")
            run_id=row[0]; self._check_version(run_id, expected_version)
            if status == "succeeded":
                verify_terminal_contract(gate, entity_type="action", run_id=run_id, target_id=str(action_id), expected_version=self.business_version(run_id))
                self._record_verified_gate(run_id, "action", str(action_id), gate)
            self.conn.execute("UPDATE actions SET status=?,result=?,error=?,attempts=attempts+1,updated_at=? WHERE action_id=?",(status,json.dumps(result,ensure_ascii=False) if result is not None else None,error,now(),action_id))
            self._business_event(run_id,"action",str(action_id),"action_finished",{"status":status,"result":result,"error":error})
    def add_spec(self,run_id,spec_id,title,position,blocked_by=None,acceptance=None,expected_version=None):
        with self.transaction():
            self._check_version(run_id, expected_version)
            self.conn.execute("INSERT INTO specs(spec_id,run_id,title,status,position,blocked_by,acceptance) VALUES(?,?,?,?,?,?,?)",(spec_id,run_id,title,"planned",position,json.dumps(blocked_by or []),json.dumps(acceptance or [])))
            self._business_event(run_id,"spec",spec_id,"spec_created",{"title":title})
    def add_ticket(self,spec_id,ticket_id,title,blocked_by=None,issue_url=None,queue_position=None,expected_version=None):
        run_id=self.conn.execute("SELECT run_id FROM specs WHERE spec_id=?",(spec_id,)).fetchone()[0]
        with self.transaction():
            self._check_version(run_id, expected_version)
            if queue_position is None:
                row=self.conn.execute("SELECT COALESCE(MAX(queue_position),0)+1 FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE s.run_id=?",(run_id,)).fetchone()
                queue_position=row[0]
            if self.conn.execute("SELECT 1 FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE s.run_id=? AND t.queue_position=?",(run_id,queue_position)).fetchone():
                raise ValueError(f"queue_position already used in run: {queue_position}")
            self.conn.execute("INSERT INTO tickets(ticket_id,spec_id,title,status,blocked_by,issue_url,queue_position) VALUES(?,?,?,?,?,?,?)",(ticket_id,spec_id,title,"planned",json.dumps(blocked_by or []),issue_url,queue_position))
            self._business_event(run_id,"ticket",ticket_id,"ticket_created",{"spec_id":spec_id,"queue_position":queue_position})
    def import_ticket_ledger(self, run_id, entries, expected_version=None):
        """Import an externally read-back ticket ledger without creating issues.

        The import is intentionally strict: it must cover the declared global
        queue exactly, map every entry to a run-owned spec, and carry structured
        evidence for already-closed tickets. It never changes GitHub state.
        """
        if not isinstance(entries, dict):
            raise TypeError("ticket ledger must be a GitHub readback envelope")
        source = entries.get("source")
        if not isinstance(source, dict) or source.get("kind") != "github":
            raise ValueError("ticket ledger source must be github")
        source_evidence = source.get("evidence", source.get("readback_evidence", []))
        if not _valid_evidence(source_evidence, _READBACK_SCHEMES):
            raise ValueError("ticket ledger requires GitHub readback evidence")
        expected_count = entries.get("expected_ticket_count")
        if not isinstance(expected_count, int) or expected_count < 1:
            raise ValueError("ticket ledger requires expected_ticket_count")
        declared_queue = entries.get("queue")
        if declared_queue is not None and (not isinstance(declared_queue, list) or any(not isinstance(item, str) for item in declared_queue)):
            raise ValueError("ticket ledger queue is invalid")
        entries = entries.get("tickets")
        if not isinstance(entries, list) or not entries:
            raise ValueError("ticket ledger must contain a non-empty tickets list")
        if len(entries) != expected_count:
            raise ValueError("ticket ledger count does not match expected_ticket_count")
        with self.transaction():
            self._check_version(run_id, expected_version)
            run = self.conn.execute(
                "SELECT execution_mode,queue_definition FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise ValueError("run not found")
            if run[0] != "single-ticket-line":
                raise ValueError("ticket ledger requires single-ticket-line run")
            try:
                queue = json.loads(run[1] or "[]")
            except (TypeError, json.JSONDecodeError):
                raise ValueError("run queue definition is invalid") from None
            if not isinstance(queue, list) or len(set(queue)) != len(queue):
                raise ValueError("run queue definition is invalid")
            if len(queue) != expected_count:
                raise ValueError("run queue count does not match expected_ticket_count")
            if declared_queue is not None and declared_queue != queue:
                raise ValueError("ticket ledger queue does not match run queue")
            by_id = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    raise TypeError("ticket ledger entries must be objects")
                ticket_id = entry.get("ticket_id") or entry.get("id")
                if not isinstance(ticket_id, str) or not ticket_id.strip() or ticket_id in by_id:
                    raise ValueError("ticket ledger contains invalid or duplicate ticket_id")
                by_id[ticket_id] = entry
            if set(by_id) != set(queue) or len(by_id) != len(queue):
                raise ValueError("ticket ledger does not exactly match queue_definition")
            spec_rows = {
                row[0]: row[1]
                for row in self.conn.execute(
                    "SELECT spec_id,status FROM specs WHERE run_id=?", (run_id,)
                )
            }
            if not spec_rows:
                raise ValueError("run spec ledger is incomplete")
            if run[0] == "single-ticket-line" and self._is_issue_444_run(run_id) and len(queue) != 33:
                raise ValueError("issue #444 requires exactly 33 queued tickets")
            normalized = []
            allowed = {"planned", "ready", "implementing", "verified", "merged", "closed", "blocked", "cancelled"}
            for position, ticket_id in enumerate(queue, 1):
                entry = by_id[ticket_id]
                spec_id = entry.get("spec_id")
                title = entry.get("title")
                status = entry.get("status", "planned")
                blockers = entry.get("blocked_by", [])
                queue_position = entry.get("queue_position", position)
                commits = entry.get("commits", [])
                tests = entry.get("tests", entry.get("test_evidence", []))
                acceptance = entry.get("acceptance", entry.get("acceptance_evidence", []))
                readback_evidence = entry.get("readback_evidence", entry.get("github_readback_evidence", []))
                if spec_id not in spec_rows or not isinstance(title, str) or not title.strip():
                    raise ValueError(f"ticket ledger entry {ticket_id} has invalid spec/title")
                if queue_position != position or status not in allowed:
                    raise ValueError(f"ticket ledger entry {ticket_id} has invalid position/status")
                if not isinstance(blockers, list) or any(item not in queue for item in blockers):
                    raise ValueError(f"ticket ledger entry {ticket_id} has invalid blockers")
                if ticket_id in blockers:
                    raise ValueError(f"ticket ledger entry {ticket_id} blocks itself")
                if not _valid_evidence(readback_evidence, _READBACK_SCHEMES):
                    raise ValueError(f"ticket ledger entry {ticket_id} lacks GitHub readback evidence")
                if status == "closed":
                    if not _valid_evidence(commits, _COMMIT_SCHEMES):
                        raise ValueError(f"ticket ledger entry {ticket_id} lacks commit evidence")
                    if not _valid_evidence(tests, _TEST_SCHEMES):
                        raise ValueError(f"ticket ledger entry {ticket_id} lacks test evidence")
                    if not _valid_evidence(acceptance, _ACCEPTANCE_SCHEMES):
                        raise ValueError(f"ticket ledger entry {ticket_id} lacks acceptance evidence")
                normalized.append((
                    ticket_id, spec_id, title, status, blockers, commits, tests, acceptance,
                    entry.get("issue_url"), queue_position, readback_evidence,
                ))
            existing = [
                tuple(row)
                for row in self.conn.execute(
                    "SELECT t.ticket_id,t.spec_id,t.title,t.status,t.blocked_by,t.commits,t.tests,t.acceptance,t.issue_url,t.queue_position "
                    "FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE s.run_id=? "
                    "ORDER BY t.queue_position", (run_id,)
                )
            ]
            canonical = [
                (ticket_id, spec_id, title, status, json.dumps(blockers, ensure_ascii=False),
                 json.dumps(commits, ensure_ascii=False), json.dumps(tests, ensure_ascii=False),
                 json.dumps(acceptance, ensure_ascii=False), issue_url, position)
                for ticket_id, spec_id, title, status, blockers, commits, tests, acceptance, issue_url, position, _ in normalized
            ]
            if existing:
                if existing != canonical:
                    raise ValueError("ticket ledger is already defined differently")
                return {"run_id": run_id, "changed": False, "ticket_count": len(canonical)}
            for ticket_id, spec_id, title, status, blockers, commits, tests, acceptance, issue_url, position, _ in normalized:
                self.conn.execute(
                    "INSERT INTO tickets(ticket_id,spec_id,title,status,blocked_by,commits,tests,acceptance,issue_url,queue_position) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (ticket_id, spec_id, title, status, json.dumps(blockers, ensure_ascii=False),
                     json.dumps(commits, ensure_ascii=False), json.dumps(tests, ensure_ascii=False),
                     json.dumps(acceptance, ensure_ascii=False),
                     issue_url, position),
                )
            self._business_event(run_id, "ticket-ledger", run_id, "ticket_ledger_imported", {
                "ticket_count": len(canonical), "source": source, "github_mutation": False,
            })
            return {"run_id": run_id, "changed": True, "ticket_count": len(canonical)}

    def _is_issue_444_run(self, run_id):
        row = self.conn.execute(
            "SELECT initiative,requirement,controller_task_id FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return False
        return (
            str(row[0]).strip() in {"#444", "444"}
            or "#444" in str(row[1])
            or row[2] == "codex://threads/01a09a58-dfcd-72b0-a5d7-c359eefce9a2"
        )

    def ticket_ledger_status(self, run_id):
        """Return the persisted GitHub-backed queue gate without mutating state."""
        run = self.conn.execute(
            "SELECT execution_mode,queue_definition FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if run is None:
            return {"allow": False, "errors": ["run_not_found"]}
        try:
            queue = json.loads(run[1] or "[]")
        except (TypeError, json.JSONDecodeError):
            return {"allow": False, "errors": ["queue_definition_invalid"]}
        errors = []
        if run[0] != "single-ticket-line":
            errors.append("not_single_ticket_line")
        if not isinstance(queue, list) or not queue or len(set(queue)) != len(queue):
            errors.append("queue_definition_invalid")
        rows = self.conn.execute(
            "SELECT t.ticket_id,t.queue_position,t.status,t.commits,t.tests,t.acceptance FROM tickets t JOIN specs s ON s.spec_id=t.spec_id "
            "WHERE s.run_id=? ORDER BY t.queue_position", (run_id,)
        ).fetchall()
        if [row[0] for row in rows] != queue or [row[1] for row in rows] != list(range(1, len(queue) + 1)):
            errors.append("ticket_ledger_incomplete")
        for row in rows:
            if row[2] == "closed" and (
                not _valid_evidence(json.loads(row[3] or "[]"), _COMMIT_SCHEMES)
                or not _valid_evidence(json.loads(row[4] or "[]"), _TEST_SCHEMES)
                or not _valid_evidence(json.loads(row[5] or "[]"), _ACCEPTANCE_SCHEMES)
            ):
                errors.append("closed_ticket_missing_evidence")
        imported = self.conn.execute(
            "SELECT payload FROM events WHERE run_id=? AND entity_type='ticket-ledger' "
            "AND event_type='ticket_ledger_imported' ORDER BY event_id DESC LIMIT 1", (run_id,)
        ).fetchone()
        if imported is None:
            errors.append("ticket_ledger_readback_missing")
        else:
            try:
                receipt = json.loads(imported[0])
            except (TypeError, json.JSONDecodeError):
                receipt = {}
            source = receipt.get("source", {})
            if source.get("kind") != "github" or not _valid_evidence(
                source.get("evidence", source.get("readback_evidence", [])), _READBACK_SCHEMES
            ):
                errors.append("ticket_ledger_github_readback_missing")
            if receipt.get("ticket_count") != len(queue):
                errors.append("ticket_ledger_count_mismatch")
        if self._is_issue_444_run(run_id) and len(queue) != 33:
            errors.append("issue_444_ticket_count_not_33")
        return {"allow": not errors, "errors": sorted(set(errors)), "ticket_count": len(queue) if isinstance(queue, list) else 0}
    def add_thread(self,run_id,thread_id,kind,spec_id=None,identity=None,client_thread_id=None,host_id=None,owner_id=None,cwd=None,project_id=None,title_token=None,formal_thread_id=None,expected_version=None):
        """Register a task attempt, returning True only when a row was inserted.

        A repeat call for the same run, task, and attempt is refused by the unique
        attempt index, so a restarted controller cannot register a second row for
        one attempt. The caller reconciles against the existing row instead.
        """
        fields={"task_id":None,"attempt_id":None,"nonce":None}
        if identity is not None:
            fields.update(identity.as_fields())
        # The registry run column is the identity run. When the identity names a
        # run that this database knows, that value is authoritative; otherwise the
        # thread run is retained so the foreign key stays satisfiable.
        identity_run=fields.get("run_id")
        if identity_run:
            known=self.conn.execute("SELECT 1 FROM runs WHERE run_id=?",(identity_run,)).fetchone()
            if known:
                run_id=identity_run
        with self.transaction():
            self._check_version(run_id, expected_version)
            cur=self.conn.execute(
                "INSERT OR IGNORE INTO threads(thread_id,run_id,kind,spec_id,lifecycle,last_observed_at,task_id,attempt_id,nonce,client_thread_id,formal_thread_id,host_id,owner_id,cwd,project_id,title_token) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (thread_id,run_id,kind,spec_id,"created",now(),fields["task_id"],fields["attempt_id"],fields["nonce"],client_thread_id,formal_thread_id,host_id,owner_id,cwd,project_id,title_token))
            inserted=cur.rowcount==1
            if inserted:
                self._business_event(run_id,"thread",thread_id,"thread_registered",{"kind":kind,"spec_id":spec_id,"task_id":fields["task_id"],"attempt_id":fields["attempt_id"]})
            else:
                self._business_event(run_id,"thread",thread_id,"thread_registration_refused",{"kind":kind,"task_id":fields["task_id"],"attempt_id":fields["attempt_id"]})
        return inserted
    def threads_by_identity(self,run_id,task_id,attempt_id=None):
        """Return registered rows for a task identity, optionally one attempt."""
        sql="SELECT * FROM threads WHERE run_id=? AND task_id=?"
        params=[run_id,task_id]
        if attempt_id is not None:
            sql+=" AND attempt_id=?"; params.append(attempt_id)
        return [dict(row) for row in self.conn.execute(sql+" ORDER BY attempt_id",params)]
    def threads_by_formal_id(self,formal_thread_id,host_id=None):
        sql="SELECT * FROM threads WHERE formal_thread_id=?"
        params=[formal_thread_id]
        if host_id is not None:
            sql+=" AND host_id=?"; params.append(host_id)
        return [dict(row) for row in self.conn.execute(sql,params)]
    def threads_by_client_id(self,client_thread_id):
        return [dict(row) for row in self.conn.execute("SELECT * FROM threads WHERE client_thread_id=?",(client_thread_id,))]
    def threads_by_title_token(self,title_token):
        return [dict(row) for row in self.conn.execute("SELECT * FROM threads WHERE title_token=?",(title_token,))]
    def next_attempt_id(self,run_id,task_id,attempt_id):
        """Return the next attempt only after the current row is terminal."""
        from task_binding import may_advance_attempt
        row=self.conn.execute(
            "SELECT lifecycle,outcome FROM threads WHERE run_id=? AND task_id=? AND attempt_id=?",
            (run_id,task_id,attempt_id),
        ).fetchone()
        if not row:
            raise ValueError("unknown task attempt")
        if not may_advance_attempt(row[0],row[1]):
            raise ValueError("current task attempt is not terminal")
        from task_identity import next_attempt_id
        return next_attempt_id(attempt_id)
    def untracked_threads(self,run_id):
        """Rows retained for audit that carry no task identity."""
        return [dict(row) for row in self.conn.execute("SELECT * FROM threads WHERE run_id=? AND task_id IS NULL",(run_id,))]
    def bind_identity(self,run_id,thread_id,formal_thread_id,host_id,identity_readback=None,expected_version=None):
        """Record the formal backend identity and its readback evidence."""
        with self.transaction():
            self._check_version(run_id, expected_version)
            cur=self.conn.execute("UPDATE threads SET formal_thread_id=?,host_id=?,identity_readback=?,last_observed_at=? WHERE thread_id=? AND run_id=?",(formal_thread_id,host_id,identity_readback,now(),thread_id,run_id))
            if cur.rowcount!=1: raise ValueError("unknown thread")
            self._business_event(run_id,"thread",thread_id,"thread_identity_bound",{"formal_thread_id":formal_thread_id,"host_id":host_id})
    def observe_thread(self,run_id,thread_id,lifecycle=None,outcome=None,next_action=None,readback=None,expected_version=None):
        """Persist a backend observation without inventing a state transition."""
        with self.transaction():
            self._check_version(run_id, expected_version)
            row=self.conn.execute("SELECT lifecycle FROM threads WHERE thread_id=? AND run_id=?",(thread_id,run_id)).fetchone()
            if not row: raise ValueError("unknown thread")
            self.conn.execute(
                "UPDATE threads SET lifecycle=COALESCE(?,lifecycle),outcome=COALESCE(?,outcome),next_action=COALESCE(?,next_action),identity_readback=COALESCE(?,identity_readback),last_observed_at=? WHERE thread_id=? AND run_id=?",
                (lifecycle,outcome,next_action,readback,now(),thread_id,run_id),
            )
            self._business_event(run_id,"thread",thread_id,"backend_reconciled",{"lifecycle":lifecycle,"outcome":outcome,"next_action":next_action})
    def set_route_evidence(self,run_id,thread_id,route_readback=None,route_receipt=None,expected_version=None):
        """Persist post-create route readback and its receipt for this attempt."""
        with self.transaction():
            self._check_version(run_id, expected_version)
            cur=self.conn.execute("UPDATE threads SET route_readback=COALESCE(?,route_readback),route_receipt=COALESCE(?,route_receipt),last_observed_at=? WHERE thread_id=? AND run_id=?",(route_readback,route_receipt,now(),thread_id,run_id))
            if cur.rowcount!=1: raise ValueError("unknown thread")
            self._business_event(run_id,"thread",thread_id,"thread_route_evidence_recorded",{"route_readback":route_readback,"route_receipt":route_receipt})
    def persist_controller_recovery(self, run_id, thread_id, identity, route_readback, next_action, expected_version=None):
        """Atomically cross the controller recovery barrier.

        The caller must already have independently validated the backend record.
        This method makes the final write indivisible: formal identity, lifecycle
        readback, applied route, and the recomputed queue action are committed in
        one SQLite transaction or none are committed.
        """
        if not isinstance(identity, dict):
            raise TypeError("controller identity must be an object")
        required = ("formal_thread_id", "host_id", "task_id", "run_id",
                    "attempt_id", "owner_id", "cwd", "project_id", "lifecycle")
        missing = [field for field in required if not identity.get(field)]
        if missing:
            raise ValueError("controller identity is incomplete: " + ", ".join(missing))
        if not isinstance(route_readback, dict) or not route_readback.get("model") or not route_readback.get("effort"):
            raise ValueError("controller applied route is incomplete")
        if not isinstance(next_action, dict) or not next_action.get("kind") or not next_action.get("target"):
            raise ValueError("controller next action is incomplete")
        ledger = self.ticket_ledger_status(run_id)
        if not ledger["allow"]:
            raise ValueError("controller recovery ledger gate failed: " + ", ".join(ledger["errors"]))
        identity_json = json.dumps(identity, ensure_ascii=False, sort_keys=True)
        route_json = json.dumps(route_readback, ensure_ascii=False, sort_keys=True)
        with self.transaction():
            self._check_version(run_id, expected_version)
            row = self.conn.execute(
                "SELECT task_id,run_id,attempt_id,owner_id,cwd,project_id FROM threads "
                "WHERE thread_id=? AND run_id=?", (thread_id, run_id)
            ).fetchone()
            if row is None:
                raise ValueError("unknown controller thread")
            expected = {
                "task_id": row[0], "run_id": row[1], "attempt_id": row[2],
                "owner_id": row[3], "cwd": row[4], "project_id": row[5],
            }
            mismatched = [field for field, value in expected.items()
                          if value is not None and identity.get(field) != value]
            if mismatched:
                raise ValueError("controller identity disagrees with registry: " + ", ".join(mismatched))
            self.conn.execute(
                "UPDATE threads SET formal_thread_id=?,host_id=?,identity_readback=?,"
                "route_readback=?,last_observed_at=? WHERE thread_id=? AND run_id=?",
                (identity["formal_thread_id"], identity["host_id"], identity_json,
                 route_json, now(), thread_id, run_id),
            )
            self._business_event(run_id, "thread", thread_id, "controller_recovery_committed", {
                "identity": identity, "route": route_readback, "next_action": next_action,
                "gates": ["ticket_ledger", "formal_identity", "applied_route"],
            })
            self._business_event(run_id, "run", run_id, "controller_next_action_recomputed", next_action)
    def add_observation(self,run_id,entity_type,entity_id,observation):
        """Record telemetry on a separate cursor that cannot satisfy a gate."""
        if not isinstance(observation, dict):
            raise TypeError("observation must be an object")
        forbidden = {"receipt", "delivery_proof", "dependency_waiver", "business_evidence"}
        if forbidden & set(observation):
            raise ValueError("business evidence must use a business evidence API")
        with self.transaction():
            self.conn.execute("INSERT INTO observations(run_id,entity_type,entity_id,payload,observed_at) VALUES(?,?,?,?,?)",(run_id,entity_type,entity_id,json.dumps(observation,ensure_ascii=False,sort_keys=True),now()))
    def _record_evidence(self, run_id, entity_type, entity_id, evidence_kind, evidence, expected_version=None):
        if evidence_kind not in {"receipt", "delivery_proof", "dependency_waiver"}:
            raise ValueError("unsupported business evidence kind")
        if evidence is None or evidence == {} or evidence == [] or evidence == "":
            raise ValueError("business evidence must not be empty")
        with self.transaction():
            self._check_version(run_id, expected_version)
            self.conn.execute("INSERT INTO evidence_refs(run_id,entity_type,entity_id,evidence_kind,payload,created_at) VALUES(?,?,?,?,?,?)",(run_id,entity_type,entity_id,evidence_kind,json.dumps(evidence,ensure_ascii=False,sort_keys=True),now()))
            return self._business_event(run_id,entity_type,entity_id,f"{evidence_kind}_recorded",{"evidence_kind":evidence_kind,"evidence":evidence})
    def record_receipt(self, run_id, entity_type, entity_id, receipt, expected_version=None):
        return self._record_evidence(run_id, entity_type, entity_id, "receipt", receipt, expected_version)
    def record_delivery_proof(self, run_id, entity_type, entity_id, proof, expected_version=None):
        return self._record_evidence(run_id, entity_type, entity_id, "delivery_proof", proof, expected_version)
    def record_dependency_waiver(self, run_id, entity_type, entity_id, waiver, expected_version=None):
        return self._record_evidence(run_id, entity_type, entity_id, "dependency_waiver", waiver, expected_version)
    def update_spec(self,spec_id,status,expected_version=None,gate=None):
        from transitions import SPEC_TRANSITIONS, transition
        row=self.conn.execute("SELECT run_id,status FROM specs WHERE spec_id=?",(spec_id,)).fetchone()
        if not row: raise ValueError("unknown spec")
        transition(SPEC_TRANSITIONS,row[1],status)
        with self.transaction():
            self._check_version(row[0], expected_version)
            if status == "closed":
                open_tickets = self.conn.execute("SELECT COUNT(*) FROM tickets WHERE spec_id=? AND status!='closed'", (spec_id,)).fetchone()[0]
                archived = self.conn.execute("SELECT archive_operation_evidence,archive_readback_evidence FROM threads WHERE spec_id=? AND lifecycle='archived'", (spec_id,)).fetchall()
                if open_tickets:
                    raise EvidenceGateError("spec_tickets_incomplete", {"spec_id": spec_id, "open_ticket_count": open_tickets})
                if not archived or any(not json.loads(item[0]) or not json.loads(item[1]) for item in archived):
                    raise EvidenceGateError("spec_archive_evidence_missing", {"spec_id": spec_id})
                verify_terminal_contract(gate, entity_type="spec", run_id=row[0], target_id=spec_id, expected_version=self.business_version(row[0]))
                self._record_verified_gate(row[0], "spec", spec_id, gate)
            self.conn.execute("UPDATE specs SET status=? WHERE spec_id=?",(status,spec_id)); self._business_event(row[0],"spec",spec_id,"spec_state_changed",{"status":status})
    def update_ticket(self,ticket_id,status,commits=None,tests=None,acceptance=None,expected_version=None,gate=None):
        row=self.conn.execute("SELECT t.status,t.commits,t.tests,t.acceptance,s.run_id FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE t.ticket_id=?",(ticket_id,)).fetchone()
        if not row: raise ValueError("unknown ticket")
        allowed={"planned":{"ready","blocked"},"ready":{"implementing","blocked"},"implementing":{"verified","blocked"},"verified":{"merged","blocked"},"merged":{"closed"},"blocked":{"ready","implementing","cancelled"},"closed":set(),"cancelled":set()}
        if status not in allowed.get(row[0],set()): raise ValueError(f"illegal ticket transition: {row[0]} -> {status}")
        try:
            stored_commits = json.loads(row[1] or "[]")
        except (TypeError, json.JSONDecodeError):
            stored_commits = None
        try:
            stored_tests = json.loads(row[2] or "[]")
        except (TypeError, json.JSONDecodeError):
            stored_tests = None
        try:
            stored_acceptance = json.loads(row[3] or "[]")
        except (TypeError, json.JSONDecodeError):
            stored_acceptance = None
        resulting_commits = commits if commits is not None else stored_commits
        resulting_tests = tests if tests is not None else stored_tests
        resulting_acceptance = acceptance if acceptance is not None else stored_acceptance
        if status == "closed":
            if not _valid_evidence(resulting_commits, _COMMIT_SCHEMES):
                raise ValueError("ticket closure requires structured commit evidence")
            if not _valid_evidence(resulting_tests, _TEST_SCHEMES):
                raise ValueError("ticket closure requires structured test evidence")
            if not _valid_evidence(resulting_acceptance, _ACCEPTANCE_SCHEMES):
                raise ValueError("ticket closure requires structured acceptance evidence")
        with self.transaction():
            self._check_version(row[4], expected_version)
            if status == "closed":
                verify_terminal_contract(gate, entity_type="ticket", run_id=row[4], target_id=ticket_id, expected_version=self.business_version(row[4]), commits=resulting_commits, tests=resulting_tests)
                self._record_verified_gate(row[4], "ticket", ticket_id, gate)
            self.conn.execute("UPDATE tickets SET status=?,commits=COALESCE(?,commits),tests=COALESCE(?,tests),acceptance=COALESCE(?,acceptance) WHERE ticket_id=?",(status,json.dumps(commits) if commits is not None else None,json.dumps(tests) if tests is not None else None,json.dumps(acceptance) if acceptance is not None else None,ticket_id)); self._business_event(row[4],"ticket",ticket_id,"ticket_state_changed",{"status":status,"commits":commits,"tests":tests,"acceptance":acceptance})
    def update_thread(self,run_id,thread_id,lifecycle,outcome="unknown",next_action=None,operation=None,readback=None,expected_version=None,gate=None):
        with self.transaction():
            self._check_version(run_id, expected_version)
            row=self.conn.execute("SELECT lifecycle FROM threads WHERE thread_id=? AND run_id=?",(thread_id,run_id)).fetchone()
            if not row: raise ValueError("unknown thread")
            from transitions import THREAD_TRANSITIONS, transition
            transition(THREAD_TRANSITIONS,row[0],lifecycle)
            current=self.conn.execute("SELECT archive_operation_evidence,archive_readback_evidence FROM threads WHERE thread_id=?",(thread_id,)).fetchone()
            operations=json.loads(current[0]); readbacks=json.loads(current[1])
            if operation: operations.append(operation)
            if readback: readbacks.append(readback)
            if lifecycle == "archived":
                if not operation:
                    raise EvidenceGateError("archive_operation_missing", {"target_id": thread_id})
                if not readback:
                    raise EvidenceGateError("archive_readback_missing", {"target_id": thread_id})
                verify_terminal_contract(gate, entity_type="thread", run_id=run_id, target_id=thread_id, expected_version=self.business_version(run_id))
                self._record_verified_gate(run_id, "thread", thread_id, gate)
            self.conn.execute("UPDATE threads SET lifecycle=?,outcome=?,next_action=?,last_observed_at=?,archive_operation_evidence=?,archive_readback_evidence=? WHERE thread_id=? AND run_id=?",(lifecycle,outcome,next_action,now(),json.dumps(operations),json.dumps(readbacks),thread_id,run_id)); self._business_event(run_id,"thread",thread_id,"thread_state_changed",{"lifecycle":lifecycle,"outcome":outcome})
    def decide(self,run_id,subject,selected,recommendation,evidence,rationale,expected_version=None):
        with self.transaction():
            self._check_version(run_id, expected_version)
            self.conn.execute("INSERT INTO decisions(run_id,subject,selected,recommendation,evidence,rationale,created_at) VALUES(?,?,?,?,?,?,?)",(run_id,subject,json.dumps(selected,ensure_ascii=False),json.dumps(recommendation,ensure_ascii=False),json.dumps(evidence,ensure_ascii=False),rationale,now())); self._business_event(run_id,"decision",subject,"controller_approved",{"selected":selected,"recommendation":recommendation,"evidence":evidence,"rationale":rationale})
    def snapshot(self,run_id):
        row=lambda q,p: [dict(x) for x in self.conn.execute(q,p)]
        run=self.conn.execute("SELECT * FROM runs WHERE run_id=?",(run_id,)).fetchone()
        startup = None
        if run:
            try:
                startup = self.startup_contract(run_id)
            except StartupContractError as exc:
                if exc.code != "startup_contract_missing":
                    raise
        return {"run":dict(run) if run else None,"authorization":self.authorization(run_id) if run else None,"startup_contract":startup,"candidate":self.candidate_snapshot(run_id) if run else None,"specs":row("SELECT * FROM specs WHERE run_id=? ORDER BY position",(run_id,)),"tickets":row("SELECT t.* FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE s.run_id=?",(run_id,)),"threads":row("SELECT * FROM threads WHERE run_id=?",(run_id,)),"actions":row("SELECT * FROM actions WHERE run_id=? ORDER BY action_id",(run_id,)),"events":row("SELECT * FROM events WHERE run_id=? ORDER BY event_id",(run_id,)),"observations":row("SELECT * FROM observations WHERE run_id=? ORDER BY observation_id",(run_id,)),"evidence_refs":row("SELECT * FROM evidence_refs WHERE run_id=? ORDER BY evidence_id",(run_id,))}
