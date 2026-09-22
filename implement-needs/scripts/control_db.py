"""SQLite state store for the Implement Needs controller."""
from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
SCHEMA_VERSION = 21

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

SPEC_ROUTE_COLUMNS = (
    ("route_summary", "TEXT NOT NULL DEFAULT '{}'"),
    ("route_summary_digest", "TEXT"),
    ("route_summary_status", "TEXT NOT NULL DEFAULT 'missing'"),
)

DECISION_COLUMNS = (
    ("actor", "TEXT NOT NULL DEFAULT ''"),
    ("scope", "TEXT NOT NULL DEFAULT '{}'"),
    ("source", "TEXT NOT NULL DEFAULT ''"),
    ("authorization_digest", "TEXT NOT NULL DEFAULT ''"),
)

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, initiative TEXT NOT NULL, requirement TEXT NOT NULL, status TEXT NOT NULL, current_action TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, execution_mode TEXT NOT NULL DEFAULT 'whole-spec', controller_task_id TEXT, queue_definition TEXT NOT NULL DEFAULT '[]', business_version INTEGER NOT NULL DEFAULT 0, run_phase TEXT NOT NULL DEFAULT 'initialized', terminal_result TEXT, stop_reason TEXT, recovery_action TEXT);
CREATE TABLE IF NOT EXISTS specs(spec_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), title TEXT NOT NULL, status TEXT NOT NULL, position INTEGER NOT NULL, blocked_by TEXT NOT NULL DEFAULT '[]', acceptance TEXT NOT NULL DEFAULT '[]', generation INTEGER NOT NULL DEFAULT 0, route_summary TEXT NOT NULL DEFAULT '{}', route_summary_digest TEXT, route_summary_status TEXT NOT NULL DEFAULT 'missing', UNIQUE(run_id,position));
CREATE TABLE IF NOT EXISTS tickets(ticket_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL REFERENCES specs(spec_id), title TEXT NOT NULL, status TEXT NOT NULL, blocked_by TEXT NOT NULL DEFAULT '[]', commits TEXT NOT NULL DEFAULT '[]', tests TEXT NOT NULL DEFAULT '[]', acceptance TEXT NOT NULL DEFAULT '[]', issue_url TEXT, queue_position INTEGER, UNIQUE(spec_id,title));
CREATE TABLE IF NOT EXISTS threads(thread_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), kind TEXT NOT NULL, spec_id TEXT REFERENCES specs(spec_id), lifecycle TEXT NOT NULL, outcome TEXT NOT NULL DEFAULT 'unknown', next_action TEXT, last_observed_at TEXT NOT NULL, archive_operation_evidence TEXT NOT NULL DEFAULT '[]', archive_readback_evidence TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS thread_bootstraps(thread_id TEXT PRIMARY KEY REFERENCES threads(thread_id), run_id TEXT NOT NULL REFERENCES runs(run_id), state TEXT NOT NULL, budget INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, route_receipt TEXT, assignment_receipt TEXT, cancellation_receipt TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS test_train_obligations(obligation_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), spec_id TEXT NOT NULL REFERENCES specs(spec_id), level TEXT NOT NULL, required INTEGER NOT NULL, status TEXT NOT NULL, candidate_sha TEXT, evidence TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(run_id,spec_id,level));
CREATE TABLE IF NOT EXISTS test_train_checkpoints(checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), sequence INTEGER NOT NULL, start_position INTEGER NOT NULL, end_position INTEGER NOT NULL, members TEXT NOT NULL, status TEXT NOT NULL, candidate_sha TEXT, evidence TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(run_id,sequence));
CREATE TABLE IF NOT EXISTS run_policies(run_id TEXT PRIMARY KEY REFERENCES runs(run_id), canonical_payload TEXT NOT NULL, policy_digest TEXT NOT NULL, implementation_digest TEXT NOT NULL, status TEXT NOT NULL, rules_version TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS backup_manifests(manifest_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), payload TEXT NOT NULL, manifest_digest TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS restore_records(restore_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), manifest_id INTEGER NOT NULL REFERENCES backup_manifests(manifest_id), status TEXT NOT NULL, reconciliation_status TEXT NOT NULL, evidence TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS context_measurements(measurement_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), phase TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, read_business_version INTEGER NOT NULL, payload_bytes INTEGER NOT NULL, token_estimate INTEGER NOT NULL, observed_tokens INTEGER, fee REAL, latency_ms REAL, refresh_count INTEGER NOT NULL DEFAULT 0, rejection_count INTEGER NOT NULL DEFAULT 0, coverage TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runtime_observations(observation_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), observation_key TEXT NOT NULL UNIQUE, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, phase TEXT NOT NULL, status TEXT NOT NULL, scope TEXT NOT NULL, unit TEXT NOT NULL, started_at TEXT, ended_at TEXT, duration_ms REAL, source TEXT NOT NULL, usage TEXT NOT NULL DEFAULT '{}', metadata TEXT NOT NULL DEFAULT '{}', observed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS actions(action_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), kind TEXT NOT NULL, target TEXT NOT NULL, status TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, attempts INTEGER NOT NULL DEFAULT 0, result TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS action_claims(action_id INTEGER PRIMARY KEY REFERENCES actions(action_id), owner_id TEXT NOT NULL, claimed_at TEXT NOT NULL, lease_expires_at TEXT NOT NULL, supports_fencing INTEGER NOT NULL DEFAULT 0, outcome_reconciled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS delivery_proofs(proof_id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, artifact_type TEXT NOT NULL, artifact_ref TEXT NOT NULL, evidence TEXT NOT NULL, observed_at TEXT NOT NULL, UNIQUE(entity_type,entity_id,artifact_type,artifact_ref));
CREATE TABLE IF NOT EXISTS delivery_receipts(receipt_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, repository TEXT NOT NULL, target_sha TEXT NOT NULL, target_ref TEXT NOT NULL, artifact_digest TEXT, test_plan TEXT NOT NULL, test_selection TEXT NOT NULL, environment_fingerprint TEXT NOT NULL, acceptance_version TEXT NOT NULL, validator_version TEXT NOT NULL, result TEXT NOT NULL, source_kind TEXT NOT NULL, provenance TEXT NOT NULL, source_uri TEXT NOT NULL, observed_at TEXT NOT NULL, equivalence_policy TEXT, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dependency_waivers(waiver_id INTEGER PRIMARY KEY AUTOINCREMENT, dependent_type TEXT NOT NULL, dependent_id TEXT NOT NULL, blocker_type TEXT NOT NULL, blocker_id TEXT NOT NULL, reason TEXT NOT NULL, authorization_source TEXT NOT NULL, scope TEXT NOT NULL, evidence TEXT NOT NULL, observed_at TEXT NOT NULL, UNIQUE(dependent_type,dependent_id,blocker_type,blocker_id));
CREATE TABLE IF NOT EXISTS external_waits(external_request_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), action_id INTEGER, event_cursor INTEGER NOT NULL, wake_condition TEXT NOT NULL, next_safe_check_at TEXT NOT NULL, status TEXT NOT NULL, last_result_digest TEXT, poll_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS operation_intents(intent_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), logical_action TEXT NOT NULL, target TEXT NOT NULL, target_state TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL, owner TEXT, result TEXT, error TEXT, business_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, operation TEXT NOT NULL DEFAULT '', generation INTEGER NOT NULL DEFAULT 0, input_digest TEXT NOT NULL DEFAULT '', normalized_parameters TEXT NOT NULL DEFAULT '{}', external_request_id TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0, reconciliation_evidence TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS intent_claims(claim_id INTEGER PRIMARY KEY AUTOINCREMENT, intent_id INTEGER NOT NULL REFERENCES operation_intents(intent_id), owner TEXT NOT NULL, lease_until TEXT NOT NULL, fencing_supported INTEGER NOT NULL, fencing_receipt TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recovery_records(intent_id INTEGER PRIMARY KEY REFERENCES operation_intents(intent_id), owner TEXT NOT NULL, classification TEXT NOT NULL, budget INTEGER NOT NULL, consumed INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, last_evidence TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(event_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL, observed_at TEXT NOT NULL, business_version INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS observations(observation_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, payload TEXT NOT NULL, observed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recovery_states(recovery_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), action_id INTEGER NOT NULL REFERENCES actions(action_id), category TEXT NOT NULL, fingerprint TEXT NOT NULL, retry_owner TEXT NOT NULL, budget_version TEXT NOT NULL, max_attempts INTEGER NOT NULL, deadline_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, last_strategy_digest TEXT NOT NULL, last_progress_marker TEXT NOT NULL, last_evidence_digest TEXT NOT NULL, status TEXT NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(action_id,fingerprint));
CREATE TABLE IF NOT EXISTS managed_turns(turn_key TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), task_id TEXT NOT NULL, attempt_id TEXT NOT NULL, formal_thread_id TEXT NOT NULL, host_id TEXT NOT NULL, turn_id TEXT NOT NULL, previous_turn_id TEXT, operation_intent_id INTEGER REFERENCES operation_intents(intent_id), failure_class TEXT NOT NULL, status TEXT NOT NULL, checkpoint TEXT NOT NULL DEFAULT '{}', terminal_event TEXT NOT NULL DEFAULT '{}', history_readback TEXT NOT NULL DEFAULT '{}', output_evidence TEXT NOT NULL DEFAULT '[]', side_effect_evidence TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(formal_thread_id,turn_id));
CREATE TABLE IF NOT EXISTS runtime_snapshots(run_id TEXT PRIMARY KEY REFERENCES runs(run_id), state_version INTEGER NOT NULL, event_cursor INTEGER NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS unresolved_exceptions(exception_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), fingerprint TEXT NOT NULL, category TEXT NOT NULL, summary TEXT NOT NULL, log_uri TEXT, details TEXT NOT NULL DEFAULT '{}', resolved INTEGER NOT NULL DEFAULT 0, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, UNIQUE(run_id,fingerprint));
CREATE TABLE IF NOT EXISTS terminal_validations(run_id TEXT PRIMARY KEY REFERENCES runs(run_id), state_version INTEGER NOT NULL, decision TEXT NOT NULL, evidence TEXT NOT NULL, validated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence_refs(evidence_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, evidence_kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS phase_receipts(receipt_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), from_phase TEXT NOT NULL, to_phase TEXT NOT NULL, result TEXT NOT NULL, payload TEXT NOT NULL, business_version INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS run_authorizations(run_id TEXT PRIMARY KEY REFERENCES runs(run_id), payload TEXT NOT NULL, authorization_digest TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidate_freezes(freeze_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), candidate_sha TEXT NOT NULL, merge_sha TEXT, authorization_digest TEXT NOT NULL, status TEXT NOT NULL, invalidation_reason TEXT, payload TEXT NOT NULL, business_version INTEGER NOT NULL, created_at TEXT NOT NULL, invalidated_at TEXT);
CREATE TABLE IF NOT EXISTS candidate_evidence(evidence_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), freeze_id INTEGER NOT NULL REFERENCES candidate_freezes(freeze_id), evidence_kind TEXT NOT NULL, candidate_sha TEXT NOT NULL, authorization_digest TEXT NOT NULL, payload TEXT NOT NULL, business_version INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS startup_contracts(run_id TEXT PRIMARY KEY REFERENCES runs(run_id), payload TEXT NOT NULL, status TEXT NOT NULL, contract_digest TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions(decision_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), subject TEXT NOT NULL, selected TEXT NOT NULL, recommendation TEXT, evidence TEXT NOT NULL DEFAULT '[]', rationale TEXT NOT NULL, actor TEXT NOT NULL, scope TEXT NOT NULL, source TEXT NOT NULL, authorization_digest TEXT NOT NULL, created_at TEXT NOT NULL);
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

def _canonical_json(value, name="value"):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be JSON serializable") from exc

def _evidence(value):
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError("evidence must be a non-empty string array")
    return [item.strip() for item in value]

@contextmanager
def transaction(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if conn.in_transaction: conn.rollback()
        raise
    else:
        if conn.in_transaction: conn.commit()

def _time_after(seconds):
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 1:
        raise ValueError("lease_seconds must be a positive integer")
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()

def _is_expired(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) <= datetime.now(timezone.utc)


class ActionConflict(RuntimeError):
    """A second executable action would violate the run's single-action gate."""

class ActionClaimConflict(RuntimeError):
    """Another executor currently owns the action."""

class UnsafeLeaseTakeover(RuntimeError):
    """An expired executor cannot be replaced without fencing and reconciliation."""


class StaleState(RuntimeError):
    """The caller attempted to write using an obsolete business version."""

    code = "stale_state"

    def __init__(self, run_id: str, expected: int, actual: int):
        self.run_id, self.expected, self.actual = run_id, expected, actual
        super().__init__(f"stale_state: run {run_id} expected version {expected}, current version {actual}")


class DatabaseModeError(ValueError):
    """The requested database mode is incompatible with the path or schema."""


class DecisionError(ValueError):
    """A public decision request does not satisfy the decision contract."""

    def __init__(self, code, details=None):
        self.code = code
        self.details = details or {}
        super().__init__(f"{code}: {self.details}")


class RestoreValidationError(ValueError):
    """A backup/restore validation failed closed with machine-readable details."""

    def __init__(self, code, details=None):
        self.code = code
        self.details = details or {}
        super().__init__(f"{code}: {self.details}")


class PolicyError(ValueError):
    """A policy identity operation failed closed with a stable error code."""

    def __init__(self, code, details=None):
        self.code = code
        self.details = details or {}
        super().__init__(code)


_EVIDENCE_URI = re.compile(r"^[a-z][a-z0-9+.-]*:(?:/{0,2})\S+$", re.IGNORECASE)
_COMMIT_SCHEMES = frozenset(("commit", "git", "https", "pr", "sha"))
_TEST_SCHEMES = frozenset(("check", "ci", "https", "pytest", "test"))
_READBACK_SCHEMES = frozenset(("github", "https"))
_ACCEPTANCE_SCHEMES = frozenset(("acceptance", "file", "github", "https"))
_SPEC_LOCAL_ALIAS = re.compile(r"^(?P<spec>#[0-9]+)/(?P<position>[0-9]{2})$")
_SPEC_LOCAL_ALIAS_PREFIX = re.compile(r"^#[0-9]+/")


def _valid_evidence(value: object, schemes: frozenset[str]) -> bool:
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if not isinstance(item, str) or not item.strip() or not _EVIDENCE_URI.match(item.strip()):
            return False
        if item.split(":", 1)[0].lower() not in schemes:
            return False
    return True


def _normalize_spec_local_blockers(spec_id, blockers, local_ticket_ids):
    """Resolve only verified, same-SPEC planning aliases to ticket IDs."""
    normalized = []
    for blocker in blockers:
        if not isinstance(blocker, str) or "/" not in blocker:
            normalized.append(blocker)
            continue
        match = _SPEC_LOCAL_ALIAS.fullmatch(blocker)
        if match is None:
            if _SPEC_LOCAL_ALIAS_PREFIX.match(blocker):
                raise ValueError(f"malformed SPEC-local ticket alias: {blocker}")
            normalized.append(blocker)
            continue
        alias_spec = match.group("spec")
        if alias_spec != spec_id:
            raise ValueError(f"cross-SPEC ticket alias: {blocker}")
        local_position = int(match.group("position"))
        target = local_ticket_ids.get(local_position)
        if target is None:
            raise ValueError(f"unknown SPEC-local ticket alias: {blocker}")
        normalized.append(target)
    return normalized

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
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='operation_intents'").fetchone():
            raise DatabaseModeError("database schema lacks operation_intents table")
        for table in ("intent_claims", "recovery_records"):
            if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                raise DatabaseModeError(f"database schema lacks {table} table")
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='thread_bootstraps'").fetchone():
            raise DatabaseModeError("database schema lacks thread_bootstraps table")
        for table in ("test_train_obligations", "test_train_checkpoints"):
            if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                raise DatabaseModeError(f"database schema lacks {table} table")
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='run_policies'").fetchone():
            raise DatabaseModeError("database schema lacks run_policies table")
        for table in ("backup_manifests", "restore_records"):
            if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                raise DatabaseModeError(f"database schema lacks {table} table")
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='context_measurements'").fetchone():
            raise DatabaseModeError("database schema lacks context_measurements table")
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_observations'").fetchone():
            raise DatabaseModeError("database schema lacks runtime_observations table")
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='action_claims'").fetchone():
            raise DatabaseModeError("database schema lacks action_claims table")
        for table in ("delivery_proofs", "delivery_receipts", "dependency_waivers", "external_waits"):
            if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                raise DatabaseModeError(f"database schema lacks {table} table")
        for table in ("recovery_states", "runtime_snapshots", "unresolved_exceptions", "terminal_validations"):
            if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                raise DatabaseModeError(f"database schema lacks {table} table")
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='managed_turns'").fetchone():
            raise DatabaseModeError("database schema lacks managed_turns table")
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
            spec_columns={row[1] for row in self.conn.execute("PRAGMA table_info(specs)")}
            for name,declaration in SPEC_ROUTE_COLUMNS:
                if name not in spec_columns:
                    self.conn.execute(f"ALTER TABLE specs ADD COLUMN {name} {declaration}")
            decision_columns={row[1] for row in self.conn.execute("PRAGMA table_info(decisions)")}
            for name,declaration in DECISION_COLUMNS:
                if name not in decision_columns:
                    self.conn.execute(f"ALTER TABLE decisions ADD COLUMN {name} {declaration}")
            run_columns={row[1] for row in self.conn.execute("PRAGMA table_info(runs)")}
            if "business_version" not in run_columns:
                self.conn.execute("ALTER TABLE runs ADD COLUMN business_version INTEGER NOT NULL DEFAULT 0")
            event_columns={row[1] for row in self.conn.execute("PRAGMA table_info(events)")}
            if "business_version" not in event_columns:
                self.conn.execute("ALTER TABLE events ADD COLUMN business_version INTEGER NOT NULL DEFAULT 0")
            self.conn.execute("CREATE TABLE IF NOT EXISTS observations(observation_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, payload TEXT NOT NULL, observed_at TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS recovery_states(recovery_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), action_id INTEGER NOT NULL REFERENCES actions(action_id), category TEXT NOT NULL, fingerprint TEXT NOT NULL, retry_owner TEXT NOT NULL, budget_version TEXT NOT NULL, max_attempts INTEGER NOT NULL, deadline_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, last_strategy_digest TEXT NOT NULL, last_progress_marker TEXT NOT NULL, last_evidence_digest TEXT NOT NULL, status TEXT NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(action_id,fingerprint))")
            self.conn.execute("CREATE TABLE IF NOT EXISTS managed_turns(turn_key TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), task_id TEXT NOT NULL, attempt_id TEXT NOT NULL, formal_thread_id TEXT NOT NULL, host_id TEXT NOT NULL, turn_id TEXT NOT NULL, previous_turn_id TEXT, operation_intent_id INTEGER REFERENCES operation_intents(intent_id), failure_class TEXT NOT NULL, status TEXT NOT NULL, checkpoint TEXT NOT NULL DEFAULT '{}', terminal_event TEXT NOT NULL DEFAULT '{}', history_readback TEXT NOT NULL DEFAULT '{}', output_evidence TEXT NOT NULL DEFAULT '[]', side_effect_evidence TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(formal_thread_id,turn_id))")
            self.conn.execute("CREATE TABLE IF NOT EXISTS runtime_snapshots(run_id TEXT PRIMARY KEY REFERENCES runs(run_id), state_version INTEGER NOT NULL, event_cursor INTEGER NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS unresolved_exceptions(exception_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), fingerprint TEXT NOT NULL, category TEXT NOT NULL, summary TEXT NOT NULL, log_uri TEXT, details TEXT NOT NULL DEFAULT '{}', resolved INTEGER NOT NULL DEFAULT 0, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, UNIQUE(run_id,fingerprint))")
            self.conn.execute("CREATE TABLE IF NOT EXISTS terminal_validations(run_id TEXT PRIMARY KEY REFERENCES runs(run_id), state_version INTEGER NOT NULL, decision TEXT NOT NULL, evidence TEXT NOT NULL, validated_at TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS evidence_refs(evidence_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, evidence_kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS phase_receipts(receipt_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), from_phase TEXT NOT NULL, to_phase TEXT NOT NULL, result TEXT NOT NULL, payload TEXT NOT NULL, business_version INTEGER NOT NULL, created_at TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS run_authorizations(run_id TEXT PRIMARY KEY REFERENCES runs(run_id), payload TEXT NOT NULL, authorization_digest TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS candidate_freezes(freeze_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), candidate_sha TEXT NOT NULL, merge_sha TEXT, authorization_digest TEXT NOT NULL, status TEXT NOT NULL, invalidation_reason TEXT, payload TEXT NOT NULL, business_version INTEGER NOT NULL, created_at TEXT NOT NULL, invalidated_at TEXT)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS candidate_evidence(evidence_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), freeze_id INTEGER NOT NULL REFERENCES candidate_freezes(freeze_id), evidence_kind TEXT NOT NULL, candidate_sha TEXT NOT NULL, authorization_digest TEXT NOT NULL, payload TEXT NOT NULL, business_version INTEGER NOT NULL, created_at TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS startup_contracts(run_id TEXT PRIMARY KEY REFERENCES runs(run_id), payload TEXT NOT NULL, status TEXT NOT NULL, contract_digest TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS operation_intents(intent_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), logical_action TEXT NOT NULL, target TEXT NOT NULL, target_state TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL, owner TEXT, result TEXT, error TEXT, business_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            intent_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(operation_intents)")}
            for name, declaration in (("operation", "TEXT NOT NULL DEFAULT ''"), ("generation", "INTEGER NOT NULL DEFAULT 0"), ("input_digest", "TEXT NOT NULL DEFAULT ''"), ("normalized_parameters", "TEXT NOT NULL DEFAULT '{}'"), ("external_request_id", "TEXT NOT NULL DEFAULT ''"), ("attempts", "INTEGER NOT NULL DEFAULT 0"), ("reconciliation_evidence", "TEXT NOT NULL DEFAULT '[]'")):
                if name not in intent_columns:
                    self.conn.execute(f"ALTER TABLE operation_intents ADD COLUMN {name} {declaration}")
            self.conn.execute("CREATE TABLE IF NOT EXISTS thread_bootstraps(thread_id TEXT PRIMARY KEY REFERENCES threads(thread_id), run_id TEXT NOT NULL REFERENCES runs(run_id), state TEXT NOT NULL, budget INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, route_receipt TEXT, assignment_receipt TEXT, cancellation_receipt TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            self.conn.execute("INSERT OR IGNORE INTO thread_bootstraps(thread_id,run_id,state,budget,created_at,updated_at) SELECT thread_id,run_id,'bootstrap',3,last_observed_at,last_observed_at FROM threads")
            self.conn.execute("CREATE TABLE IF NOT EXISTS test_train_obligations(obligation_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), spec_id TEXT NOT NULL REFERENCES specs(spec_id), level TEXT NOT NULL, required INTEGER NOT NULL, status TEXT NOT NULL, candidate_sha TEXT, evidence TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(run_id,spec_id,level))")
            self.conn.execute("CREATE TABLE IF NOT EXISTS test_train_checkpoints(checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), sequence INTEGER NOT NULL, start_position INTEGER NOT NULL, end_position INTEGER NOT NULL, members TEXT NOT NULL, status TEXT NOT NULL, candidate_sha TEXT, evidence TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(run_id,sequence))")
            self.conn.execute("CREATE TABLE IF NOT EXISTS run_policies(run_id TEXT PRIMARY KEY REFERENCES runs(run_id), canonical_payload TEXT NOT NULL, policy_digest TEXT NOT NULL, implementation_digest TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            policy_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(run_policies)")}
            if "rules_version" not in policy_columns:
                self.conn.execute("ALTER TABLE run_policies ADD COLUMN rules_version TEXT NOT NULL DEFAULT ''")
            self.conn.execute("CREATE TABLE IF NOT EXISTS backup_manifests(manifest_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), payload TEXT NOT NULL, manifest_digest TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS restore_records(restore_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), manifest_id INTEGER NOT NULL REFERENCES backup_manifests(manifest_id), status TEXT NOT NULL, reconciliation_status TEXT NOT NULL, evidence TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS context_measurements(measurement_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), phase TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, read_business_version INTEGER NOT NULL, payload_bytes INTEGER NOT NULL, token_estimate INTEGER NOT NULL, observed_tokens INTEGER, fee REAL, latency_ms REAL, refresh_count INTEGER NOT NULL DEFAULT 0, rejection_count INTEGER NOT NULL DEFAULT 0, coverage TEXT NOT NULL, created_at TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS runtime_observations(observation_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), observation_key TEXT NOT NULL UNIQUE, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, phase TEXT NOT NULL, status TEXT NOT NULL, scope TEXT NOT NULL, unit TEXT NOT NULL, started_at TEXT, ended_at TEXT, duration_ms REAL, source TEXT NOT NULL, usage TEXT NOT NULL DEFAULT '{}', metadata TEXT NOT NULL DEFAULT '{}', observed_at TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS action_claims(action_id INTEGER PRIMARY KEY REFERENCES actions(action_id), owner_id TEXT NOT NULL, claimed_at TEXT NOT NULL, lease_expires_at TEXT NOT NULL, supports_fencing INTEGER NOT NULL DEFAULT 0, outcome_reconciled INTEGER NOT NULL DEFAULT 0)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS delivery_proofs(proof_id INTEGER PRIMARY KEY AUTOINCREMENT, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, artifact_type TEXT NOT NULL, artifact_ref TEXT NOT NULL, evidence TEXT NOT NULL, observed_at TEXT NOT NULL, UNIQUE(entity_type,entity_id,artifact_type,artifact_ref))")
            self.conn.execute("CREATE TABLE IF NOT EXISTS delivery_receipts(receipt_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id), entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, repository TEXT NOT NULL, target_sha TEXT NOT NULL, target_ref TEXT NOT NULL, artifact_digest TEXT, test_plan TEXT NOT NULL, test_selection TEXT NOT NULL, environment_fingerprint TEXT NOT NULL, acceptance_version TEXT NOT NULL, validator_version TEXT NOT NULL, result TEXT NOT NULL, source_kind TEXT NOT NULL, provenance TEXT NOT NULL, source_uri TEXT NOT NULL, observed_at TEXT NOT NULL, equivalence_policy TEXT, payload TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS dependency_waivers(waiver_id INTEGER PRIMARY KEY AUTOINCREMENT, dependent_type TEXT NOT NULL, dependent_id TEXT NOT NULL, blocker_type TEXT NOT NULL, blocker_id TEXT NOT NULL, reason TEXT NOT NULL, authorization_source TEXT NOT NULL, scope TEXT NOT NULL, evidence TEXT NOT NULL, observed_at TEXT NOT NULL, UNIQUE(dependent_type,dependent_id,blocker_type,blocker_id))")
            self.conn.execute("CREATE TABLE IF NOT EXISTS external_waits(external_request_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), action_id INTEGER, event_cursor INTEGER NOT NULL, wake_condition TEXT NOT NULL, next_safe_check_at TEXT NOT NULL, status TEXT NOT NULL, last_result_digest TEXT, poll_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS intent_claims(claim_id INTEGER PRIMARY KEY AUTOINCREMENT, intent_id INTEGER NOT NULL REFERENCES operation_intents(intent_id), owner TEXT NOT NULL, lease_until TEXT NOT NULL, fencing_supported INTEGER NOT NULL, fencing_receipt TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS recovery_records(intent_id INTEGER PRIMARY KEY REFERENCES operation_intents(intent_id), owner TEXT NOT NULL, classification TEXT NOT NULL, budget INTEGER NOT NULL, consumed INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, last_evidence TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
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

    def require_context_fresh(self, context, run_id=None):
        """Validate a projection envelope before a context-driven mutation."""
        if not isinstance(context, dict):
            raise ValueError("context envelope is required")
        required = {"run_id", "entity_type", "entity_id", "read_business_version"}
        if not required.issubset(context):
            raise ValueError("context envelope is incomplete")
        context_run_id = context["run_id"]
        if run_id is not None and context_run_id != run_id:
            raise ValueError("context run_id mismatch")
        version = context["read_business_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise ValueError("context read_business_version is invalid")
        return self._check_version(context_run_id, version)

    def update_spec_from_context(self, spec_id, status, context, gate=None):
        row = self.conn.execute("SELECT run_id FROM specs WHERE spec_id=?", (spec_id,)).fetchone()
        if row is None:
            raise ValueError("unknown spec")
        version = self.require_context_fresh(context, row[0])
        return self.update_spec(spec_id, status, version, gate)

    def update_ticket_from_context(self, ticket_id, status, context, commits=None, tests=None, acceptance=None, gate=None):
        row = self.conn.execute(
            "SELECT s.run_id FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE t.ticket_id=?", (ticket_id,)
        ).fetchone()
        if row is None:
            raise ValueError("unknown ticket")
        version = self.require_context_fresh(context, row[0])
        return self.update_ticket(ticket_id, status, commits, tests, acceptance, version, gate)

    def record_context_measurement(self, run_id, context, measurement):
        """Persist telemetry without advancing business_version or satisfying a gate."""
        if not isinstance(measurement, dict):
            raise ValueError("context measurement must be an object")
        self.require_context_fresh(context, run_id)
        required = {"payload_bytes", "token_estimate", "coverage"}
        if not required.issubset(measurement):
            raise ValueError("context measurement is incomplete")
        if any(not isinstance(measurement[key], int) or isinstance(measurement[key], bool) or measurement[key] < 0 for key in ("payload_bytes", "token_estimate")):
            raise ValueError("context measurement sizes are invalid")
        coverage = measurement["coverage"]
        if not isinstance(coverage, dict) or not coverage.get("tokens") or not coverage.get("fees"):
            raise ValueError("context measurement coverage is required")
        with self.transaction():
            stamp = now()
            cur = self.conn.execute(
                "INSERT INTO context_measurements(run_id,phase,entity_type,entity_id,read_business_version,payload_bytes,token_estimate,observed_tokens,fee,latency_ms,refresh_count,rejection_count,coverage,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, context["phase"], context["entity_type"], context["entity_id"], context["read_business_version"], measurement["payload_bytes"], measurement["token_estimate"], measurement.get("observed_tokens"), measurement.get("fee"), measurement.get("latency_ms"), measurement.get("refresh_count", 0), measurement.get("rejection_count", 0), self._canonical_json(coverage), stamp),
            )
            return {"measurement_id": cur.lastrowid, "run_id": run_id, "business_version": self.business_version(run_id), "measurement": measurement}

    def context_measurements(self, run_id):
        rows = [dict(row) for row in self.conn.execute("SELECT * FROM context_measurements WHERE run_id=? ORDER BY measurement_id", (run_id,))]
        for row in rows:
            row["coverage"] = json.loads(row["coverage"])
        return rows

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

    def event_cursor(self, run_id):
        row = self.conn.execute("SELECT COALESCE(MAX(event_id),0) FROM events WHERE run_id=?", (run_id,)).fetchone()
        return int(row[0])

    def events_since(self, run_id, event_cursor=0):
        if not isinstance(event_cursor, int) or isinstance(event_cursor, bool) or event_cursor < 0:
            raise ValueError("event_cursor must be a non-negative integer")
        rows = self.conn.execute("SELECT * FROM events WHERE run_id=? AND event_id>? ORDER BY event_id", (run_id, event_cursor)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            result.append(item)
        return result

    def save_snapshot(self, run_id, payload, expected_event_cursor=None, expected_state_version=None):
        if not isinstance(payload, dict):
            raise ValueError("snapshot payload must be a JSON object")
        with self.transaction():
            cursor = self.event_cursor(run_id)
            if expected_event_cursor is not None and expected_event_cursor != cursor:
                raise ValueError(f"stale snapshot cursor: expected {expected_event_cursor}, current {cursor}")
            if expected_state_version is not None and expected_state_version != cursor:
                raise ValueError(f"stale snapshot version: expected {expected_state_version}, current {cursor}")
            stamp = now()
            encoded = _canonical_json(payload, "snapshot")
            self.conn.execute("INSERT INTO runtime_snapshots(run_id,state_version,event_cursor,payload,created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET state_version=excluded.state_version,event_cursor=excluded.event_cursor,payload=excluded.payload,updated_at=excluded.updated_at", (run_id, cursor, cursor, encoded, stamp, stamp))
            self._business_event(run_id, "run", run_id, "runtime_snapshot_saved", {"state_version": cursor, "event_cursor": cursor})
            cursor = self.event_cursor(run_id)
            self.conn.execute("UPDATE runtime_snapshots SET state_version=?,event_cursor=? WHERE run_id=?", (cursor, cursor, run_id))
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
        with self.transaction():
            self.conn.execute("INSERT INTO unresolved_exceptions(run_id,fingerprint,category,summary,log_uri,details,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(run_id,fingerprint) DO UPDATE SET category=excluded.category,summary=excluded.summary,log_uri=excluded.log_uri,details=excluded.details,resolved=0,last_seen=excluded.last_seen", (run_id, fingerprint, category, summary, log_uri, _canonical_json(details or {}, "details"), stamp, stamp))
            row = self.conn.execute("SELECT * FROM unresolved_exceptions WHERE run_id=? AND fingerprint=?", (run_id, fingerprint)).fetchone()
            self._business_event(run_id, "exception", str(row["exception_id"]), "exception_unresolved", {"fingerprint": fingerprint, "category": category})
            return dict(row)

    def resolve_exception(self, run_id, fingerprint, evidence):
        evidence = _evidence(evidence)
        with self.transaction():
            row = self.conn.execute("SELECT * FROM unresolved_exceptions WHERE run_id=? AND fingerprint=?", (run_id, fingerprint)).fetchone()
            if not row:
                raise ValueError("unknown exception")
            self.conn.execute("UPDATE unresolved_exceptions SET resolved=1,last_seen=?,details=? WHERE exception_id=?", (now(), _canonical_json({"resolution_evidence": evidence}, "details"), row["exception_id"]))
            self._business_event(run_id, "exception", str(row["exception_id"]), "exception_resolved", {"fingerprint": fingerprint, "evidence": evidence})
            return dict(self.conn.execute("SELECT * FROM unresolved_exceptions WHERE exception_id=?", (row["exception_id"],)).fetchone())

    def unresolved_exceptions(self, run_id):
        return [dict(row) for row in self.conn.execute("SELECT * FROM unresolved_exceptions WHERE run_id=? AND resolved=0 ORDER BY exception_id", (run_id,))]

    def record_terminal_validation(self, run_id, decision, evidence):
        if decision not in {"allow", "reject"} or not isinstance(evidence, list) or not evidence:
            raise ValueError("terminal validation evidence is required")
        with self.transaction():
            cursor = self.event_cursor(run_id)
            self.conn.execute("INSERT INTO terminal_validations(run_id,state_version,decision,evidence,validated_at) VALUES(?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET state_version=excluded.state_version,decision=excluded.decision,evidence=excluded.evidence,validated_at=excluded.validated_at", (run_id, cursor, decision, _canonical_json(evidence, "evidence"), now()))
            self._business_event(run_id, "run", run_id, "terminal_validation_recorded", {"decision": decision, "evidence": evidence})
            cursor = self.event_cursor(run_id)
            self.conn.execute("UPDATE terminal_validations SET state_version=? WHERE run_id=?", (cursor, run_id))
            return {"run_id": run_id, "decision": decision, "state_version": cursor, "evidence": evidence}

    def terminal_validation_satisfied(self, run_id):
        row = self.conn.execute("SELECT * FROM terminal_validations WHERE run_id=?", (run_id,)).fetchone()
        return bool(row and row["decision"] == "allow" and row["state_version"] == self.event_cursor(run_id))
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
    def create_operation_intent(self, run_id, operation, target, parameters, generation=0, idempotency_key=None):
        if not isinstance(operation, str) or not operation.strip() or not isinstance(target, str) or not target.strip():
            raise ValueError("operation and target are required")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
            raise ValueError("generation must be a non-negative integer")
        normalized = json.dumps(parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        key = idempotency_key or f"{run_id}:{operation}:{target}:{generation}:{digest}"
        request_id = "in-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        with self.transaction():
            existing = self.conn.execute("SELECT * FROM operation_intents WHERE idempotency_key=?", (key,)).fetchone()
            if existing:
                if any(existing[field] != value for field, value in (("run_id", run_id), ("operation", operation), ("target", target), ("generation", generation), ("input_digest", digest), ("normalized_parameters", normalized))):
                    raise ValueError("idempotency key already exists with different intent")
                return {"intent": dict(existing), "created": False}
            cur = self.conn.execute("INSERT INTO operation_intents(run_id,logical_action,target,target_state,idempotency_key,status,business_version,created_at,updated_at,operation,generation,input_digest,normalized_parameters,external_request_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (run_id, operation, target, "requested", key, "prepared", self.business_version(run_id), now(), now(), operation, generation, digest, normalized, request_id))
            version = self._business_event(run_id, "operation_intent", str(cur.lastrowid), "operation_intent_created", {"operation": operation, "target": target, "external_request_id": request_id})
            self.conn.execute("UPDATE operation_intents SET business_version=? WHERE intent_id=?", (version, cur.lastrowid))
            return {"intent": dict(self.conn.execute("SELECT * FROM operation_intents WHERE intent_id=?", (cur.lastrowid,)).fetchone()), "created": True}

    def start_operation_intent(self, intent_id, executor_id):
        if not isinstance(executor_id, str) or not executor_id.strip():
            raise ValueError("executor_id is required")
        row = self.conn.execute("SELECT * FROM operation_intents WHERE intent_id=?", (intent_id,)).fetchone()
        if row is None:
            raise ValueError("unknown operation intent")
        if row["status"] == "outcome_unknown":
            raise ValueError("unknown outcome must be reconciled before retry")
        with self.transaction():
            if row["status"] not in {"prepared", "reconciled_not_found"}:
                raise ValueError(f"operation intent cannot start from {row['status']}")
            self.conn.execute("UPDATE operation_intents SET status='executing',attempts=attempts+1,owner=?,updated_at=? WHERE intent_id=?", (executor_id, now(), intent_id))
            version = self._business_event(row["run_id"], "operation_intent", str(intent_id), "operation_intent_started", {"executor_id": executor_id})
            self.conn.execute("UPDATE operation_intents SET business_version=? WHERE intent_id=?", (version, intent_id))
            return dict(self.conn.execute("SELECT * FROM operation_intents WHERE intent_id=?", (intent_id,)).fetchone())

    def mark_operation_unknown(self, intent_id, reason, evidence):
        if not isinstance(reason, str) or not reason.strip() or not isinstance(evidence, list) or not evidence:
            raise ValueError("unknown outcome reason and evidence are required")
        row = self.conn.execute("SELECT * FROM operation_intents WHERE intent_id=?", (intent_id,)).fetchone()
        if row is None:
            raise ValueError("unknown operation intent")
        with self.transaction():
            self.conn.execute("UPDATE operation_intents SET status='outcome_unknown',result=?,reconciliation_evidence=?,updated_at=? WHERE intent_id=?", (json.dumps({"reason": reason}), json.dumps(evidence), now(), intent_id))
            version = self._business_event(row["run_id"], "operation_intent", str(intent_id), "operation_outcome_unknown", {"reason": reason, "evidence": evidence})
            self.conn.execute("UPDATE operation_intents SET business_version=? WHERE intent_id=?", (version, intent_id))
            return {"intent_id": intent_id, "status": "outcome_unknown", "business_version": version}

    def reconcile_operation_intent(self, intent_id, outcome, evidence, result=None):
        if outcome not in {"not_found", "succeeded", "failed"} or not isinstance(evidence, list) or not evidence:
            raise ValueError("reconciliation outcome and evidence are required")
        row = self.conn.execute("SELECT * FROM operation_intents WHERE intent_id=?", (intent_id,)).fetchone()
        if row is None or row["status"] != "outcome_unknown":
            raise ValueError("operation intent is not awaiting reconciliation")
        status = "reconciled_not_found" if outcome == "not_found" else outcome
        with self.transaction():
            self.conn.execute("UPDATE operation_intents SET status=?,result=?,reconciliation_evidence=?,updated_at=? WHERE intent_id=?", (status, json.dumps(result or {"outcome": outcome}), json.dumps(evidence), now(), intent_id))
            version = self._business_event(row["run_id"], "operation_intent", str(intent_id), "operation_reconciled", {"outcome": outcome, "evidence": evidence})
            self.conn.execute("UPDATE operation_intents SET business_version=? WHERE intent_id=?", (version, intent_id))
            return dict(self.conn.execute("SELECT * FROM operation_intents WHERE intent_id=?", (intent_id,)).fetchone())

    def prepare_intent(self, run_id, logical_action, target, target_state, expected_version=None):
        values = (logical_action, target, target_state)
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("intent identity fields are required")
        key = ":".join((run_id, logical_action, target, target_state))
        with self.transaction():
            current = self._check_version(run_id, expected_version)
            existing = self.conn.execute("SELECT intent_id,status,idempotency_key FROM operation_intents WHERE idempotency_key=?", (key,)).fetchone()
            if existing is not None:
                return {"intent_id": existing[0], "status": existing[1], "idempotency_key": existing[2], "changed": False}
            stamp = now()
            cur = self.conn.execute(
                "INSERT INTO operation_intents(run_id,logical_action,target,target_state,idempotency_key,status,business_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (run_id, logical_action, target, target_state, key, "prepared", current, stamp, stamp),
            )
            version = self._business_event(run_id, "intent", str(cur.lastrowid), "intent_prepared", {"logical_action": logical_action, "target": target, "target_state": target_state, "idempotency_key": key})
            self.conn.execute("UPDATE operation_intents SET business_version=? WHERE intent_id=?", (version, cur.lastrowid))
            return {"intent_id": cur.lastrowid, "status": "prepared", "idempotency_key": key, "changed": True, "business_version": version}

    def intent(self, intent_id):
        row = self.conn.execute("SELECT * FROM operation_intents WHERE intent_id=?", (intent_id,)).fetchone()
        if row is None:
            raise ValueError("unknown intent")
        result = dict(row)
        for field in ("result",):
            if result[field] is not None:
                result[field] = json.loads(result[field])
        return result

    def record_intent_outcome(self, intent_id, status, response, readback=None, expected_version=None, reconciled=False):
        if status not in {"succeeded", "failed", "outcome_unknown"}:
            raise ValueError("unsupported intent outcome")
        row = self.conn.execute("SELECT run_id,status,target FROM operation_intents WHERE intent_id=?", (intent_id,)).fetchone()
        if row is None:
            raise ValueError("unknown intent")
        if status == "succeeded" and (not isinstance(response, dict) or response.get("status") != "verified" or not isinstance(readback, dict) or readback.get("status") != "verified"):
            raise ValueError("intent success requires verified response and authoritative readback")
        with self.transaction():
            current = self._check_version(row[0], expected_version)
            if row[1] == "succeeded" and status == "succeeded":
                return {"intent_id": intent_id, "changed": False, "status": "succeeded"}
            if row[1] == "outcome_unknown" and status == "succeeded" and not reconciled:
                raise ValueError("unknown intent requires reconciliation")
            payload = {"response": response, "readback": readback}
            error = response.get("error") if isinstance(response, dict) else None
            self.conn.execute("UPDATE operation_intents SET status=?,result=?,error=?,business_version=?,updated_at=? WHERE intent_id=?", (status, json.dumps(payload, ensure_ascii=False, sort_keys=True), error, current, now(), intent_id))
            version = self._business_event(row[0], "intent", str(intent_id), "intent_outcome_recorded", {"status": status, "target": row[2]})
            self.conn.execute("UPDATE operation_intents SET business_version=? WHERE intent_id=?", (version, intent_id))
            return {"intent_id": intent_id, "status": status, "business_version": version}

    def reconcile_intent(self, intent_id, readback, expected_version=None):
        row = self.conn.execute("SELECT run_id,status,target FROM operation_intents WHERE intent_id=?", (intent_id,)).fetchone()
        if row is None:
            raise ValueError("unknown intent")
        if row[1] != "outcome_unknown":
            raise ValueError("intent is not awaiting reconciliation")
        if not isinstance(readback, dict) or readback.get("status") != "verified":
            raise ValueError("reconciliation requires verified authoritative readback")
        return self.record_intent_outcome(intent_id, "succeeded", {"status": "verified", "source": "reconciliation"}, readback, expected_version, reconciled=True)

    def claim_intent(self, intent_id, owner, lease_until, fencing_supported=False, fencing_receipt=None, expected_version=None):
        if not isinstance(owner, str) or not owner.strip() or not isinstance(lease_until, str) or not lease_until.strip():
            raise ValueError("claim owner and lease are required")
        row = self.conn.execute("SELECT run_id,status FROM operation_intents WHERE intent_id=?", (intent_id,)).fetchone()
        if row is None:
            raise ValueError("unknown intent")
        with self.transaction():
            current = self._check_version(row[0], expected_version)
            active = self.conn.execute("SELECT claim_id,owner,lease_until FROM intent_claims WHERE intent_id=? AND status='active' ORDER BY claim_id DESC LIMIT 1", (intent_id,)).fetchone()
            if active is not None:
                if active[1] == owner and active[2] == lease_until:
                    return {"claim_id": active[0], "changed": False, "owner": owner}
                if active[2] > now():
                    raise ActionConflict("intent lease is still active")
                if not fencing_supported or not isinstance(fencing_receipt, dict) or fencing_receipt.get("status") != "verified":
                    raise ValueError("expired claim requires verified fencing capability and receipt")
                self.conn.execute("UPDATE intent_claims SET status='superseded',updated_at=? WHERE claim_id=?", (now(), active[0]))
            stamp = now()
            cur = self.conn.execute("INSERT INTO intent_claims(intent_id,owner,lease_until,fencing_supported,fencing_receipt,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (intent_id, owner, lease_until, int(bool(fencing_supported)), json.dumps(fencing_receipt, ensure_ascii=False, sort_keys=True) if fencing_receipt is not None else None, "active", stamp, stamp))
            version = self._business_event(row[0], "intent", str(intent_id), "intent_claimed", {"owner": owner, "lease_until": lease_until, "fencing_supported": bool(fencing_supported)})
            return {"claim_id": cur.lastrowid, "changed": True, "business_version": version}

    def record_recovery(self, intent_id, owner, classification, budget, evidence=None, expected_version=None):
        if not isinstance(owner, str) or not owner.strip() or not isinstance(classification, str) or not classification.strip() or not isinstance(budget, int) or budget < 0:
            raise ValueError("recovery owner, classification and non-negative budget are required")
        row = self.conn.execute("SELECT run_id FROM operation_intents WHERE intent_id=?", (intent_id,)).fetchone()
        if row is None:
            raise ValueError("unknown intent")
        with self.transaction():
            current = self._check_version(row[0], expected_version)
            existing = self.conn.execute("SELECT owner,classification,budget,consumed,status FROM recovery_records WHERE intent_id=?", (intent_id,)).fetchone()
            if existing is not None and existing[0] != owner:
                raise ValueError("recovery owner mismatch")
            if existing is None:
                self.conn.execute("INSERT INTO recovery_records(intent_id,owner,classification,budget,status,last_evidence,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (intent_id, owner, classification, budget, "active", json.dumps(evidence, ensure_ascii=False, sort_keys=True) if evidence is not None else None, now(), now()))
                consumed = 0
            else:
                consumed = existing[3]
            if consumed >= budget:
                self.conn.execute("UPDATE recovery_records SET status='paused',updated_at=? WHERE intent_id=?", (now(), intent_id))
                self.conn.execute("UPDATE operation_intents SET status='paused',updated_at=? WHERE intent_id=?", (now(), intent_id))
                version = self._business_event(row[0], "intent", str(intent_id), "recovery_paused_budget_exhausted", {"owner": owner, "budget": budget, "consumed": consumed})
                return {"intent_id": intent_id, "status": "paused", "remaining": 0, "business_version": version}
            self.conn.execute("UPDATE recovery_records SET consumed=consumed+1,last_evidence=?,updated_at=? WHERE intent_id=?", (json.dumps(evidence, ensure_ascii=False, sort_keys=True) if evidence is not None else None, now(), intent_id))
            version = self._business_event(row[0], "intent", str(intent_id), "recovery_attempt_recorded", {"classification": classification, "owner": owner, "remaining": budget - consumed - 1})
            return {"intent_id": intent_id, "status": "active", "remaining": budget - consumed - 1, "business_version": version}

    def record_managed_turn(self, run_id, identity, failure_class, status="started",
                            *, previous_turn_id=None, operation_intent_id=None,
                            checkpoint=None, terminal_event=None, history_readback=None,
                            output_evidence=None, side_effect_evidence=None):
        """Persist one formal turn receipt; repeated writes are idempotent."""
        required = ("task_id", "attempt_id", "formal_thread_id", "host_id", "turn_id")
        if not isinstance(identity, dict) or any(not isinstance(identity.get(k), str) or not identity[k].strip() for k in required):
            raise ValueError("managed turn requires formal task, thread, host and turn identity")
        if not isinstance(failure_class, str) or not failure_class.strip() or not isinstance(status, str) or not status.strip():
            raise ValueError("managed turn class and status are required")
        turn_key = f"{run_id}:{identity['formal_thread_id']}:{identity['turn_id']}"
        values = {
            "checkpoint": checkpoint or {}, "terminal_event": terminal_event or {},
            "history_readback": history_readback or {}, "output_evidence": output_evidence or [],
            "side_effect_evidence": side_effect_evidence or [],
        }
        with self.transaction():
            existing = self.conn.execute("SELECT * FROM managed_turns WHERE turn_key=?", (turn_key,)).fetchone()
            if existing:
                if (existing["run_id"], existing["formal_thread_id"], existing["turn_id"]) != (run_id, identity["formal_thread_id"], identity["turn_id"]):
                    raise ValueError("managed turn key conflicts with existing identity")
                return {"turn": dict(existing), "created": False}
            stamp = now()
            self.conn.execute(
                "INSERT INTO managed_turns(turn_key,run_id,task_id,attempt_id,formal_thread_id,host_id,turn_id,previous_turn_id,operation_intent_id,failure_class,status,checkpoint,terminal_event,history_readback,output_evidence,side_effect_evidence,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (turn_key, run_id, identity["task_id"], identity["attempt_id"], identity["formal_thread_id"], identity["host_id"], identity["turn_id"], previous_turn_id, operation_intent_id, failure_class, status, json.dumps(values["checkpoint"], ensure_ascii=False, sort_keys=True), json.dumps(values["terminal_event"], ensure_ascii=False, sort_keys=True), json.dumps(values["history_readback"], ensure_ascii=False, sort_keys=True), json.dumps(values["output_evidence"], ensure_ascii=False, sort_keys=True), json.dumps(values["side_effect_evidence"], ensure_ascii=False, sort_keys=True), stamp, stamp),
            )
            self._business_event(run_id, "managed_turn", turn_key, "managed_turn_recorded", {"turn_id": identity["turn_id"], "failure_class": failure_class, "status": status})
            return {"turn": dict(self.conn.execute("SELECT * FROM managed_turns WHERE turn_key=?", (turn_key,)).fetchone()), "created": True}

    def update_managed_turn(self, run_id, formal_thread_id, turn_id, *, failure_class=None,
                            status=None, terminal_event=None, history_readback=None,
                            output_evidence=None, side_effect_evidence=None, checkpoint=None):
        turn_key = f"{run_id}:{formal_thread_id}:{turn_id}"
        with self.transaction():
            row = self.conn.execute("SELECT * FROM managed_turns WHERE turn_key=?", (turn_key,)).fetchone()
            if row is None:
                raise ValueError("unknown managed turn")
            updates = {
                "failure_class": failure_class if failure_class is not None else row["failure_class"],
                "status": status if status is not None else row["status"],
                "terminal_event": terminal_event if terminal_event is not None else json.loads(row["terminal_event"]),
                "history_readback": history_readback if history_readback is not None else json.loads(row["history_readback"]),
                "output_evidence": output_evidence if output_evidence is not None else json.loads(row["output_evidence"]),
                "side_effect_evidence": side_effect_evidence if side_effect_evidence is not None else json.loads(row["side_effect_evidence"]),
                "checkpoint": checkpoint if checkpoint is not None else json.loads(row["checkpoint"]),
            }
            self.conn.execute("UPDATE managed_turns SET failure_class=?,status=?,terminal_event=?,history_readback=?,output_evidence=?,side_effect_evidence=?,checkpoint=?,updated_at=? WHERE turn_key=?", (updates["failure_class"], updates["status"], json.dumps(updates["terminal_event"], ensure_ascii=False, sort_keys=True), json.dumps(updates["history_readback"], ensure_ascii=False, sort_keys=True), json.dumps(updates["output_evidence"], ensure_ascii=False, sort_keys=True), json.dumps(updates["side_effect_evidence"], ensure_ascii=False, sort_keys=True), json.dumps(updates["checkpoint"], ensure_ascii=False, sort_keys=True), now(), turn_key))
            self._business_event(run_id, "managed_turn", turn_key, "managed_turn_updated", {"turn_id": turn_id, "failure_class": updates["failure_class"], "status": updates["status"]})
            return dict(self.conn.execute("SELECT * FROM managed_turns WHERE turn_key=?", (turn_key,)).fetchone())

    def managed_turn(self, run_id, formal_thread_id, turn_id):
        row = self.conn.execute("SELECT * FROM managed_turns WHERE turn_key=? AND run_id=?", (f"{run_id}:{formal_thread_id}:{turn_id}", run_id)).fetchone()
        if row is None:
            raise ValueError("unknown managed turn")
        return dict(row)

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

    def claim_action(self, action_id, owner_id, lease_seconds=60, supports_fencing=False, outcome_reconciled=False):
        if not isinstance(owner_id, str) or not owner_id.strip() or not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or lease_seconds < 1:
            raise ValueError("owner_id and positive lease_seconds are required")
        expires = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
        with self.transaction():
            action = self.conn.execute("SELECT run_id,status FROM actions WHERE action_id=?", (action_id,)).fetchone()
            if action is None:
                raise ValueError("unknown action")
            existing = self.conn.execute("SELECT * FROM action_claims WHERE action_id=?", (action_id,)).fetchone()
            if existing:
                expired = datetime.fromisoformat(existing["lease_expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc)
                if not expired:
                    raise ActionClaimConflict(f"action {action_id} is owned by {existing['owner_id']}")
                if not (supports_fencing and outcome_reconciled):
                    raise UnsafeLeaseTakeover("expired action claim needs fencing and reconciliation")
                self.conn.execute("DELETE FROM action_claims WHERE action_id=?", (action_id,))
            stamp = now()
            self.conn.execute("INSERT INTO action_claims(action_id,owner_id,claimed_at,lease_expires_at,supports_fencing,outcome_reconciled) VALUES(?,?,?,?,?,?)", (action_id, owner_id, stamp, expires, int(bool(supports_fencing)), int(bool(outcome_reconciled))))
            self.conn.execute("UPDATE actions SET status='running',updated_at=? WHERE action_id=?", (stamp, action_id))
            self.conn.execute("INSERT INTO events(run_id,entity_type,entity_id,event_type,payload,observed_at) VALUES(?,?,?,?,?,?)", (action["run_id"], "action", str(action_id), "action_claimed", json.dumps({"owner_id": owner_id, "lease_expires_at": expires}), now()))
            return dict(self.conn.execute("SELECT * FROM action_claims WHERE action_id=?", (action_id,)).fetchone())

    def assert_action_effect_permitted(self, action_id, owner_id):
        claim = self.conn.execute("SELECT * FROM action_claims WHERE action_id=?", (action_id,)).fetchone()
        if claim is None or claim["owner_id"] != owner_id:
            raise ActionClaimConflict("action effect is not owned by this executor")
        if datetime.fromisoformat(claim["lease_expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
            raise UnsafeLeaseTakeover("action effect permission expired")
        return dict(claim)
    def finish_action(self,action_id,status,result=None,error=None,expected_version=None,gate=None):
        with self.transaction():
            row=self.conn.execute("SELECT run_id,kind,target FROM actions WHERE action_id=?",(action_id,)).fetchone()
            if row is None: raise ValueError("unknown action")
            run_id=row[0]; self._check_version(run_id, expected_version)
            if status == "succeeded":
                verify_terminal_contract(gate, entity_type="action", run_id=run_id, target_id=str(action_id), expected_version=self.business_version(run_id))
                self._record_verified_gate(run_id, "action", str(action_id), gate)
            self.conn.execute("UPDATE actions SET status=?,result=?,error=?,attempts=attempts+1,updated_at=? WHERE action_id=?",(status,json.dumps(result,ensure_ascii=False) if result is not None else None,error,now(),action_id))
            if status in {"succeeded", "failed", "blocked", "cancelled"}:
                self.conn.execute("DELETE FROM action_claims WHERE action_id=?", (action_id,))
            current = self.conn.execute("SELECT current_action,recovery_action FROM runs WHERE run_id=?", (run_id,)).fetchone()
            action_ref = f"{row[1]}:{row[2]}"
            if status in {"succeeded", "cancelled"} and current is not None:
                self.conn.execute(
                    "UPDATE runs SET current_action=CASE WHEN current_action=? THEN NULL ELSE current_action END, "
                    "recovery_action=CASE WHEN recovery_action=? THEN NULL ELSE recovery_action END, updated_at=? WHERE run_id=?",
                    (action_ref, row[1], now(), run_id),
                )
            elif status == "blocked" and current is not None:
                # A blocked recovery remains resumable, but no longer owns the
                # active execution slot.  The recovery kind is the durable
                # resume intent and is intentionally retained.
                self.conn.execute(
                    "UPDATE runs SET current_action=CASE WHEN current_action=? THEN NULL ELSE current_action END, "
                    "recovery_action=COALESCE(recovery_action,?), updated_at=? WHERE run_id=?",
                    (action_ref, row[1], now(), run_id),
                )
            self._business_event(run_id,"action",str(action_id),"action_finished",{"status":status,"result":result,"error":error})
    def add_spec(self,run_id,spec_id,title,position,blocked_by=None,acceptance=None,expected_version=None):
        with self.transaction():
            self._check_version(run_id, expected_version)
            self.conn.execute("INSERT INTO specs(spec_id,run_id,title,status,position,blocked_by,acceptance) VALUES(?,?,?,?,?,?,?)",(spec_id,run_id,title,"planned",position,json.dumps(blocked_by or []),json.dumps(acceptance or [])))
            self._business_event(run_id,"spec",spec_id,"spec_created",{"title":title})
    def set_route_summary(self, spec_id, summary, expected_version=None):
        """Persist a validated planned route, never an applied-route claim."""
        from route_summary import validate_route_summary
        row = self.conn.execute("SELECT run_id FROM specs WHERE spec_id=?", (spec_id,)).fetchone()
        if row is None:
            raise ValueError("unknown spec")
        checked = validate_route_summary(summary, spec_id=spec_id)
        run_id = row[0]
        with self.transaction():
            self._check_version(run_id, expected_version)
            self.conn.execute(
                "UPDATE specs SET route_summary=?,route_summary_digest=?,route_summary_status=? WHERE spec_id=?",
                (json.dumps(checked, ensure_ascii=False, sort_keys=True), checked["summary_digest"], "verified", spec_id),
            )
            self._business_event(run_id, "spec", spec_id, "spec_route_summary_recorded", {
                "summary_digest": checked["summary_digest"], "planned_model": checked["planned_model"],
                "planned_effort": checked["planned_effort"],
            })
            return checked

    def route_summary(self, spec_id):
        row = self.conn.execute("SELECT route_summary,route_summary_digest,route_summary_status FROM specs WHERE spec_id=?", (spec_id,)).fetchone()
        if row is None:
            raise ValueError("unknown spec")
        try:
            value = json.loads(row[0] or "{}")
        except (TypeError, json.JSONDecodeError):
            value = {}
        if row[1] and value.get("summary_digest") != row[1]:
            raise ValueError("route summary digest mismatch")
        value["status"] = row[2]
        return value

    def set_applied_route(self, thread_id, route_readback, expected_version=None):
        """Record configured route evidence only after an independent readback."""
        if not isinstance(route_readback, dict) or not route_readback.get("model") or not route_readback.get("effort"):
            raise ValueError("applied route readback is incomplete")
        row = self.conn.execute("SELECT run_id FROM threads WHERE thread_id=?", (thread_id,)).fetchone()
        if row is None:
            raise ValueError("unknown thread")
        run_id = row[0]
        with self.transaction():
            self._check_version(run_id, expected_version)
            self.conn.execute("UPDATE threads SET route_readback=?,last_observed_at=? WHERE thread_id=?", (json.dumps(route_readback, ensure_ascii=False, sort_keys=True), now(), thread_id))
            version = self._business_event(run_id, "thread", thread_id, "applied_route_readback_recorded", {"route_readback": route_readback})
            return {"thread_id": thread_id, "route_readback": route_readback, "business_version": version}
    def add_ticket(self,spec_id,ticket_id,title,blocked_by=None,issue_url=None,queue_position=None,expected_version=None):
        run_id=self.conn.execute("SELECT run_id FROM specs WHERE spec_id=?",(spec_id,)).fetchone()[0]
        with self.transaction():
            self._check_version(run_id, expected_version)
            if queue_position is None:
                row=self.conn.execute("SELECT COALESCE(MAX(queue_position),0)+1 FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE s.run_id=?",(run_id,)).fetchone()
                queue_position=row[0]
            if self.conn.execute("SELECT 1 FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE s.run_id=? AND t.queue_position=?",(run_id,queue_position)).fetchone():
                raise ValueError(f"queue_position already used in run: {queue_position}")
            local_rows = self.conn.execute(
                "SELECT ticket_id FROM tickets WHERE spec_id=? ORDER BY queue_position,ticket_id",
                (spec_id,),
            ).fetchall()
            local_ticket_ids = {position: row[0] for position, row in enumerate(local_rows, 1)}
            normalized_blockers = _normalize_spec_local_blockers(
                spec_id, blocked_by or [], local_ticket_ids
            )
            self.conn.execute("INSERT INTO tickets(ticket_id,spec_id,title,status,blocked_by,issue_url,queue_position) VALUES(?,?,?,?,?,?,?)",(ticket_id,spec_id,title,"planned",json.dumps(normalized_blockers),issue_url,queue_position))
            self._business_event(run_id,"ticket",ticket_id,"ticket_created",{"spec_id":spec_id,"queue_position":queue_position})

    def reconcile_ticket_blockers(self, ticket_id, blocked_by, expected_version=None, evidence=None):
        """Replace legacy blocker identities with verified run-owned tickets."""
        if not isinstance(blocked_by, list) or any(not isinstance(item, str) or not item.strip() for item in blocked_by):
            raise ValueError("blocked_by must be a list of non-empty strings")
        row = self.conn.execute(
            "SELECT t.spec_id,s.run_id FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE t.ticket_id=?",
            (ticket_id,),
        ).fetchone()
        if row is None:
            raise ValueError("unknown ticket")
        run_id = row[1]
        if ticket_id in blocked_by:
            raise ValueError("ticket cannot block itself")
        from dependencies import resolve_ticket_alias
        declared_blocked_by = list(blocked_by)
        resolved_blocked_by = [
            resolve_ticket_alias(self, ticket_id, item) if "/" in item else item
            for item in declared_blocked_by
        ]
        placeholders = ",".join("?" for _ in blocked_by) or "NULL"
        present = self.conn.execute(
            f"SELECT t.ticket_id FROM tickets t JOIN specs s ON s.spec_id=t.spec_id "
            f"WHERE s.run_id=? AND t.ticket_id IN ({placeholders})",
            [run_id, *resolved_blocked_by],
        ).fetchall()
        known = {item[0] for item in present}
        unknown = [item for item in resolved_blocked_by if item not in known]
        if unknown:
            raise ValueError(f"unknown run-owned blocker(s): {', '.join(unknown)}")
        with self.transaction():
            self._check_version(run_id, expected_version)
            current = self.conn.execute("SELECT blocked_by FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
            current_value = json.loads(current[0] or "[]")
            if current_value == resolved_blocked_by:
                return {"ticket_id": ticket_id, "changed": False, "business_version": self.business_version(run_id)}
            self.conn.execute("UPDATE tickets SET blocked_by=? WHERE ticket_id=?", (json.dumps(resolved_blocked_by, ensure_ascii=False), ticket_id))
            version = self._business_event(run_id, "ticket", ticket_id, "ticket_dependencies_reconciled", {
                "previous": current_value, "declared_blocked_by": declared_blocked_by,
                "blocked_by": resolved_blocked_by, "evidence": evidence or [],
            })
            return {"ticket_id": ticket_id, "changed": True, "business_version": version}
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
            local_ticket_groups = {}
            for position, ticket_id in enumerate(queue, 1):
                entry = by_id[ticket_id]
                local_ticket_groups.setdefault(entry.get("spec_id"), []).append((position, ticket_id))
            local_ticket_ids = {
                spec_id: {
                    local_position: ticket_id
                    for local_position, (_, ticket_id) in enumerate(items, 1)
                }
                for spec_id, items in local_ticket_groups.items()
            }
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
                if not isinstance(blockers, list):
                    raise ValueError(f"ticket ledger entry {ticket_id} has invalid blockers")
                blockers = _normalize_spec_local_blockers(
                    spec_id, blockers, local_ticket_ids.get(spec_id, {})
                )
                if any(item not in queue for item in blockers):
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
                self.conn.execute("INSERT INTO thread_bootstraps(thread_id,run_id,state,budget,created_at,updated_at) VALUES(?,?,?,?,?,?)", (thread_id, run_id, "bootstrap", 3, now(), now()))
                self._business_event(run_id,"thread",thread_id,"thread_registered",{"kind":kind,"spec_id":spec_id,"task_id":fields["task_id"],"attempt_id":fields["attempt_id"]})
            else:
                self._business_event(run_id,"thread",thread_id,"thread_registration_refused",{"kind":kind,"task_id":fields["task_id"],"attempt_id":fields["attempt_id"]})
        return inserted
    def initialize_test_train(self, run_id, spec_ids, checkpoint_size=10, expected_version=None):
        if not isinstance(spec_ids, list) or not spec_ids or any(not isinstance(item, str) or not item.strip() for item in spec_ids) or len(set(spec_ids)) != len(spec_ids):
            raise ValueError("test train requires an ordered, unique SPEC list")
        if checkpoint_size != 10:
            raise ValueError("checkpoint_size is fixed at 10")
        with self.transaction():
            current = self._check_version(run_id, expected_version)
            actual = [row[0] for row in self.conn.execute("SELECT spec_id FROM specs WHERE run_id=? ORDER BY position", (run_id,))]
            if actual != spec_ids:
                raise ValueError("test train SPEC membership does not match the ordered run plan")
            existing = self.conn.execute("SELECT COUNT(*) FROM test_train_obligations WHERE run_id=?", (run_id,)).fetchone()[0]
            if existing:
                return {"run_id": run_id, "changed": False, "spec_count": len(spec_ids)}
            stamp = now()
            for spec_id in spec_ids:
                for level in ("L0", "L1", "L2"):
                    self.conn.execute("INSERT INTO test_train_obligations(run_id,spec_id,level,required,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (run_id, spec_id, level, 1, "pending", stamp, stamp))
            for sequence, start in enumerate(range(0, len(spec_ids), checkpoint_size), 1):
                members = spec_ids[start:start + checkpoint_size]
                self.conn.execute("INSERT INTO test_train_checkpoints(run_id,sequence,start_position,end_position,members,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (run_id, sequence, start + 1, start + len(members), json.dumps(members, ensure_ascii=False), "pending", stamp, stamp))
            version = self._business_event(run_id, "test-train", run_id, "test_train_initialized", {"spec_count": len(spec_ids), "checkpoint_size": checkpoint_size, "checkpoint_count": (len(spec_ids) + 9) // 10})
            return {"run_id": run_id, "changed": True, "spec_count": len(spec_ids), "business_version": version}

    def policy(self, run_id):
        row = self.conn.execute("SELECT canonical_payload,policy_digest,implementation_digest,status FROM run_policies WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise PolicyError("policy_missing", {"run_id": run_id})
        return {"payload": json.loads(row[0]), "policy_digest": row[1], "implementation_digest": row[2], "status": row[3]}

    def pin_policy(self, run_id, payload, implementation_digest=None, expected_version=None, migration=None):
        if implementation_digest is None and isinstance(payload, dict):
            implementation_digest = payload.get("skill_bundle_digest") or hashlib.sha256(_canonical_json(payload, "policy").encode()).hexdigest()
        if not isinstance(payload, dict) or not payload or not isinstance(implementation_digest, str) or not implementation_digest.strip():
            raise PolicyError("policy_identity_invalid")
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self.transaction():
            current = self._check_version(run_id, expected_version)
            existing = self.conn.execute("SELECT policy_digest,implementation_digest,status FROM run_policies WHERE run_id=?", (run_id,)).fetchone()
            if existing is not None and existing[0] == digest and existing[1] == implementation_digest:
                return {"run_id": run_id, "changed": False, "policy_digest": digest, "implementation_digest": implementation_digest, "business_version": current}
            if existing is not None:
                required = {"compatibility", "authorization", "rollback"}
                if not isinstance(migration, dict) or not required.issubset(migration) or any(not migration[key] for key in required):
                    raise PolicyError("policy_migration_evidence_required", {"required": sorted(required)})
            stamp = now()
            self.conn.execute("INSERT INTO run_policies(run_id,canonical_payload,policy_digest,implementation_digest,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET canonical_payload=excluded.canonical_payload,policy_digest=excluded.policy_digest,implementation_digest=excluded.implementation_digest,status=excluded.status,updated_at=excluded.updated_at", (run_id, canonical, digest, implementation_digest, "pinned", stamp, stamp))
            self.conn.execute("UPDATE run_policies SET rules_version=? WHERE run_id=?", (str(payload.get("rules_version", "")), run_id))
            version = self._business_event(run_id, "policy", run_id, "policy_pinned" if existing is None else "policy_migrated", {"policy_digest": digest, "implementation_digest": implementation_digest, "migration": migration})
            return {"run_id": run_id, "changed": True, "policy_digest": digest, "implementation_digest": implementation_digest, "business_version": version}

    def verify_policy(self, run_id, loaded_payload, loaded_implementation_digest):
        record = self.policy(run_id)
        canonical = json.dumps(loaded_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if isinstance(loaded_payload, dict) else ""
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest() if canonical else None
        if digest != record["policy_digest"] or loaded_implementation_digest != record["implementation_digest"]:
            raise PolicyError("policy_digest_mismatch", {"expected_policy_digest": record["policy_digest"], "loaded_policy_digest": digest, "expected_implementation_digest": record["implementation_digest"], "loaded_implementation_digest": loaded_implementation_digest})
        return {"decision": "allow", "policy_digest": digest, "implementation_digest": loaded_implementation_digest}

    @staticmethod
    def _canonical_json(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _digest_json(cls, value):
        return hashlib.sha256(cls._canonical_json(value).encode("utf-8")).hexdigest()

    def _evidence_snapshot(self, run_id):
        rows = self.conn.execute(
            "SELECT evidence_id,entity_type,entity_id,evidence_kind,payload "
            "FROM evidence_refs WHERE run_id=? ORDER BY evidence_id", (run_id,)
        ).fetchall()
        return [
            {
                "evidence_id": int(row[0]),
                "entity_type": row[1],
                "entity_id": row[2],
                "evidence_kind": row[3],
                "payload_digest": self._digest_json(json.loads(row[4])),
            }
            for row in rows
        ]

    def create_backup_manifest(
        self,
        run_id,
        file_digests=None,
        database_digest=None,
        expected_version=None,
    ):
        """Persist a tamper-evident description of the local recovery point.

        ``file_digests`` is deliberately metadata, not a file-copy operation:
        callers must hash the exact files they intend to back up and provide
        those hashes.  The manifest never claims that an external system was
        rolled back.
        """
        if file_digests is None:
            file_digests = {}
        if not isinstance(file_digests, dict) or any(
            not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip()
            for key, value in file_digests.items()
        ):
            raise ValueError("file_digests must be a path-to-digest object")
        if database_digest is not None and (not isinstance(database_digest, str) or not database_digest.strip()):
            raise ValueError("database_digest must be a non-empty string")
        policy_row = self.conn.execute(
            "SELECT policy_digest,implementation_digest FROM run_policies WHERE run_id=?", (run_id,)
        ).fetchone()
        evidence = self._evidence_snapshot(run_id)
        payload = {
            "manifest_version": 1,
            "schema_version": SCHEMA_VERSION,
            "database_digest": database_digest,
            "file_digests": dict(sorted(file_digests.items())),
            "policy_identity": {
                "policy_digest": policy_row[0] if policy_row else None,
                "implementation_digest": policy_row[1] if policy_row else None,
            },
            "evidence_refs": evidence,
            "external_rollback": False,
        }
        manifest_digest = self._digest_json(payload)
        with self.transaction():
            current = self._check_version(run_id, expected_version)
            stamp = now()
            cur = self.conn.execute(
                "INSERT INTO backup_manifests(run_id,payload,manifest_digest,status,created_at) VALUES(?,?,?,?,?)",
                (run_id, self._canonical_json(payload), manifest_digest, "valid", stamp),
            )
            version = self._business_event(run_id, "backup-manifest", str(cur.lastrowid), "backup_manifest_created", {
                "manifest_digest": manifest_digest,
                "schema_version": SCHEMA_VERSION,
                "evidence_count": len(evidence),
                "external_rollback": False,
            })
            return {
                "manifest_id": cur.lastrowid,
                "manifest_digest": manifest_digest,
                "status": "valid",
                "business_version": version,
                "database_digest": database_digest,
                "evidence_count": len(evidence),
                "policy_digest": payload["policy_identity"]["policy_digest"],
            }

    def _manifest_row(self, manifest_id):
        row = self.conn.execute(
            "SELECT manifest_id,run_id,payload,manifest_digest,status FROM backup_manifests WHERE manifest_id=?",
            (manifest_id,),
        ).fetchone()
        if row is None:
            raise RestoreValidationError("manifest_missing", {"manifest_id": manifest_id})
        try:
            payload = json.loads(row[2])
        except (TypeError, json.JSONDecodeError):
            raise RestoreValidationError("manifest_payload_invalid", {"manifest_id": manifest_id}) from None
        return row, payload

    def validate_backup_manifest(
        self,
        run_id,
        manifest_id,
        file_digests=None,
        database_digest=None,
    ):
        row, payload = self._manifest_row(manifest_id)
        errors = []
        if row[1] != run_id:
            errors.append("manifest_run_mismatch")
        if row[4] != "valid":
            errors.append("manifest_not_valid")
        if self._digest_json(payload) != row[3]:
            errors.append("manifest_digest_mismatch")
        if payload.get("schema_version") != SCHEMA_VERSION:
            errors.append("schema_incompatible")
        if database_digest is not None and payload.get("database_digest") != database_digest:
            errors.append("database_digest_mismatch")
        if file_digests is not None and payload.get("file_digests") != dict(sorted(file_digests.items())):
            errors.append("file_digest_mismatch")
        policy = self.conn.execute(
            "SELECT policy_digest,implementation_digest FROM run_policies WHERE run_id=?", (run_id,)
        ).fetchone()
        identity = payload.get("policy_identity")
        if not isinstance(identity, dict):
            errors.append("policy_identity_missing")
        elif (
            (policy[0] if policy else None) != identity.get("policy_digest")
            or (policy[1] if policy else None) != identity.get("implementation_digest")
        ):
            errors.append("policy_identity_mismatch")
        expected_refs = payload.get("evidence_refs")
        actual_refs = self._evidence_snapshot(run_id)
        if not isinstance(expected_refs, list):
            errors.append("evidence_refs_invalid")
        else:
            actual_by_id = {item["evidence_id"]: item for item in actual_refs}
            for item in expected_refs:
                if not isinstance(item, dict) or not isinstance(item.get("evidence_id"), int):
                    errors.append("evidence_reference_invalid")
                    continue
                actual = actual_by_id.get(item["evidence_id"])
                if actual is None:
                    errors.append("evidence_unreachable")
                elif actual != item:
                    errors.append("evidence_changed")
        if errors:
            raise RestoreValidationError("restore_blocked", {"manifest_id": manifest_id, "errors": sorted(set(errors))})
        return {
            "decision": "allow",
            "manifest_id": manifest_id,
            "manifest_digest": row[3],
            "schema_version": payload["schema_version"],
            "policy_digest": identity.get("policy_digest"),
            "evidence_count": len(expected_refs),
            "external_rollback": False,
        }

    def begin_restore(self, run_id, manifest_id, expected_version=None):
        """Create a local restore record; readiness always starts pending reconciliation."""
        self.validate_backup_manifest(run_id, manifest_id)
        with self.transaction():
            current = self._check_version(run_id, expected_version)
            unknown = [
                int(row[0]) for row in self.conn.execute(
                    "SELECT intent_id FROM operation_intents WHERE run_id=? AND status='outcome_unknown' ORDER BY intent_id",
                    (run_id,),
                )
            ]
            stamp = now()
            evidence = {"unknown_intents": unknown, "external_rollback": False, "local_restore": True}
            cur = self.conn.execute(
                "INSERT INTO restore_records(run_id,manifest_id,status,reconciliation_status,evidence,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (run_id, manifest_id, "pending_reconciliation", "pending", self._canonical_json(evidence), stamp, stamp),
            )
            version = self._business_event(run_id, "restore", str(cur.lastrowid), "restore_started", evidence)
            return {"restore_id": cur.lastrowid, "status": "pending_reconciliation", "reconciliation_status": "pending", "unknown_intents": unknown, "business_version": version}

    def record_restore_reconciliation(self, restore_id, reconciliation_status, evidence, expected_version=None):
        if reconciliation_status not in {"pending", "complete", "blocked"}:
            raise ValueError("unsupported reconciliation status")
        if not isinstance(evidence, dict):
            raise ValueError("reconciliation evidence must be an object")
        row = self.conn.execute("SELECT run_id,status,reconciliation_status FROM restore_records WHERE restore_id=?", (restore_id,)).fetchone()
        if row is None:
            raise ValueError("unknown restore")
        if row[1] not in {"pending_reconciliation", "blocked", "ready"}:
            raise ValueError("restore is not reconcilable")
        unknown = self.conn.execute("SELECT COUNT(*) FROM operation_intents WHERE run_id=? AND status='outcome_unknown'", (row[0],)).fetchone()[0]
        if reconciliation_status == "complete" and unknown:
            raise RestoreValidationError("reconciliation_incomplete", {"unknown_intents": unknown})
        if reconciliation_status == "complete" and not evidence.get("verified"):
            raise RestoreValidationError("reconciliation_evidence_missing", {"required": "verified"})
        with self.transaction():
            current = self._check_version(row[0], expected_version)
            status = "ready" if reconciliation_status == "complete" else ("blocked" if reconciliation_status == "blocked" else "pending_reconciliation")
            self.conn.execute(
                "UPDATE restore_records SET status=?,reconciliation_status=?,evidence=?,updated_at=? WHERE restore_id=?",
                (status, reconciliation_status, self._canonical_json(evidence), now(), restore_id),
            )
            version = self._business_event(row[0], "restore", str(restore_id), "restore_reconciliation_recorded", {"status": status, "reconciliation_status": reconciliation_status, "evidence": evidence, "external_rollback": False})
            return {"restore_id": restore_id, "status": status, "reconciliation_status": reconciliation_status, "business_version": version}

    def test_train_status(self, run_id):
        obligations = [dict(row) for row in self.conn.execute("SELECT * FROM test_train_obligations WHERE run_id=? ORDER BY obligation_id", (run_id,))]
        checkpoints = [dict(row) for row in self.conn.execute("SELECT * FROM test_train_checkpoints WHERE run_id=? ORDER BY sequence", (run_id,))]
        due = []
        closed = {row[0] for row in self.conn.execute("SELECT spec_id FROM specs WHERE run_id=? AND status IN ('closed','cancelled')", (run_id,))}
        for checkpoint in checkpoints:
            members = json.loads(checkpoint["members"])
            if set(members).issubset(closed) and checkpoint["status"] != "passed":
                due.append(checkpoint["sequence"])
        return {"run_id": run_id, "initialized": bool(obligations), "obligations": obligations, "checkpoints": checkpoints, "due_checkpoints": due, "allow_next_segment": not due}

    def record_test_gate(self, run_id, spec_id, level, status, candidate_sha, evidence, expected_version=None):
        if level not in {"L0", "L1", "L2", "L3"} or status not in {"passed", "failed"}:
            raise ValueError("unsupported test gate")
        if not isinstance(candidate_sha, str) or not candidate_sha.strip() or not isinstance(evidence, list) or not evidence:
            raise ValueError("test gate requires candidate SHA and evidence")
        with self.transaction():
            current = self._check_version(run_id, expected_version)
            row = self.conn.execute("SELECT obligation_id,required FROM test_train_obligations WHERE run_id=? AND spec_id=? AND level=?", (run_id, spec_id, level)).fetchone()
            if row is None:
                if level == "L3":
                    self.conn.execute("INSERT INTO test_train_obligations(run_id,spec_id,level,required,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (run_id, spec_id, level, 1, "pending", now(), now()))
                    row = self.conn.execute("SELECT obligation_id,required FROM test_train_obligations WHERE run_id=? AND spec_id=? AND level=?", (run_id, spec_id, level)).fetchone()
                else:
                    raise ValueError("test train obligation missing")
            self.conn.execute("UPDATE test_train_obligations SET status=?,candidate_sha=?,evidence=?,updated_at=? WHERE obligation_id=?", (status, candidate_sha, json.dumps(evidence, ensure_ascii=False), now(), row[0]))
            version = self._business_event(run_id, "test-train", spec_id, "test_gate_recorded", {"level": level, "status": status, "candidate_sha": candidate_sha, "evidence": evidence})
            return {"run_id": run_id, "spec_id": spec_id, "level": level, "status": status, "business_version": version}

    def record_checkpoint(self, run_id, sequence, status, candidate_sha, evidence, expected_version=None):
        if status not in {"passed", "failed"} or not isinstance(candidate_sha, str) or not candidate_sha.strip() or not isinstance(evidence, list) or not evidence:
            raise ValueError("checkpoint requires status, candidate SHA and evidence")
        with self.transaction():
            self._check_version(run_id, expected_version)
            row = self.conn.execute("SELECT checkpoint_id FROM test_train_checkpoints WHERE run_id=? AND sequence=?", (run_id, sequence)).fetchone()
            if row is None:
                raise ValueError("unknown checkpoint")
            version = self._business_event(run_id, "test-train", str(sequence), "checkpoint_recorded", {"status": status, "candidate_sha": candidate_sha, "evidence": evidence})
            self.conn.execute("UPDATE test_train_checkpoints SET status=?,candidate_sha=?,evidence=?,updated_at=? WHERE checkpoint_id=?", (status, candidate_sha, json.dumps(evidence, ensure_ascii=False), now(), row[0]))
            return {"run_id": run_id, "sequence": sequence, "status": status, "business_version": version}
    def bootstrap(self, run_id, thread_id):
        row = self.conn.execute("SELECT * FROM thread_bootstraps WHERE run_id=? AND thread_id=?", (run_id, thread_id)).fetchone()
        if row is None:
            raise ValueError("unknown thread bootstrap")
        result = dict(row)
        for field in ("route_receipt", "assignment_receipt", "cancellation_receipt"):
            if result[field] is not None:
                result[field] = json.loads(result[field])
        return result

    def advance_bootstrap(self, run_id, thread_id, state, receipt=None, expected_version=None):
        allowed = {"bootstrap": {"route_verifying", "cancelled"}, "route_verifying": {"assigned", "bootstrap", "cancelled"}, "assigned": {"cancelled"}, "cancelled": set()}
        if state not in allowed:
            raise ValueError("unsupported bootstrap state")
        with self.transaction():
            current = self._check_version(run_id, expected_version)
            row = self.conn.execute("SELECT state,budget,attempts,route_receipt FROM thread_bootstraps WHERE run_id=? AND thread_id=?", (run_id, thread_id)).fetchone()
            if row is None:
                raise ValueError("unknown thread bootstrap")
            if state not in allowed.get(row[0], set()):
                raise ValueError(f"illegal bootstrap transition: {row[0]} -> {state}")
            if state == "route_verifying" and (not isinstance(receipt, dict) or receipt.get("status") != "verified"):
                raise ValueError("route verification requires verified receipt")
            if state == "assigned" and (row[3] is None or not isinstance(receipt, dict) or receipt.get("status") != "verified" or not receipt.get("assignment_id")):
                raise ValueError("assignment requires verified route and assignment receipt")
            if state == "route_verifying" and row[2] >= row[1]:
                raise ValueError("bootstrap repair budget exhausted")
            column = "route_receipt" if state == "route_verifying" else "assignment_receipt" if state == "assigned" else "cancellation_receipt"
            self.conn.execute(f"UPDATE thread_bootstraps SET state=?,attempts=attempts+?,{column}=?,updated_at=? WHERE thread_id=? AND run_id=?", (state, 1 if state == "route_verifying" else 0, json.dumps(receipt, ensure_ascii=False, sort_keys=True) if receipt is not None else None, now(), thread_id, run_id))
            version = self._business_event(run_id, "thread", thread_id, "thread_bootstrap_state_changed", {"from_state": row[0], "to_state": state, "receipt": receipt, "budget": row[1], "attempts": row[2] + (1 if state == "route_verifying" else 0)})
            return {"thread_id": thread_id, "state": state, "business_version": version}
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
    def recover_unregistered_bootstrap(
        self, run_id, thread_id, kind, identity, *, host_id, owner_id, cwd,
        project_id, title_token, creation_evidence, absence_evidence,
        no_execution_evidence, expected_version=None,
    ):
        """Retain an absent, never-assigned bootstrap as a non-archive tombstone."""
        if identity.run_id != run_id:
            raise ValueError("bootstrap identity run does not match recovery run")
        required_strings = {
            "thread_id": thread_id, "kind": kind, "host_id": host_id,
            "owner_id": owner_id, "cwd": cwd, "project_id": project_id,
            "title_token": title_token,
        }
        if any(not isinstance(value, str) or not value.strip()
               for value in required_strings.values()):
            raise ValueError("bootstrap tombstone identity is incomplete")

        def evidence_list(value, label, minimum=1):
            if (not isinstance(value, list) or len(value) < minimum
                    or any(not isinstance(item, str) or not item.strip() for item in value)):
                raise ValueError(f"bootstrap tombstone requires {label} evidence")
            return value

        creation_evidence = evidence_list(creation_evidence, "creation")
        absence_evidence = evidence_list(absence_evidence, "backend-absence", minimum=2)
        no_execution_evidence = evidence_list(
            no_execution_evidence, "no-execution", minimum=2
        )
        if not any("assignment_absent" in item for item in no_execution_evidence):
            raise ValueError("bootstrap tombstone requires assignment-absence evidence")
        if not any("managed_turn_absent" in item for item in no_execution_evidence):
            raise ValueError("bootstrap tombstone requires managed-turn-absence evidence")

        with self.transaction():
            self._check_version(run_id, expected_version)
            duplicate = self.conn.execute(
                "SELECT thread_id FROM threads WHERE thread_id=? OR formal_thread_id=? "
                "OR (run_id=? AND task_id=? AND attempt_id=?)",
                (thread_id, thread_id, run_id, identity.task_id, identity.attempt_id),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("bootstrap tombstone conflicts with an existing registry row")
            stamp = now()
            readback = {
                "classification": "backend_absent_after_create",
                "creation_evidence": creation_evidence,
                "absence_evidence": absence_evidence,
                "no_execution_evidence": no_execution_evidence,
                "archived": False,
            }
            self.conn.execute(
                "INSERT INTO threads(thread_id,run_id,kind,lifecycle,outcome,last_observed_at,"
                "task_id,attempt_id,nonce,formal_thread_id,host_id,owner_id,cwd,project_id,"
                "title_token,identity_readback) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (thread_id, run_id, kind, "tombstoned", "backend_absent_after_create",
                 stamp, identity.task_id, identity.attempt_id, identity.nonce, thread_id,
                 host_id, owner_id, cwd, project_id, title_token,
                 json.dumps(readback, ensure_ascii=False, sort_keys=True)),
            )
            cancellation = {
                "status": "verified",
                "classification": "backend_absent_after_create",
                "archived": False,
                "evidence": creation_evidence + absence_evidence + no_execution_evidence,
            }
            self.conn.execute(
                "INSERT INTO thread_bootstraps(thread_id,run_id,state,budget,attempts,"
                "cancellation_receipt,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (thread_id, run_id, "cancelled", 3, 0,
                 json.dumps(cancellation, ensure_ascii=False, sort_keys=True), stamp, stamp),
            )
            version = self._business_event(
                run_id, "thread", thread_id, "unregistered_bootstrap_tombstoned",
                {"task_id": identity.task_id, "attempt_id": identity.attempt_id,
                 "classification": "backend_absent_after_create", "archived": False,
                 "evidence": readback},
            )
            return {"thread_id": thread_id, "lifecycle": "tombstoned",
                    "outcome": "backend_absent_after_create",
                    "business_version": version}
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
        run_mode = self.conn.execute(
            "SELECT execution_mode FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if run_mode is None:
            raise ValueError("controller recovery run is missing")
        if run_mode[0] == "single-ticket-line":
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
            gates = ["formal_identity", "applied_route"]
            if run_mode[0] == "single-ticket-line":
                gates.insert(0, "ticket_ledger")
            self._business_event(run_id, "thread", thread_id, "controller_recovery_committed", {
                "identity": identity, "route": route_readback, "next_action": next_action,
                "gates": gates,
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

    def _record_delivery_proof_artifact(self, entity_type, entity_id, artifact_type, artifact_ref, evidence):
        if not all(isinstance(value, str) and value.strip() for value in (entity_type, entity_id, artifact_type, artifact_ref)) or not isinstance(evidence, list) or not evidence:
            raise ValueError("delivery proof fields are required")
        existing = self.conn.execute("SELECT * FROM delivery_proofs WHERE entity_type=? AND entity_id=? AND artifact_type=? AND artifact_ref=?", (entity_type, entity_id, artifact_type, artifact_ref)).fetchone()
        if existing:
            return {"proof": dict(existing), "created": False}
        with self.transaction():
            cur = self.conn.execute("INSERT INTO delivery_proofs(entity_type,entity_id,artifact_type,artifact_ref,evidence,observed_at) VALUES(?,?,?,?,?,?)", (entity_type, entity_id, artifact_type, artifact_ref, json.dumps(evidence), now()))
        return {"proof": dict(self.conn.execute("SELECT * FROM delivery_proofs WHERE proof_id=?", (cur.lastrowid,)).fetchone()), "created": True}

    def waive_dependency(self, dependent_type, dependent_id, blocker_type, blocker_id, reason, authorization_source, scope, evidence):
        values = (dependent_type, dependent_id, blocker_type, blocker_id, reason, authorization_source, scope)
        if any(not isinstance(value, str) or not value.strip() for value in values) or not isinstance(evidence, list) or not evidence:
            raise ValueError("dependency waiver fields are required")
        existing = self.conn.execute("SELECT * FROM dependency_waivers WHERE dependent_type=? AND dependent_id=? AND blocker_type=? AND blocker_id=?", values[:4]).fetchone()
        if existing:
            return {"waiver": dict(existing), "created": False}
        with self.transaction():
            cur = self.conn.execute("INSERT INTO dependency_waivers(dependent_type,dependent_id,blocker_type,blocker_id,reason,authorization_source,scope,evidence,observed_at) VALUES(?,?,?,?,?,?,?,?,?)", (*values, json.dumps(evidence), now()))
        return {"waiver": dict(self.conn.execute("SELECT * FROM dependency_waivers WHERE waiver_id=?", (cur.lastrowid,)).fetchone()), "created": True}

    def record_external_wait(self, external_request_id, run_id, event_cursor, wake_condition, next_safe_check_at, action_id=None):
        with self.transaction():
            self.conn.execute("INSERT OR REPLACE INTO external_waits(external_request_id,run_id,action_id,event_cursor,wake_condition,next_safe_check_at,status,poll_count,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (external_request_id, run_id, action_id, event_cursor, wake_condition, next_safe_check_at, "waiting", 0, now(), now()))
        return {"external_request_id": external_request_id, "status": "waiting"}

    def poll_external_wait(self, external_request_id, result, changed, evidence=None):
        row = self.conn.execute("SELECT * FROM external_waits WHERE external_request_id=?", (external_request_id,)).fetchone()
        if row is None: raise ValueError("unknown external wait")
        if not changed:
            self.conn.execute("UPDATE external_waits SET poll_count=poll_count+1,updated_at=? WHERE external_request_id=?", (now(), external_request_id))
            return {"external_request_id": external_request_id, "changed": False, "semantic_round": False, "result": result}
        with self.transaction():
            self.conn.execute("UPDATE external_waits SET status='changed',poll_count=poll_count+1,last_result_digest=?,updated_at=? WHERE external_request_id=?", (hashlib.sha256(_canonical_json(result).encode()).hexdigest(), now(), external_request_id))
            self.conn.execute("INSERT INTO events(run_id,entity_type,entity_id,event_type,payload,observed_at) VALUES(?,?,?,?,?,?)", (row["run_id"], "external_wait", external_request_id, "external_wait_changed", json.dumps({"evidence": evidence or []}), now()))
        return {"external_request_id": external_request_id, "changed": True, "semantic_round": True, "result": result}

    def record_runtime_observation(self, run_id, observation_key, entity_type, entity_id, phase, status,
                                   scope="run", unit="count", started_at=None, ended_at=None,
                                   duration_ms=None, source="controller", usage=None, metadata=None):
        if not isinstance(observation_key, str) or not observation_key.strip():
            raise ValueError("observation_key is required")
        if not isinstance(phase, str) or not phase.strip():
            raise ValueError("phase is required")
        if duration_ms is not None and unit == "count":
            unit = "milliseconds"
        if not isinstance(scope, str) or not scope.strip() or not isinstance(unit, str) or not unit.strip() or not isinstance(source, str) or not source.strip():
            raise ValueError("scope, unit, and source are required")
        if duration_ms is not None and (not isinstance(duration_ms, (int, float)) or duration_ms < 0):
            raise ValueError("duration_ms must be non-negative")
        usage = {} if usage is None else usage
        metadata = {} if metadata is None else metadata
        if not isinstance(usage, dict) or not isinstance(metadata, dict):
            raise ValueError("usage and metadata must be JSON objects")
        payload = {"phase": phase, "status": status, "scope": scope, "unit": unit, "started_at": started_at, "ended_at": ended_at, "duration_ms": duration_ms, "source": source, "usage": usage, "metadata": metadata}
        existing = self.conn.execute("SELECT * FROM runtime_observations WHERE observation_key=?", (observation_key,)).fetchone()
        if existing:
            current = dict(existing); current["usage"] = json.loads(current["usage"]); current["metadata"] = json.loads(current["metadata"])
            if existing["run_id"] != run_id or existing["entity_type"] != entity_type or existing["entity_id"] != entity_id or any(current[key] != value for key, value in payload.items()):
                raise ValueError("observation_key already exists with different data")
            return {"observation_id": existing["observation_id"], "created": False, **payload}
        stamp = now()
        with self.transaction():
            cur = self.conn.execute("INSERT INTO runtime_observations(run_id,observation_key,entity_type,entity_id,phase,status,scope,unit,started_at,ended_at,duration_ms,source,usage,metadata,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (run_id, observation_key, entity_type, entity_id, phase, status, scope, unit, started_at, ended_at, duration_ms, source, json.dumps(usage, ensure_ascii=False, sort_keys=True), json.dumps(metadata, ensure_ascii=False, sort_keys=True), stamp))
            self.conn.execute("INSERT INTO events(run_id,entity_type,entity_id,event_type,payload,observed_at) VALUES(?,?,?,?,?,?)", (run_id, entity_type, entity_id, "runtime_observation_recorded", json.dumps(payload, ensure_ascii=False, sort_keys=True), now()))
        return {"observation_id": cur.lastrowid, "created": True, **payload}
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
    def record_delivery_proof(self, *args, **kwargs):
        """Record delivery evidence for both supported controller contracts.

        The current business-evidence contract stores a proof in ``evidence_refs``
        and advances the run version.  Older dependency callers use the
        artifact-oriented contract backed by ``delivery_proofs``.  Keep that
        compatibility explicit and shape-gated so a telemetry-like value cannot
        accidentally satisfy either delivery gate.
        """
        if kwargs:
            expected_version = kwargs.pop("expected_version", None)
            if kwargs:
                raise TypeError("unexpected keyword arguments: " + ", ".join(sorted(kwargs)))
        else:
            expected_version = None
        if len(args) == 5:
            first, second, third, fourth, fifth = args
            run_exists = self.conn.execute("SELECT 1 FROM runs WHERE run_id=?", (first,)).fetchone() is not None
            if run_exists and isinstance(fourth, dict) and (fifth is None or isinstance(fifth, int)):
                return self._record_evidence(first, second, third, "delivery_proof", fourth, fifth)
            return self._record_delivery_proof_artifact(first, second, third, fourth, fifth)
        if len(args) == 4:
            first, second, third, fourth = args
            run_exists = self.conn.execute("SELECT 1 FROM runs WHERE run_id=?", (first,)).fetchone() is not None
            if run_exists:
                return self._record_evidence(first, second, third, "delivery_proof", fourth, expected_version)
            raise TypeError("record_delivery_proof requires either an artifact evidence contract or a run evidence contract")
        raise TypeError("record_delivery_proof expects 4 or 5 positional arguments")
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
                raise ValueError("ticket closure requires delivery evidence: structured commit evidence")
            if not _valid_evidence(resulting_tests, _TEST_SCHEMES):
                raise ValueError("ticket closure requires delivery evidence: structured test evidence")
            # The legacy dependency API predates explicit acceptance evidence;
            # preserve that narrow compatibility path.  A terminal contract
            # still requires acceptance evidence when the caller supplies a
            # gate for the current controller protocol.
            if gate is not None and not _valid_evidence(resulting_acceptance, _ACCEPTANCE_SCHEMES):
                raise ValueError("ticket closure requires delivery evidence: structured acceptance evidence")
        with self.transaction():
            self._check_version(row[4], expected_version)
            if status == "closed":
                if gate is not None or resulting_acceptance:
                    verify_terminal_contract(gate, entity_type="ticket", run_id=row[4], target_id=ticket_id, expected_version=self.business_version(row[4]), commits=resulting_commits, tests=resulting_tests)
                    self._record_verified_gate(row[4], "ticket", ticket_id, gate)
            self.conn.execute("UPDATE tickets SET status=?,commits=COALESCE(?,commits),tests=COALESCE(?,tests),acceptance=COALESCE(?,acceptance) WHERE ticket_id=?",(status,json.dumps(commits) if commits is not None else None,json.dumps(tests) if tests is not None else None,json.dumps(acceptance) if acceptance is not None else None,ticket_id)); self._business_event(row[4],"ticket",ticket_id,"ticket_state_changed",{"status":status,"commits":commits,"tests":tests,"acceptance":acceptance})
            if status == "closed":
                delivery_evidence = list(resulting_commits or []) + list(resulting_tests or [])
                self.conn.execute("INSERT OR IGNORE INTO delivery_proofs(entity_type,entity_id,artifact_type,artifact_ref,evidence,observed_at) VALUES(?,?,?,?,?,?)", ("ticket", ticket_id, "delivery", f"ticket:{ticket_id}:closed", json.dumps(delivery_evidence, ensure_ascii=False), now()))

    def advance_local_action(self, run_id, kind, target, next_status):
        if kind not in {"advance_spec", "advance_ticket"}:
            raise ValueError("unsupported local action")
        key = f"{run_id}:{kind}:{target}"
        with self.transaction():
            action = self.conn.execute("SELECT * FROM actions WHERE idempotency_key=?", (key,)).fetchone()
            if kind == "advance_spec":
                entity = self.conn.execute("SELECT run_id,status FROM specs WHERE spec_id=?", (target,)).fetchone()
                if not entity or entity["run_id"] != run_id:
                    raise ValueError("unknown spec")
                from transitions import SPEC_TRANSITIONS, transition
                transitions = SPEC_TRANSITIONS
            else:
                entity = self.conn.execute("SELECT s.run_id,t.status FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE t.ticket_id=?", (target,)).fetchone()
                if not entity or entity["run_id"] != run_id:
                    raise ValueError("unknown ticket")
                transitions = {"planned":{"ready","blocked"},"ready":{"implementing","blocked"},"implementing":{"verified","blocked"},"verified":{"merged","blocked"},"merged":{"closed"},"blocked":{"ready","implementing","cancelled"},"closed":set(),"cancelled":set()}
            if action and action["status"] == "succeeded":
                return int(action["action_id"])
            if action and entity["status"] == next_status:
                action_id = int(action["action_id"])
                self.conn.execute("UPDATE actions SET status='succeeded',result=?,error=NULL,attempts=attempts+1,updated_at=? WHERE action_id=?", (json.dumps({"next_status": next_status}), now(), action_id))
                self.conn.execute("UPDATE runs SET current_action=CASE WHEN current_action=? THEN NULL ELSE current_action END,updated_at=? WHERE run_id=?", (f"{kind}:{target}", now(), run_id))
                self._business_event(run_id, "action", str(action_id), "action_reconciled", {"status":"succeeded", "next_status":next_status})
                return action_id
            if action and action["status"] not in {"pending", "running"}:
                raise ValueError(f"local action cannot resume from {action['status']}")
            if action:
                action_id = int(action["action_id"])
            else:
                stamp = now()
                cursor = self.conn.execute("INSERT INTO actions(run_id,kind,target,status,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (run_id,kind,target,"pending",key,stamp,stamp))
                action_id = int(cursor.lastrowid)
                self.conn.execute("UPDATE runs SET current_action=?,updated_at=? WHERE run_id=?", (f"{kind}:{target}", stamp, run_id))
            if kind == "advance_spec":
                transition(transitions, entity["status"], next_status)
                self.conn.execute("UPDATE specs SET status=? WHERE spec_id=?", (next_status, target))
                self._business_event(run_id, "spec", target, "spec_state_changed", {"status": next_status})
            else:
                if next_status not in transitions.get(entity["status"], set()):
                    raise ValueError(f"illegal ticket transition: {entity['status']} -> {next_status}")
                self.conn.execute("UPDATE tickets SET status=? WHERE ticket_id=?", (next_status, target))
                self._business_event(run_id, "ticket", target, "ticket_state_changed", {"status": next_status})
            self.conn.execute("UPDATE actions SET status='succeeded',result=?,attempts=attempts+1,updated_at=? WHERE action_id=?", (json.dumps({"next_status":next_status}), now(), action_id))
            self.conn.execute("UPDATE runs SET current_action=CASE WHEN current_action=? THEN NULL ELSE current_action END,updated_at=? WHERE run_id=?", (f"{kind}:{target}", now(), run_id))
            self._business_event(run_id, "action", str(action_id), "action_finished", {"status":"succeeded", "error":None})
            return action_id
    def update_thread(self,run_id,thread_id,lifecycle,outcome="unknown",next_action=None,operation=None,readback=None,expected_version=None,gate=None):
        with self.transaction():
            self._check_version(run_id, expected_version)
            row=self.conn.execute("SELECT lifecycle FROM threads WHERE thread_id=? AND run_id=?",(thread_id,run_id)).fetchone()
            if not row: raise ValueError("unknown thread")
            from transitions import THREAD_TRANSITIONS, transition
            # Backend observations such as ``idle`` and ``notLoaded`` are not
            # controller lifecycle states.  They remain unarchived observations
            # until the archive operation and independent readback are verified.
            if not (lifecycle == "archived" and row[0] not in THREAD_TRANSITIONS):
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
    def decide(self,run_id,subject,selected,recommendation,evidence,rationale,actor,scope,source,authorization,expected_version=None):
        for value, field in ((subject, "subject"), (actor, "actor"), (source, "source"), (rationale, "rationale")):
            if not isinstance(value, str) or not value.strip():
                raise DecisionError("decision_field_missing", {"field": field})
        if not isinstance(scope, (dict, list, str)) or scope == {} or scope == [] or scope == "":
            raise DecisionError("decision_scope_missing")
        if not isinstance(evidence, list) or not evidence:
            raise DecisionError("decision_evidence_missing")
        if not isinstance(authorization, dict) or authorization.get("decision") != "allow":
            raise DecisionError("decision_authorization_required")
        authorization_digest_value = authorization.get("authorization_digest")
        if not isinstance(authorization_digest_value, str) or not authorization_digest_value.strip():
            raise DecisionError("decision_authorization_digest_missing")
        record = self.authorization(run_id)
        if record["status"] != "configured" or record["authorization_digest"] != authorization_digest_value:
            raise DecisionError("decision_authorization_mismatch", {"run_id": run_id})
        with self.transaction():
            self._check_version(run_id, expected_version)
            cur = self.conn.execute(
                "INSERT INTO decisions(run_id,subject,selected,recommendation,evidence,rationale,actor,scope,source,authorization_digest,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, subject, json.dumps(selected, ensure_ascii=False), json.dumps(recommendation, ensure_ascii=False), json.dumps(evidence, ensure_ascii=False), rationale, actor, json.dumps(scope, ensure_ascii=False, sort_keys=True), source, authorization_digest_value, now()),
            )
            version = self._business_event(run_id, "decision", subject, "controller_approved", {"selected": selected, "recommendation": recommendation, "evidence": evidence, "rationale": rationale, "actor": actor, "scope": scope, "source": source, "authorization_digest": authorization_digest_value})
            return {"decision_id": cur.lastrowid, "business_version": version}
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
        policy = None
        if run:
            try:
                policy = self.policy(run_id)
            except ValueError as exc:
                if str(exc) != "policy_missing":
                    raise
        return {"run":dict(run) if run else None,"authorization":self.authorization(run_id) if run else None,"startup_contract":startup,"policy":policy,"candidate":self.candidate_snapshot(run_id) if run else None,"specs":row("SELECT * FROM specs WHERE run_id=? ORDER BY position",(run_id,)),"tickets":row("SELECT t.* FROM tickets t JOIN specs s ON s.spec_id=t.spec_id WHERE s.run_id=?",(run_id,)),"threads":row("SELECT * FROM threads WHERE run_id=?",(run_id,)),"bootstraps":row("SELECT * FROM thread_bootstraps WHERE run_id=? ORDER BY thread_id",(run_id,)),"test_train": {"obligations": row("SELECT * FROM test_train_obligations WHERE run_id=? ORDER BY obligation_id", (run_id,)), "checkpoints": row("SELECT * FROM test_train_checkpoints WHERE run_id=? ORDER BY sequence", (run_id,))},"actions":row("SELECT * FROM actions WHERE run_id=? ORDER BY action_id",(run_id,)),"intents":row("SELECT * FROM operation_intents WHERE run_id=? ORDER BY intent_id",(run_id,)),"claims":row("SELECT c.* FROM intent_claims c JOIN operation_intents i ON i.intent_id=c.intent_id WHERE i.run_id=? ORDER BY claim_id",(run_id,)),"recovery":row("SELECT r.* FROM recovery_records r JOIN operation_intents i ON i.intent_id=r.intent_id WHERE i.run_id=? ORDER BY intent_id",(run_id,)),"decisions":row("SELECT * FROM decisions WHERE run_id=? ORDER BY decision_id",(run_id,)),"events":row("SELECT * FROM events WHERE run_id=? ORDER BY event_id",(run_id,)),"observations":row("SELECT * FROM observations WHERE run_id=? ORDER BY observation_id",(run_id,)),"evidence_refs":row("SELECT * FROM evidence_refs WHERE run_id=? ORDER BY evidence_id",(run_id,))}
