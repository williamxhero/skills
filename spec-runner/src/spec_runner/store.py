from __future__ import annotations

import json
import hashlib
import sqlite3
import os
import socket
import ctypes
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator

from .errors import RunnerError
from .recovery import recovery_diagnostic

SCHEMA_VERSION = "spec-runner-store/v1"


def now() -> str:
    return datetime.now(UTC).isoformat()


def _raise_if_control_database_busy(exc: sqlite3.OperationalError) -> None:
    message = str(exc)
    if "locked" in message.lower() or "busy" in message.lower():
        raise RunnerError(
            "control_database_busy",
            "Spec Runner control database is temporarily locked",
            details={"database_error": message},
        ) from exc


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # Windows accepts os.kill(pid, 0) without reliably proving that the
        # process still exists. Query the kernel handle and exit code instead.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        try:
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _lease_age_seconds(value: str) -> float | None:
    try:
        observed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, (datetime.now(UTC) - observed).total_seconds())


def _route_scope_key(value: str) -> str:
    scope = str(value or "").strip()
    if not scope or scope.lower() in {"unknown", "none", "null"} or len(scope) > 256:
        raise RunnerError(
            "route_scope_invalid",
            "shared route circuit requires an explicit, bounded route scope",
        )
    if any(ord(character) < 32 for character in scope):
        raise RunnerError("route_scope_invalid", "route scope contains a control character")
    return scope


def _route_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise RunnerError("route_time_invalid", "route circuit timestamp is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _route_timestamp(value: str | None) -> str:
    if value is None:
        return now()
    return _route_time(value).isoformat()


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    launch_key: str
    input_digest: str
    config_digest: str
    repository_path: str
    target_ref: str
    artifact_root: str
    backend_kind: str
    state: str
    current_step: str
    log_path: str
    created_at: str
    updated_at: str

    def public(self) -> dict[str, str]:
        return asdict(self)


class Store:
    def __init__(self, database_path: Path, connection: sqlite3.Connection):
        self.database_path = database_path
        self.connection = connection

    @classmethod
    def open(cls, control_root: Path, *, create: bool) -> "Store":
        database_path = control_root / "spec-runner.sqlite3"
        if not create and not database_path.is_file():
            raise RunnerError("unknown_control_root", "no Spec Runner database exists at control_root")
        if create:
            control_root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        store = cls(database_path, connection)
        try:
            if create:
                store._initialize()
            else:
                # A detached child creates the SQLite file before its schema
                # transaction commits. Treat that short window as not-ready so a
                # launcher can retry instead of surfacing a raw sqlite error.
                table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
                ).fetchone()
                if table is None:
                    raise RunnerError("control_not_ready", "Spec Runner control database schema is not ready")
        except sqlite3.OperationalError as exc:
            connection.close()
            _raise_if_control_database_busy(exc)
            raise
        except Exception:
            connection.close()
            raise
        return store

    def close(self) -> None:
        self.connection.close()

    def _initialize(self) -> None:
        with self.transaction():
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    launch_key TEXT NOT NULL UNIQUE,
                    input_digest TEXT NOT NULL,
                    config_digest TEXT NOT NULL,
                    repository_path TEXT NOT NULL,
                    target_ref TEXT NOT NULL,
                    artifact_root TEXT NOT NULL,
                    backend_kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    current_step TEXT NOT NULL,
                    log_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS steps (
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    step_name TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, step_name)
                );
                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    operation_kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    input_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workers (
                    worker_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    backend_kind TEXT NOT NULL,
                    external_thread_id TEXT,
                    external_turn_id TEXT,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS verifications (
                    receipt_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    stage_name TEXT NOT NULL DEFAULT '',
                    receipt_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, stage_name)
                );
                CREATE TABLE IF NOT EXISTS runtime_owners (
                    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                    pid INTEGER NOT NULL,
                    owner_token TEXT NOT NULL,
                    log_path TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS run_controls (
                    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                    requested_state TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runner_leases (
                    scope TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    owner_token TEXT NOT NULL,
                    pid INTEGER NOT NULL,
                    host TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS route_circuits (
                    route_scope TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    cooldown_until TEXT,
                    half_open_owner TEXT,
                    half_open_expires_at TEXT,
                    last_failure_fingerprint TEXT,
                    last_success_evidence TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    event_key TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS takeover_records (
                    takeover_key TEXT PRIMARY KEY,
                    report_digest TEXT NOT NULL,
                    frontier_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS takeover_transitions (
                    transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    takeover_key TEXT NOT NULL,
                    event_key TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS run_answers (
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    question_id TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    value_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, question_id)
                );
                CREATE TABLE IF NOT EXISTS production_spec_completions (
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    spec_key TEXT NOT NULL,
                    plan_digest TEXT NOT NULL,
                    delivery_digest TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, spec_key)
                );
                CREATE TABLE IF NOT EXISTS external_operations (
                    operation_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    operation_kind TEXT NOT NULL,
                    repository TEXT NOT NULL,
                    input_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    receipt_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recovery_episodes (
                    episode_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    operation_kind TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    same_thread_attempts INTEGER NOT NULL DEFAULT 0,
                    capacity_attempts INTEGER NOT NULL DEFAULT 0,
                    route_probe_attempts INTEGER NOT NULL DEFAULT 0,
                    clean_probe_attempts INTEGER NOT NULL DEFAULT 0,
                    migration_attempts INTEGER NOT NULL DEFAULT 0,
                    no_progress_attempts INTEGER NOT NULL DEFAULT 0,
                    retry_deadline TEXT,
                    wait_deadline TEXT,
                    last_verified_progress TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recovery_observations (
                    observation_id TEXT PRIMARY KEY,
                    episode_id TEXT NOT NULL REFERENCES recovery_episodes(episode_id),
                    fingerprint TEXT NOT NULL,
                    observation_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recovery_decisions (
                    decision_id TEXT PRIMARY KEY,
                    episode_id TEXT NOT NULL REFERENCES recovery_episodes(episode_id),
                    decision_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recovery_budget_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    episode_id TEXT NOT NULL REFERENCES recovery_episodes(episode_id),
                    counter_name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS continuation_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    spec_key TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    bundle_path TEXT NOT NULL,
                    bundle_digest TEXT NOT NULL,
                    input_revision TEXT NOT NULL,
                    workspace_identity_json TEXT NOT NULL,
                    last_verified_progress TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(run_id, spec_key, stage, generation)
                );
                CREATE TABLE IF NOT EXISTS thread_migrations (
                    migration_key TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    stage TEXT NOT NULL,
                    source_thread_id TEXT NOT NULL,
                    handover_digest TEXT NOT NULL,
                    input_revision TEXT NOT NULL,
                    owner_generation INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    successor_thread_id TEXT,
                    owner_worker_id TEXT,
                    handover_json TEXT,
                    successor_json TEXT,
                    uncertainty_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS migration_milestones (
                    migration_key TEXT NOT NULL REFERENCES thread_migrations(migration_key),
                    milestone TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(migration_key, milestone)
                );
                """
            )
            verification_sql = self.connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'verifications'"
            ).fetchone()[0]
            if "run_id TEXT NOT NULL UNIQUE" in verification_sql or "stage_name" not in verification_sql:
                self._migrate_verifications_v1()
            self.connection.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES('schema_version', ?)",
                (SCHEMA_VERSION,),
            )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield
            self.connection.commit()
        except sqlite3.OperationalError as exc:
            self.connection.rollback()
            _raise_if_control_database_busy(exc)
            raise
        except Exception:
            self.connection.rollback()
            raise

    def _migrate_verifications_v1(self) -> None:
        """Preserve the one-stage table as audit data before adding stage receipts."""
        legacy_table = "verifications_legacy_v1"
        if self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (legacy_table,)
        ).fetchone():
            raise RunnerError("store_migration_failed", "existing legacy verification table prevents safe migration")
        self.connection.execute(f"ALTER TABLE verifications RENAME TO {legacy_table}")
        self.connection.execute(
            """CREATE TABLE verifications (
                receipt_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                stage_name TEXT NOT NULL,
                receipt_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(run_id, stage_name)
            )"""
        )
        legacy_rows = self.connection.execute(f"SELECT * FROM {legacy_table}").fetchall()
        for row in legacy_rows:
            values = dict(row)
            receipt_json = values.get("receipt_json", "")
            stage_name = values.get("stage_name", "")
            # A pre-release build wrote values by ordinal after adding a column
            # with ALTER TABLE. Recover the JSON without inventing timestamps.
            if not receipt_json.lstrip().startswith("{") and str(values.get("created_at", "")).lstrip().startswith("{"):
                stage_name = receipt_json
                receipt_json = values["created_at"]
            try:
                receipt = json.loads(receipt_json)
            except (TypeError, json.JSONDecodeError):
                continue
            stage_name = str(receipt.get("stage") or stage_name or "legacy")
            created_at = values.get("created_at")
            if not isinstance(created_at, str) or created_at.lstrip().startswith("{"):
                created_at = now()
            self.connection.execute(
                """INSERT OR IGNORE INTO verifications(receipt_id, run_id, stage_name, receipt_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (f"migration:{values['run_id']}:{stage_name}", values["run_id"], stage_name, json.dumps(receipt, ensure_ascii=False, sort_keys=True), created_at),
            )

    @staticmethod
    def _record(row: sqlite3.Row | None) -> RunRecord | None:
        if row is None:
            return None
        return RunRecord(**dict(row))

    def find_by_launch_key(self, launch_key: str) -> RunRecord | None:
        return self._record(self.connection.execute("SELECT * FROM runs WHERE launch_key = ?", (launch_key,)).fetchone())

    def find_by_run_id(self, run_id: str) -> RunRecord | None:
        return self._record(self.connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone())

    def register_runtime(self, run_id: str, *, pid: int, owner_token: str, log_path: str) -> None:
        timestamp = now()
        with self.transaction():
            self.connection.execute(
                """INSERT OR REPLACE INTO runtime_owners(run_id, pid, owner_token, log_path, started_at, updated_at)
                   VALUES (?, ?, ?, ?, COALESCE((SELECT started_at FROM runtime_owners WHERE run_id = ?), ?), ?)""",
                (run_id, pid, owner_token, log_path, run_id, timestamp, timestamp),
            )

    def runtime_for_run(self, run_id: str) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM runtime_owners WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def request_control(self, run_id: str, requested_state: str) -> dict[str, object]:
        if requested_state not in {"pause_requested", "cancel_requested", "resume_requested"}:
            raise RunnerError("invalid_control", "unsupported run control request")
        timestamp = now()
        with self.transaction():
            if self.find_by_run_id(run_id) is None:
                raise RunnerError("unknown_run", f"run does not exist: {run_id}")
            previous = self.connection.execute("SELECT generation FROM run_controls WHERE run_id = ?", (run_id,)).fetchone()
            generation = int(previous[0]) + 1 if previous else 1
            self.connection.execute(
                "INSERT OR REPLACE INTO run_controls VALUES (?, ?, ?, ?)",
                (run_id, requested_state, generation, timestamp),
            )
            self._insert_event(
                run_id=run_id,
                event_key=f"control:{run_id}:{generation}",
                event_type="control_requested",
                payload={"requested_state": requested_state, "generation": generation},
            )
        return {"run_id": run_id, "requested_state": requested_state, "generation": generation, "updated_at": timestamp}

    def control_for_run(self, run_id: str) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM run_controls WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def submit_answer(self, *, run_id: str, question_id: str, value: object) -> dict[str, object]:
        return self._submit_answer(run_id=run_id, question_id=question_id, value=value, wake=False)

    def submit_answer_and_wake(self, *, run_id: str, question_id: str, value: object) -> dict[str, object]:
        """Commit an answer and its same-run resume intent atomically."""
        return self._submit_answer(run_id=run_id, question_id=question_id, value=value, wake=True)

    def _submit_answer(self, *, run_id: str, question_id: str, value: object, wake: bool) -> dict[str, object]:
        if not question_id or any(character.isspace() for character in question_id):
            raise RunnerError("invalid_answer", "question_id must be non-empty and contain no whitespace")
        record = self.find_by_run_id(run_id)
        if record is None:
            raise RunnerError("unknown_run", f"run does not exist: {run_id}")
        if record.state in {"cancelled", "completed", "failed", "blocked", "blocked_writer_busy"} and wake:
            raise RunnerError("cancelled_run", "cancelled runs cannot be revived by an answer")
        value_json = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        import hashlib

        value_digest = hashlib.sha256(value_json.encode("utf-8")).hexdigest()
        timestamp = now()
        with self.transaction():
            current = self.connection.execute("SELECT state FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if current is None:
                raise RunnerError("unknown_run", f"run does not exist: {run_id}")
            if wake and current[0] in {"cancelled", "completed", "failed", "blocked", "blocked_writer_busy"}:
                raise RunnerError("answer_run_not_waiting", "terminal runs cannot accept an answer")
            existing = self.connection.execute(
                "SELECT * FROM run_answers WHERE run_id = ? AND question_id = ?", (run_id, question_id)
            ).fetchone()
            if wake and current[0] != "needs_input" and existing is None:
                raise RunnerError("answer_run_not_waiting", "answers may wake only a run currently waiting for input")
            if existing:
                if existing["value_digest"] != value_digest:
                    raise RunnerError("answer_conflict", "question already has a different answer")
                answer = dict(existing)
            else:
                self.connection.execute(
                    "INSERT INTO run_answers VALUES (?, ?, ?, ?, ?, ?)",
                    (run_id, question_id, value_json, value_digest, timestamp, timestamp),
                )
                self._insert_event(
                    run_id=run_id,
                    event_key=f"answer:{run_id}:{question_id}:{value_digest}",
                    event_type="answer_submitted",
                    payload={"question_id": question_id, "value_digest": value_digest},
                )
                answer = {"run_id": run_id, "question_id": question_id, "value": value,
                          "value_digest": value_digest, "created_at": timestamp, "updated_at": timestamp}
            if wake and current[0] == "needs_input":
                previous = self.connection.execute("SELECT generation, requested_state FROM run_controls WHERE run_id = ?", (run_id,)).fetchone()
                if previous is None or previous["requested_state"] != "resume_requested":
                    generation = int(previous["generation"]) + 1 if previous else 1
                    self.connection.execute("INSERT OR REPLACE INTO run_controls VALUES (?, ?, ?, ?)",
                        (run_id, "resume_requested", generation, timestamp))
                    self._insert_event(run_id=run_id, event_key=f"control:{run_id}:{generation}",
                        event_type="control_requested", payload={"requested_state": "resume_requested", "generation": generation})
            return answer

    def answers_for_run(self, run_id: str) -> list[dict[str, object]]:
        answers = []
        for row in self.connection.execute("SELECT * FROM run_answers WHERE run_id = ? ORDER BY question_id", (run_id,)):
            item = dict(row)
            item["value"] = json.loads(str(item.pop("value_json")))
            answers.append(item)
        return answers

    def clear_control(self, run_id: str) -> None:
        with self.transaction():
            self.connection.execute("DELETE FROM run_controls WHERE run_id = ?", (run_id,))

    def set_run_state(self, run_id: str, state: str) -> None:
        with self.transaction():
            self.connection.execute("UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?", (state, now(), run_id))
            self._insert_event(
                run_id=run_id,
                event_key=f"state:{run_id}:{state}:{now()}",
                event_type="state_changed",
                payload={"state": state},
            )

    def complete_production_spec(self, *, run_id: str, spec_key: str,
                                 plan_digest: str, delivery_digest: str) -> None:
        """Atomically persist SPEC completion evidence and the queue state."""
        timestamp = now()
        with self.transaction():
            existing = self.connection.execute(
                "SELECT plan_digest, delivery_digest FROM production_spec_completions WHERE run_id = ? AND spec_key = ?",
                (run_id, spec_key),
            ).fetchone()
            if existing and (existing["plan_digest"] != plan_digest or existing["delivery_digest"] != delivery_digest):
                raise RunnerError("production_completion_conflict", "SPEC completion identity has conflicting evidence")
            self.connection.execute(
                "INSERT OR IGNORE INTO production_spec_completions VALUES (?, ?, ?, ?, ?)",
                (run_id, spec_key, plan_digest, delivery_digest, timestamp),
            )
            self.connection.execute("UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                                    ("spec_completed", timestamp, run_id))
            self._insert_event(run_id=run_id, event_key=f"production-spec:{run_id}:{spec_key}:completed",
                event_type="production_spec_completed", payload={"spec_key": spec_key,
                    "plan_digest": plan_digest, "delivery_digest": delivery_digest})

    def production_completed_specs(self, run_id: str) -> set[str]:
        return {str(row[0]) for row in self.connection.execute(
            "SELECT spec_key FROM production_spec_completions WHERE run_id = ?", (run_id,))}

    def prepare_external_operation(self, *, operation_id: str, run_id: str,
                                   operation_kind: str, repository: str,
                                   input_digest: str) -> dict[str, object]:
        timestamp = now()
        with self.transaction():
            existing = self.connection.execute("SELECT * FROM external_operations WHERE operation_id = ?",
                                                (operation_id,)).fetchone()
            identity = (run_id, operation_kind, repository, input_digest)
            if existing and tuple(existing[key] for key in ("run_id", "operation_kind", "repository", "input_digest")) != identity:
                raise RunnerError("external_operation_conflict", "external operation ID was reused with different identity")
            if not existing:
                self.connection.execute("INSERT INTO external_operations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (operation_id, run_id, operation_kind, repository, input_digest, "intent", None, timestamp, timestamp))
                self._insert_event(run_id=run_id, event_key=f"external:{operation_id}:intent",
                    event_type="external_operation_intent", payload={"operation_id": operation_id,
                        "operation_kind": operation_kind, "repository": repository, "input_digest": input_digest})
            row = self.connection.execute("SELECT * FROM external_operations WHERE operation_id = ?", (operation_id,)).fetchone()
            assert row is not None
            result = dict(row)
            if result.get("receipt_json"):
                result["receipt"] = json.loads(str(result.pop("receipt_json")))
            else:
                result.pop("receipt_json", None)
            return result

    def complete_external_operation(self, *, operation_id: str, receipt: dict[str, object]) -> dict[str, object]:
        timestamp = now()
        receipt_json = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
        with self.transaction():
            existing = self.connection.execute("SELECT * FROM external_operations WHERE operation_id = ?", (operation_id,)).fetchone()
            if existing is None:
                raise RunnerError("external_operation_missing", "external operation intent was not persisted")
            if existing["state"] == "completed" and existing["receipt_json"] != receipt_json:
                raise RunnerError("external_receipt_conflict", "completed external operation receipt changed")
            self.connection.execute("UPDATE external_operations SET state = ?, receipt_json = ?, updated_at = ? WHERE operation_id = ?",
                ("completed", receipt_json, timestamp, operation_id))
            self._insert_event(run_id=existing["run_id"], event_key=f"external:{operation_id}:completed",
                event_type="external_operation_completed", payload={"operation_id": operation_id,
                    "receipt_digest": hashlib.sha256(receipt_json.encode("utf-8")).hexdigest()})
            row = self.connection.execute("SELECT * FROM external_operations WHERE operation_id = ?", (operation_id,)).fetchone()
            assert row is not None
            result = dict(row)
            result["receipt"] = json.loads(str(result.pop("receipt_json")))
            return result

    def external_operation(self, operation_id: str) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM external_operations WHERE operation_id = ?", (operation_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        if result.get("receipt_json"):
            result["receipt"] = json.loads(str(result.pop("receipt_json")))
        return result

    def create_run(self, run: RunRecord, operation_id: str) -> None:
        with self.transaction():
            self.connection.execute(
                """INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                tuple(run.public().values()),
            )
            self.connection.execute(
                "INSERT INTO steps VALUES (?, ?, ?, ?, ?)",
                (run.run_id, run.current_step, "pending", run.created_at, run.updated_at),
            )
            self.connection.execute(
                "INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?)",
                (operation_id, run.run_id, f"{run.backend_kind}_stage", "intent", run.input_digest, run.created_at, run.updated_at),
            )
            self.connection.execute(
                "INSERT INTO workers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (f"{run.backend_kind}:{run.run_id}", run.run_id, run.backend_kind, None, None, "pending", run.created_at, run.updated_at),
            )
            self._insert_event(
                run_id=run.run_id,
                event_key=f"run:{run.run_id}:created",
                event_type="run_created",
                payload={"backend_kind": run.backend_kind, "operation_id": operation_id},
            )

    def complete_deterministic_stage(self, run_id: str, operation_id: str) -> RunRecord:
        timestamp = now()
        with self.transaction():
            self.connection.execute(
                "UPDATE operations SET state = ?, updated_at = ? WHERE operation_id = ?",
                ("completed", timestamp, operation_id),
            )
            self.connection.execute(
                "UPDATE workers SET state = ?, updated_at = ? WHERE worker_id = ?",
                ("completed", timestamp, f"deterministic_test:{run_id}"),
            )
            self.connection.execute(
                "UPDATE steps SET state = ?, updated_at = ? WHERE run_id = ? AND step_name = ?",
                ("completed_test_backend", timestamp, run_id, "deterministic_example"),
            )
            self.connection.execute(
                "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                ("completed_test_backend", timestamp, run_id),
            )
            self._insert_event(
                run_id=run_id,
                event_key=f"operation:{operation_id}:completed",
                event_type="step_completed",
                payload={"operation_id": operation_id, "state": "completed_test_backend"},
            )
        record = self.find_by_run_id(run_id)
        assert record is not None
        return record

    def complete_codex_stage(
        self, run_id: str, operation_id: str, *, thread_id: str, turn_id: str, state: str,
        step_name: str = "codex_example", worker_id: str | None = None,
        external_operation: tuple[str, dict[str, object]] | None = None,
    ) -> RunRecord:
        timestamp = now()
        with self.transaction():
            if external_operation is not None:
                external_id, receipt = external_operation
                receipt_json = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
                external = self.connection.execute(
                    "SELECT * FROM external_operations WHERE operation_id = ? AND run_id = ?",
                    (external_id, run_id),
                ).fetchone()
                if external is None:
                    raise RunnerError("external_operation_missing", "cannot complete stage without its external operation intent")
                if external["state"] == "completed" and external["receipt_json"] != receipt_json:
                    raise RunnerError("external_receipt_conflict", "completed external operation receipt changed")
                self.connection.execute(
                    "UPDATE external_operations SET state = ?, receipt_json = ?, updated_at = ? WHERE operation_id = ?",
                    ("completed", receipt_json, timestamp, external_id),
                )
                self._insert_event(run_id=run_id, event_key=f"external:{external_id}:completed",
                    event_type="external_operation_completed", payload={"operation_id": external_id,
                        "receipt_digest": hashlib.sha256(receipt_json.encode("utf-8")).hexdigest()})
            operation_updated = self.connection.execute(
                "UPDATE operations SET state = ?, updated_at = ? WHERE operation_id = ?",
                (state, timestamp, operation_id),
            ).rowcount
            if operation_updated != 1:
                raise RunnerError("operation_missing", "cannot complete a Codex stage without its durable operation")
            worker_updated = self.connection.execute(
                """UPDATE workers SET external_thread_id = ?, external_turn_id = ?, state = ?, updated_at = ?
                   WHERE worker_id = ?""",
                (thread_id, turn_id, state, timestamp, worker_id or f"codex_sdk:{run_id}"),
            ).rowcount
            if worker_updated != 1:
                raise RunnerError("worker_missing", "cannot complete a Codex stage without its durable worker")
            step_updated = self.connection.execute(
                "UPDATE steps SET state = ?, updated_at = ? WHERE run_id = ? AND step_name = ?",
                (state, timestamp, run_id, step_name),
            ).rowcount
            if step_updated != 1:
                raise RunnerError("step_missing", "cannot complete a Codex stage without its durable step")
            run_updated = self.connection.execute(
                "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                (state, timestamp, run_id),
            ).rowcount
            if run_updated != 1:
                raise RunnerError("run_missing", "cannot complete a Codex stage for an unknown run")
            self._insert_event(
                run_id=run_id,
                event_key=f"operation:{operation_id}:completed:{turn_id}",
                event_type=("step_completed" if state == "turn_completed" else ("worker_needs_input" if state == "needs_input" else "worker_turn_interrupted")),
                payload={"operation_id": operation_id, "state": state, "thread_id": thread_id, "turn_id": turn_id},
            )
        record = self.find_by_run_id(run_id)
        assert record is not None
        return record

    def reject_codex_stage(
        self, run_id: str, operation_id: str, *, thread_id: str, turn_id: str,
        step_name: str, worker_id: str, code: str,
    ) -> RunRecord:
        """Persist a terminal Codex result whose structured receipt was rejected."""
        timestamp = now()
        with self.transaction():
            operation_updated = self.connection.execute(
                "UPDATE operations SET state = ?, updated_at = ? WHERE operation_id = ? AND run_id = ?",
                ("rejected", timestamp, operation_id, run_id),
            ).rowcount
            if operation_updated != 1:
                raise RunnerError("operation_missing", "cannot reject a Codex stage without its durable operation")
            worker_updated = self.connection.execute(
                """UPDATE workers SET external_thread_id = ?, external_turn_id = ?, state = ?, updated_at = ?
                   WHERE worker_id = ? AND run_id = ?""",
                (thread_id, turn_id, "rejected", timestamp, worker_id, run_id),
            ).rowcount
            if worker_updated != 1:
                raise RunnerError("worker_missing", "cannot reject a Codex stage without its durable worker")
            step_updated = self.connection.execute(
                "UPDATE steps SET state = ?, updated_at = ? WHERE run_id = ? AND step_name = ?",
                ("blocked", timestamp, run_id, step_name),
            ).rowcount
            if step_updated != 1:
                raise RunnerError("step_missing", "cannot reject a Codex stage without its durable step")
            run_updated = self.connection.execute(
                "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                ("blocked", timestamp, run_id),
            ).rowcount
            if run_updated != 1:
                raise RunnerError("run_missing", "cannot reject a Codex stage for an unknown run")
            self._insert_event(
                run_id=run_id,
                event_key=f"operation:{operation_id}:rejected:{turn_id}",
                event_type="codex_stage_receipt_rejected",
                payload={"operation_id": operation_id, "step_name": step_name,
                         "thread_id": thread_id, "turn_id": turn_id, "code": code},
            )
        record = self.find_by_run_id(run_id)
        assert record is not None
        return record

    def record_codex_turn_started(
        self,
        run_id: str,
        operation_id: str,
        *,
        thread_id: str,
        turn_id: str,
        step_name: str,
        worker_id: str,
    ) -> None:
        """Persist formal SDK identities before waiting for the turn result."""
        timestamp = now()
        with self.transaction():
            updated = self.connection.execute(
                """UPDATE workers
                   SET external_thread_id = ?, external_turn_id = ?, state = ?, updated_at = ?
                   WHERE worker_id = ? AND run_id = ?""",
                (thread_id, turn_id, "running", timestamp, worker_id, run_id),
            ).rowcount
            if updated != 1:
                raise RunnerError("worker_identity_missing", "cannot record an SDK turn for an unknown worker")
            operation_updated = self.connection.execute(
                "UPDATE operations SET state = ?, updated_at = ? WHERE operation_id = ? AND run_id = ?",
                ("running", timestamp, operation_id, run_id),
            ).rowcount
            if operation_updated != 1:
                raise RunnerError("operation_missing", "cannot start a Codex turn without its durable operation")
            step_updated = self.connection.execute(
                "UPDATE steps SET state = ?, updated_at = ? WHERE run_id = ? AND step_name = ?",
                ("running", timestamp, run_id, step_name),
            ).rowcount
            if step_updated != 1:
                raise RunnerError("step_missing", "cannot start a Codex turn without its durable step")
            run_updated = self.connection.execute(
                "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                ("running", timestamp, run_id),
            ).rowcount
            if run_updated != 1:
                raise RunnerError("run_missing", "cannot start a Codex turn for an unknown run")
            self._insert_event(
                run_id=run_id,
                event_key=f"operation:{operation_id}:turn-started:{turn_id}",
                event_type="worker_turn_started",
                payload={"operation_id": operation_id, "step_name": step_name, "thread_id": thread_id, "turn_id": turn_id},
            )

    def begin_stage(self, run_id: str, *, step_name: str, operation_id: str, backend_kind: str, worker_id: str | None = None) -> None:
        timestamp = now()
        with self.transaction():
            self.connection.execute(
                "INSERT OR IGNORE INTO steps VALUES (?, ?, ?, ?, ?)",
                (run_id, step_name, "pending", timestamp, timestamp),
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO operations VALUES (?, ?, ?, ?, ?, ?, ?)",
                (operation_id, run_id, f"{backend_kind}_stage", "intent", "", timestamp, timestamp),
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO workers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (worker_id or f"{backend_kind}:{run_id}:{step_name}", run_id, backend_kind, None, None, "pending", timestamp, timestamp),
            )
            self.connection.execute(
                "UPDATE runs SET current_step = ?, state = ?, updated_at = ? WHERE run_id = ?",
                (step_name, "starting", timestamp, run_id),
            )
            self._insert_event(
                run_id=run_id,
                event_key=f"operation:{operation_id}:intent",
                event_type="step_intent_recorded",
                payload={"operation_id": operation_id, "step_name": step_name, "backend_kind": backend_kind},
            )

    def complete_mechanical_stage(self, run_id: str, operation_id: str, *, step_name: str) -> RunRecord:
        timestamp = now()
        with self.transaction():
            self.connection.execute("UPDATE operations SET state = ?, updated_at = ? WHERE operation_id = ?", ("completed", timestamp, operation_id))
            self.connection.execute(
                "UPDATE workers SET state = ?, updated_at = ? WHERE worker_id = ?",
                ("completed", timestamp, f"deterministic_test:{run_id}:{step_name}"),
            )
            self.connection.execute("UPDATE steps SET state = ?, updated_at = ? WHERE run_id = ? AND step_name = ?", ("turn_completed", timestamp, run_id, step_name))
            self.connection.execute("UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?", ("turn_completed", timestamp, run_id))
            self._insert_event(
                run_id=run_id,
                event_key=f"operation:{operation_id}:completed",
                event_type="step_completed",
                payload={"operation_id": operation_id, "step_name": step_name, "state": "turn_completed"},
            )
        record = self.find_by_run_id(run_id)
        assert record is not None
        return record

    def record_verification(self, run_id: str, receipt: dict[str, object]) -> None:
        timestamp = now()
        stage_name = str(receipt.get("stage", ""))
        with self.transaction():
            self.connection.execute(
                """INSERT OR REPLACE INTO verifications(receipt_id, run_id, stage_name, receipt_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (f"verification:{run_id}:{stage_name}", run_id, stage_name, json.dumps(receipt, ensure_ascii=False, sort_keys=True), timestamp),
            )
            self.connection.execute(
                "UPDATE steps SET state = ?, updated_at = ? WHERE run_id = ?",
                ("verified", timestamp, run_id),
            )
            self.connection.execute(
                "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                ("verified", timestamp, run_id),
            )
            self._insert_event(
                run_id=run_id,
                event_key=f"verification:{run_id}:{stage_name}",
                event_type="step_verified",
                payload={"stage_name": stage_name, "receipt_id": f"verification:{run_id}:{stage_name}"},
            )

    def mark_archived(self, run_id: str, *, state: str = "completed") -> None:
        timestamp = now()
        with self.transaction():
            self.connection.execute("UPDATE workers SET state = ?, updated_at = ? WHERE run_id = ?", ("archived", timestamp, run_id))
            self.connection.execute("UPDATE steps SET state = ?, updated_at = ? WHERE run_id = ?", ("archived", timestamp, run_id))
            self.connection.execute("UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?", (state, timestamp, run_id))
            self._insert_event(
                run_id=run_id,
                event_key=f"archive:{run_id}:{state}",
                event_type="cleanup_readback",
                payload={"state": state, "archived": True},
            )

    def mark_cleanup_pending(self, run_id: str) -> None:
        timestamp = now()
        with self.transaction():
            self.connection.execute("UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?", ("cleanup_pending", timestamp, run_id))
            self._insert_event(
                run_id=run_id,
                event_key=f"cleanup:{run_id}:pending",
                event_type="cleanup_pending",
                payload={"state": "cleanup_pending"},
            )

    def verification_for_run(self, run_id: str) -> list[dict[str, object]]:
        receipts: list[dict[str, object]] = []
        for row in self.connection.execute("SELECT * FROM verifications WHERE run_id = ? ORDER BY created_at", (run_id,)):
            values = dict(row)
            receipt_json = values.get("receipt_json", "")
            if not str(receipt_json).lstrip().startswith("{") and str(values.get("created_at", "")).lstrip().startswith("{"):
                # Read-only compatibility for the pre-migration ordinal-write
                # layout. A normal status query must never repair the database.
                receipt_json = values["created_at"]
            try:
                receipt = json.loads(receipt_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(receipt, dict):
                receipts.append(receipt)
        return receipts

    def fail_run(self, run_id: str, operation_id: str, *, state: str = "failed") -> None:
        timestamp = now()
        with self.transaction():
            self.connection.execute(
                "UPDATE operations SET state = ?, updated_at = ? WHERE operation_id = ?",
                (state, timestamp, operation_id),
            )
            self.connection.execute("UPDATE workers SET state = ?, updated_at = ? WHERE run_id = ?", (state, timestamp, run_id))
            self.connection.execute("UPDATE steps SET state = ?, updated_at = ? WHERE run_id = ?", (state, timestamp, run_id))
            self.connection.execute("UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?", (state, timestamp, run_id))
            self._insert_event(
                run_id=run_id,
                event_key=f"operation:{operation_id}:failed:{state}",
                event_type="run_failed",
                payload={"operation_id": operation_id, "state": state},
            )

    def workers_for_run(self, run_id: str) -> list[dict[str, object]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM workers WHERE run_id = ? ORDER BY rowid", (run_id,))]

    def steps_for_run(self, run_id: str) -> list[dict[str, object]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM steps WHERE run_id = ? ORDER BY created_at, step_name", (run_id,))]

    def operations_for_run(self, run_id: str) -> list[dict[str, object]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM operations WHERE run_id = ? ORDER BY created_at, operation_id", (run_id,))]

    def public_status(self, run_id: str) -> dict[str, object]:
        record = self.find_by_run_id(run_id)
        if record is None:
            raise RunnerError("unknown_run", f"run does not exist: {run_id}")
        step = self.connection.execute(
            "SELECT step_name, state, updated_at FROM steps WHERE run_id = ? ORDER BY rowid DESC LIMIT 1", (run_id,)
        ).fetchone()
        return {
            "run": record.public(),
            "step": dict(step) if step else None,
            "steps": self.steps_for_run(run_id),
            "operations": self.operations_for_run(run_id),
            "workers": self.workers_for_run(run_id),
            "verification": self.verification_for_run(run_id),
            "runtime": self.runtime_for_run(run_id),
            "control": self.control_for_run(run_id),
            "answers": self.answers_for_run(run_id),
            "events": self.events_for_run(run_id),
            "recovery": self.recovery_for_run(run_id),
            "continuation": self.continuation_receipts_for_run(run_id),
            "thread_migrations": self.thread_migrations_for_run(run_id),
            "migration_milestones": self.migration_milestones_for_run(run_id),
            "writer_leases": [dict(row) for row in self.connection.execute("SELECT * FROM runner_leases WHERE run_id = ?", (run_id,))],
            "route_circuits": self.route_circuits(),
        }

    def list_status(self) -> list[dict[str, str]]:
        return [record.public() for record in (self._record(row) for row in self.connection.execute("SELECT * FROM runs ORDER BY created_at")) if record]

    def write_log(self, control_root: Path, run_id: str, event: dict[str, object]) -> Path:
        log_path = control_root / "logs" / f"{run_id}.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"observed_at": now(), **event}, ensure_ascii=False, sort_keys=True) + "\n")
        return log_path

    def acquire_lease(self, *, scope: str, run_id: str, owner_token: str, pid: int | None = None, stale_after_seconds: float = 30.0) -> dict[str, object]:
        """Acquire a durable writer lease, reclaiming only proven local stale owners."""
        timestamp = now()
        pid = pid or os.getpid()
        with self.transaction():
            existing = self.connection.execute("SELECT * FROM runner_leases WHERE scope = ?", (scope,)).fetchone()
            if existing and existing["owner_token"] != owner_token:
                same_host = existing["host"] == socket.gethostname()
                age = _lease_age_seconds(str(existing["heartbeat_at"]))
                reclaimable = same_host and age is not None and age >= stale_after_seconds and not _process_alive(int(existing["pid"]))
                if not reclaimable:
                    raise RunnerError("writer_busy", "another Spec Runner writer owns this scope", details={"scope": scope, "run_id": existing["run_id"], "pid": existing["pid"], "same_host": same_host, "heartbeat_age_seconds": age})
                self.connection.execute("DELETE FROM runner_leases WHERE scope = ? AND owner_token = ?", (scope, existing["owner_token"]))
            self.connection.execute(
                "INSERT OR REPLACE INTO runner_leases(scope, run_id, owner_token, pid, host, acquired_at, heartbeat_at) VALUES (?, ?, ?, ?, ?, COALESCE((SELECT acquired_at FROM runner_leases WHERE scope = ?), ?), ?)",
                (scope, run_id, owner_token, pid, socket.gethostname(), scope, timestamp, timestamp),
            )
        return self.lease(scope)

    def heartbeat_lease(self, *, scope: str, owner_token: str) -> None:
        with self.transaction():
            updated = self.connection.execute("UPDATE runner_leases SET heartbeat_at = ? WHERE scope = ? AND owner_token = ?", (now(), scope, owner_token)).rowcount
            if not updated:
                raise RunnerError("writer_lease_lost", "writer lease is no longer owned by this process")

    def release_lease(self, *, scope: str, owner_token: str) -> None:
        with self.transaction():
            self.connection.execute("DELETE FROM runner_leases WHERE scope = ? AND owner_token = ?", (scope, owner_token))

    def lease(self, scope: str) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM runner_leases WHERE scope = ?", (scope,)).fetchone()
        return dict(row) if row else None

    def route_circuit(self, route_scope: str) -> dict[str, object] | None:
        """Read the durable circuit state for one explicitly scoped route."""
        scope = _route_scope_key(route_scope)
        row = self.connection.execute(
            "SELECT * FROM route_circuits WHERE route_scope = ?", (scope,)
        ).fetchone()
        return dict(row) if row else None

    def route_circuits(self) -> list[dict[str, object]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM route_circuits ORDER BY route_scope"
            )
        ]

    def record_route_failure(
        self,
        *,
        route_scope: str,
        failure_fingerprint: str,
        cooldown_seconds: float,
        owner_token: str | None = None,
        observed_at: str | None = None,
    ) -> dict[str, object]:
        """Open a shared route circuit while preserving half-open ownership."""
        scope = _route_scope_key(route_scope)
        fingerprint = str(failure_fingerprint or "").strip()
        if not fingerprint:
            raise RunnerError("route_failure_invalid", "route failure requires a fingerprint")
        if isinstance(cooldown_seconds, bool) or cooldown_seconds < 0:
            raise RunnerError("route_cooldown_invalid", "route cooldown must be non-negative")
        timestamp = _route_timestamp(observed_at)
        observed = _route_time(timestamp)
        cooldown_until = (observed + timedelta(seconds=float(cooldown_seconds))).isoformat()
        with self.transaction():
            existing = self.connection.execute(
                "SELECT * FROM route_circuits WHERE route_scope = ?", (scope,)
            ).fetchone()
            if existing is not None and existing["state"] == "half_open":
                current_owner = existing["half_open_owner"]
                if current_owner and current_owner != owner_token:
                    result = dict(existing)
                    result.update({"accepted": False, "reason": "half_open_owned"})
                    return result
            failure_count = int(existing["failure_count"]) + 1 if existing else 1
            if existing and existing["cooldown_until"]:
                try:
                    cooldown_until = max(
                        _route_time(str(existing["cooldown_until"])),
                        _route_time(cooldown_until),
                    ).isoformat()
                except RunnerError:
                    pass
            self.connection.execute(
                """INSERT INTO route_circuits(
                    route_scope, state, failure_count, cooldown_until,
                    half_open_owner, half_open_expires_at, last_failure_fingerprint,
                    last_success_evidence, updated_at
                ) VALUES (?, 'open', ?, ?, NULL, NULL, ?, NULL, ?)
                ON CONFLICT(route_scope) DO UPDATE SET
                    state='open', failure_count=excluded.failure_count,
                    cooldown_until=excluded.cooldown_until, half_open_owner=NULL,
                    half_open_expires_at=NULL,
                    last_failure_fingerprint=excluded.last_failure_fingerprint,
                    last_success_evidence=NULL, updated_at=excluded.updated_at""",
                (scope, failure_count, cooldown_until, fingerprint, timestamp),
            )
        result = self.route_circuit(scope) or {}
        result.update({"accepted": True, "reason": "circuit_opened"})
        return result

    def acquire_route_probe(
        self,
        *,
        route_scope: str,
        owner_token: str,
        lease_seconds: float,
        observed_at: str | None = None,
    ) -> dict[str, object]:
        """Atomically claim the only half-open probe after cooldown expiry."""
        scope = _route_scope_key(route_scope)
        owner = str(owner_token or "").strip()
        if not owner:
            raise RunnerError("route_probe_owner_invalid", "route probe requires an owner token")
        if isinstance(lease_seconds, bool) or lease_seconds <= 0:
            raise RunnerError("route_probe_lease_invalid", "route probe lease must be positive")
        timestamp = _route_timestamp(observed_at)
        observed = _route_time(timestamp)
        with self.transaction():
            row = self.connection.execute(
                "SELECT * FROM route_circuits WHERE route_scope = ?", (scope,)
            ).fetchone()
            if row is None:
                return {"route_scope": scope, "state": "closed", "acquired": False, "reason": "circuit_closed"}
            state = str(row["state"])
            if state == "closed":
                return {**dict(row), "acquired": False, "reason": "circuit_closed"}
            if state == "open":
                cooldown = row["cooldown_until"]
                if cooldown and _route_time(str(cooldown)) > observed:
                    return {**dict(row), "acquired": False, "reason": "cooldown_active"}
            elif state == "half_open":
                expires = row["half_open_expires_at"]
                current_owner = row["half_open_owner"]
                if current_owner == owner and expires and _route_time(str(expires)) > observed:
                    return {**dict(row), "acquired": True, "reason": "owner_replay"}
                if expires and _route_time(str(expires)) > observed:
                    return {**dict(row), "acquired": False, "reason": "half_open_owned"}
            else:
                raise RunnerError("route_circuit_invalid", "route circuit has an unknown state")
            expires_at = (observed + timedelta(seconds=float(lease_seconds))).isoformat()
            self.connection.execute(
                """UPDATE route_circuits SET state='half_open', half_open_owner = ?,
                   half_open_expires_at = ?, updated_at = ? WHERE route_scope = ?""",
                (owner, expires_at, timestamp, scope),
            )
        result = self.route_circuit(scope) or {}
        result.update({"acquired": True, "reason": "half_open_acquired"})
        return result

    def complete_route_probe(
        self,
        *,
        route_scope: str,
        owner_token: str,
        success_evidence: str,
        observed_at: str | None = None,
    ) -> dict[str, object]:
        """Close a circuit only with explicit route or business success evidence."""
        scope = _route_scope_key(route_scope)
        owner = str(owner_token or "").strip()
        evidence = str(success_evidence or "").strip()
        if not evidence or not any(
            evidence.startswith(prefix) for prefix in ("business_progress:", "route_success:")
        ):
            raise RunnerError(
                "route_probe_evidence_required",
                "a route probe cannot close its circuit without verified success evidence",
            )
        timestamp = _route_timestamp(observed_at)
        with self.transaction():
            row = self.connection.execute(
                "SELECT * FROM route_circuits WHERE route_scope = ?", (scope,)
            ).fetchone()
            if row is None or row["state"] != "half_open" or row["half_open_owner"] != owner:
                raise RunnerError("route_probe_not_owner", "route probe is not the half-open circuit owner")
            self.connection.execute(
                """UPDATE route_circuits SET state='closed', failure_count=0,
                   cooldown_until=NULL, half_open_owner=NULL, half_open_expires_at=NULL,
                   last_success_evidence=?, updated_at=? WHERE route_scope=?""",
                (evidence, timestamp, scope),
            )
        result = self.route_circuit(scope) or {}
        result.update({"closed": True, "reason": "verified_success"})
        return result

    def _insert_event(self, *, run_id: str, event_key: str, event_type: str, payload: dict[str, object]) -> bool:
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO events(run_id, event_key, event_type, payload_json, observed_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, event_key, event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True), now()),
        )
        return cursor.rowcount == 1

    def append_event(self, *, run_id: str, event_key: str, event_type: str, payload: dict[str, object]) -> bool:
        with self.transaction():
            return self._insert_event(run_id=run_id, event_key=event_key, event_type=event_type, payload=payload)

    def events_for_run(self, run_id: str) -> list[dict[str, object]]:
        return [
            {**dict(row), "payload": json.loads(row["payload_json"])}
            for row in self.connection.execute("SELECT * FROM events WHERE run_id = ? ORDER BY event_id", (run_id,))
        ]

    def upsert_recovery_episode(self, *, episode_id: str, run_id: str, operation_kind: str,
                                stage: str, generation: int, state: str = "open",
                                counters: dict[str, int] | None = None,
                                retry_deadline: str | None = None,
                                wait_deadline: str | None = None,
                                last_verified_progress: str | None = None) -> dict[str, object]:
        """Persist recovery budgets so restarts and thread changes cannot reset them."""
        counters = counters or {}
        values = {key: int(counters.get(key, 0)) for key in (
            "same_thread_attempts", "capacity_attempts", "route_probe_attempts",
            "clean_probe_attempts", "migration_attempts", "no_progress_attempts",
        )}
        timestamp = now()
        with self.transaction():
            existing = self.connection.execute("SELECT * FROM recovery_episodes WHERE episode_id = ?", (episode_id,)).fetchone()
            if existing is not None and (existing["run_id"] != run_id or existing["operation_kind"] != operation_kind or existing["stage"] != stage or int(existing["generation"]) != generation):
                raise RunnerError("recovery_episode_conflict", "recovery episode identity changed")
            self.connection.execute(
                """INSERT INTO recovery_episodes(episode_id, run_id, operation_kind, stage, generation, state,
                   same_thread_attempts, capacity_attempts, route_probe_attempts, clean_probe_attempts,
                   migration_attempts, no_progress_attempts, retry_deadline, wait_deadline,
                   last_verified_progress, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(episode_id) DO UPDATE SET state=excluded.state,
                   same_thread_attempts=excluded.same_thread_attempts, capacity_attempts=excluded.capacity_attempts,
                   route_probe_attempts=excluded.route_probe_attempts, clean_probe_attempts=excluded.clean_probe_attempts,
                   migration_attempts=MAX(recovery_episodes.migration_attempts, excluded.migration_attempts),
                   no_progress_attempts=MAX(recovery_episodes.no_progress_attempts, excluded.no_progress_attempts),
                   same_thread_attempts=MAX(recovery_episodes.same_thread_attempts, excluded.same_thread_attempts),
                   capacity_attempts=MAX(recovery_episodes.capacity_attempts, excluded.capacity_attempts),
                   route_probe_attempts=MAX(recovery_episodes.route_probe_attempts, excluded.route_probe_attempts),
                   clean_probe_attempts=MAX(recovery_episodes.clean_probe_attempts, excluded.clean_probe_attempts),
                   retry_deadline=excluded.retry_deadline, wait_deadline=excluded.wait_deadline,
                   last_verified_progress=excluded.last_verified_progress, updated_at=excluded.updated_at""",
                (episode_id, run_id, operation_kind, stage, generation, state,
                 values["same_thread_attempts"], values["capacity_attempts"], values["route_probe_attempts"],
                 values["clean_probe_attempts"], values["migration_attempts"], values["no_progress_attempts"],
                 retry_deadline, wait_deadline, last_verified_progress, timestamp),
            )
            self._insert_event(run_id=run_id, event_key=f"recovery:{episode_id}:episode:{state}:{timestamp}",
                               event_type="recovery_episode_updated", payload={"episode_id": episode_id, "state": state, **values})
        return self.recovery_episode(episode_id) or {}

    def reserve_recovery_budget(self, *, reservation_id: str, episode_id: str,
                                counter_name: str) -> dict[str, object]:
        """Atomically consume one recovery counter, once per attempt identity.

        The reservation row closes the crash window between deciding to retry and
        writing the updated episode. Replaying the same provider attempt is
        idempotent; a later attempt must use a new request/turn identity.
        """
        allowed = {
            "same_thread_attempts", "capacity_attempts", "route_probe_attempts",
            "clean_probe_attempts", "migration_attempts", "no_progress_attempts",
        }
        if counter_name not in allowed:
            raise RunnerError("recovery_counter_invalid", "unknown recovery budget counter")
        timestamp = now()
        with self.transaction():
            episode = self.connection.execute(
                "SELECT * FROM recovery_episodes WHERE episode_id = ?", (episode_id,)
            ).fetchone()
            if episode is None:
                raise RunnerError("recovery_episode_missing", "cannot reserve budget for an unknown episode")
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO recovery_budget_reservations(reservation_id, episode_id, counter_name, created_at) VALUES (?, ?, ?, ?)",
                (reservation_id, episode_id, counter_name, timestamp),
            )
            if cursor.rowcount == 1:
                self.connection.execute(
                    f"UPDATE recovery_episodes SET {counter_name} = {counter_name} + 1, updated_at = ? WHERE episode_id = ?",
                    (timestamp, episode_id),
                )
                self._insert_event(
                    run_id=str(episode["run_id"]),
                    event_key=f"recovery:{episode_id}:budget:{reservation_id}",
                    event_type="recovery_budget_reserved",
                    payload={"episode_id": episode_id, "counter": counter_name, "reservation_id": reservation_id},
                )
        return self.recovery_episode(episode_id) or {}

    def recovery_episode(self, episode_id: str) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM recovery_episodes WHERE episode_id = ?", (episode_id,)).fetchone()
        return dict(row) if row else None

    def record_recovery_observation(self, *, observation_id: str, episode_id: str,
                                    observation: dict[str, object]) -> dict[str, object]:
        fingerprint = str(observation.get("fingerprint") or "")
        if not fingerprint:
            raise RunnerError("recovery_observation_invalid", "observation requires a stable fingerprint")
        with self.transaction():
            if self.connection.execute("SELECT 1 FROM recovery_episodes WHERE episode_id = ?", (episode_id,)).fetchone() is None:
                raise RunnerError("recovery_episode_missing", "cannot record observation for an unknown episode")
            self.connection.execute(
                "INSERT OR IGNORE INTO recovery_observations(observation_id, episode_id, fingerprint, observation_json, observed_at) VALUES (?, ?, ?, ?, ?)",
                (observation_id, episode_id, fingerprint, json.dumps(observation, ensure_ascii=False, sort_keys=True), now()),
            )
        row = self.connection.execute("SELECT * FROM recovery_observations WHERE observation_id = ?", (observation_id,)).fetchone()
        assert row is not None
        result = dict(row)
        result["observation"] = json.loads(str(result.pop("observation_json")))
        return result

    def record_recovery_decision(self, *, decision_id: str, episode_id: str,
                                 decision: dict[str, object]) -> dict[str, object]:
        with self.transaction():
            if self.connection.execute("SELECT 1 FROM recovery_episodes WHERE episode_id = ?", (episode_id,)).fetchone() is None:
                raise RunnerError("recovery_episode_missing", "cannot record decision for an unknown episode")
            self.connection.execute(
                "INSERT OR IGNORE INTO recovery_decisions(decision_id, episode_id, decision_json, created_at) VALUES (?, ?, ?, ?)",
                (decision_id, episode_id, json.dumps(decision, ensure_ascii=False, sort_keys=True), now()),
            )
        row = self.connection.execute("SELECT * FROM recovery_decisions WHERE decision_id = ?", (decision_id,)).fetchone()
        assert row is not None
        result = dict(row)
        result["decision"] = json.loads(str(result.pop("decision_json")))
        return result

    def recovery_for_run(self, run_id: str) -> dict[str, object]:
        episodes = [dict(row) for row in self.connection.execute("SELECT * FROM recovery_episodes WHERE run_id = ? ORDER BY updated_at", (run_id,))]
        for episode in episodes:
            observations = []
            for row in self.connection.execute("SELECT * FROM recovery_observations WHERE episode_id = ? ORDER BY observed_at", (episode["episode_id"],)):
                item = dict(row)
                item["observation"] = json.loads(str(item.pop("observation_json")))
                observations.append(item)
            decisions = []
            for row in self.connection.execute("SELECT * FROM recovery_decisions WHERE episode_id = ? ORDER BY created_at", (episode["episode_id"],)):
                item = dict(row)
                item["decision"] = json.loads(str(item.pop("decision_json")))
                decisions.append(item)
            episode["observations"] = observations
            episode["decisions"] = decisions
            owner = self.connection.execute(
                "SELECT worker_id, backend_kind, external_thread_id, external_turn_id, state, updated_at "
                "FROM workers WHERE run_id = ? ORDER BY rowid DESC LIMIT 1", (run_id,)
            ).fetchone()
            episode["diagnostic"] = recovery_diagnostic(
                episode=episode,
                observations=observations,
                decisions=decisions,
                execution_owner=dict(owner) if owner else None,
                run_state=self.find_by_run_id(run_id).state if self.find_by_run_id(run_id) else None,
            )
        return {"episodes": episodes}

    def operation(self, operation_id: str) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM operations WHERE operation_id = ?", (operation_id,)).fetchone()
        return dict(row) if row else None

    def record_continuation_bundle(self, *, run_id: str, bundle_path: Path,
                                   bundle: dict[str, object]) -> dict[str, object]:
        """Register an already atomically written, schema-checked bundle.

        The file write intentionally happens before this transaction.  A later
        process can call the same method while reconciling the crash window;
        identity and digest conflicts fail closed instead of silently replacing
        the last known handoff.
        """
        required = ("run_id", "spec_key", "stage", "input_revision", "bundle_digest", "generation")
        if bundle.get("run_id") != run_id or any(key not in bundle for key in required):
            raise RunnerError("continuation_receipt_identity_invalid", "continuation receipt identity does not match its run")
        generation = bundle.get("generation")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise RunnerError("continuation_receipt_identity_invalid", "continuation receipt generation is invalid")
        spec_key = str(bundle["spec_key"])
        stage = str(bundle["stage"])
        input_revision = str(bundle["input_revision"])
        bundle_digest = str(bundle["bundle_digest"])
        if bundle_path.is_symlink() or not bundle_path.is_file():
            raise RunnerError("continuation_path_invalid", "continuation receipt must reference an existing regular file")
        digest_body = dict(bundle)
        digest_body.pop("bundle_digest", None)
        expected_digest = hashlib.sha256(json.dumps(
            digest_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        if bundle_digest != expected_digest:
            raise RunnerError("continuation_digest_mismatch", "continuation receipt digest does not match its contents")
        canonical_path = os.fspath(bundle_path.resolve())
        workspace = bundle.get("workspace")
        if not isinstance(workspace, dict):
            raise RunnerError("continuation_receipt_identity_invalid", "continuation receipt has no workspace identity")
        workspace_json = json.dumps(workspace, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        receipt_id = hashlib.sha256(json.dumps({
            "run_id": run_id, "spec_key": spec_key, "stage": stage, "generation": generation,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        timestamp = now()
        with self.transaction():
            if self.connection.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone() is None:
                raise RunnerError("run_missing", "cannot register continuation for an unknown run")
            existing = self.connection.execute(
                "SELECT * FROM continuation_receipts WHERE run_id = ? AND spec_key = ? AND stage = ? AND generation = ?",
                (run_id, spec_key, stage, generation),
            ).fetchone()
            if existing is not None:
                expected = (canonical_path, bundle_digest, input_revision, workspace_json, bundle.get("last_verified_progress"))
                actual = tuple(existing[key] for key in ("bundle_path", "bundle_digest", "input_revision", "workspace_identity_json", "last_verified_progress"))
                if actual != expected:
                    raise RunnerError("continuation_receipt_conflict", "continuation bundle identity or digest changed")
                return dict(existing)
            self.connection.execute(
                """INSERT INTO continuation_receipts(
                   receipt_id, run_id, spec_key, stage, generation, bundle_path,
                   bundle_digest, input_revision, workspace_identity_json,
                   last_verified_progress, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (receipt_id, run_id, spec_key, stage, generation, canonical_path,
                 bundle_digest, input_revision, workspace_json,
                 bundle.get("last_verified_progress"), timestamp, timestamp),
            )
            self._insert_event(
                run_id=run_id,
                event_key=f"continuation:{receipt_id}:registered",
                event_type="continuation_bundle_registered",
                payload={"receipt_id": receipt_id, "spec_key": spec_key, "stage": stage,
                         "generation": generation, "bundle_digest": bundle_digest,
                         "bundle_path": canonical_path},
            )
            row = self.connection.execute("SELECT * FROM continuation_receipts WHERE receipt_id = ?", (receipt_id,)).fetchone()
            assert row is not None
            return dict(row)

    def continuation_receipts_for_run(self, run_id: str) -> list[dict[str, object]]:
        receipts: list[dict[str, object]] = []
        for row in self.connection.execute(
                "SELECT * FROM continuation_receipts WHERE run_id = ? ORDER BY generation, spec_key, stage", (run_id,)):
            item = dict(row)
            item["workspace_identity"] = json.loads(str(item.pop("workspace_identity_json")))
            receipts.append(item)
        return receipts

    def prepare_thread_migration(
        self, *, migration_key: str, run_id: str, stage: str, source_thread_id: str,
        handover_digest: str, input_revision: str, owner_generation: int = 0,
    ) -> dict[str, object]:
        """Persist one clean-thread migration intent before creating a successor."""
        values = (run_id, stage, source_thread_id, handover_digest, input_revision, int(owner_generation))
        timestamp = now()
        with self.transaction():
            existing = self.connection.execute(
                "SELECT * FROM thread_migrations WHERE migration_key = ?", (migration_key,)
            ).fetchone()
            if existing is not None:
                actual = tuple(existing[key] for key in (
                    "run_id", "stage", "source_thread_id", "handover_digest", "input_revision", "owner_generation"
                ))
                if actual != values:
                    raise RunnerError("thread_migration_identity_conflict", "migration key was reused with different identity")
            else:
                self.connection.execute(
                    """INSERT INTO thread_migrations(
                       migration_key, run_id, stage, source_thread_id, handover_digest, input_revision,
                       owner_generation, state, successor_thread_id, owner_worker_id, handover_json,
                       successor_json, uncertainty_json, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'intent', NULL, NULL, NULL, NULL, NULL, ?, ?)""",
                    (migration_key, *values, timestamp, timestamp),
                )
                self._insert_event(run_id=run_id, event_key=f"migration:{migration_key}:intent",
                                   event_type="thread_migration_intent",
                                   payload={"migration_key": migration_key, "stage": stage,
                                            "source_thread_id": source_thread_id, "owner_generation": owner_generation})
        return self.thread_migration(migration_key) or {}

    def record_migration_handover(self, *, migration_key: str, handover: dict[str, object]) -> dict[str, object]:
        migration = self.thread_migration(migration_key)
        if migration is None:
            raise RunnerError("thread_migration_missing", "handover requires a durable migration intent")
        if migration["state"] not in {"intent", "handover_confirmed"}:
            if migration["state"] == "successor_registered" or migration["state"] == "owner_transferred":
                return migration
            raise RunnerError("thread_migration_state_conflict", "handover cannot advance this migration state")
        source = str(handover.get("thread_id") or "")
        handover_digest = hashlib.sha256(json.dumps(handover, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        if source != migration["source_thread_id"] or handover.get("accepted") is not True:
            raise RunnerError("thread_handover_unconfirmed", "successor creation requires confirmed source handover")
        if handover_digest != migration["handover_digest"]:
            raise RunnerError("thread_migration_identity_conflict", "handover evidence does not match the migration intent")
        timestamp = now()
        payload = json.dumps(handover, ensure_ascii=False, sort_keys=True)
        with self.transaction():
            self.connection.execute(
                "UPDATE thread_migrations SET state = 'handover_confirmed', handover_json = ?, updated_at = ? WHERE migration_key = ?",
                (payload, timestamp, migration_key),
            )
            self._insert_event(run_id=str(migration["run_id"]), event_key=f"migration:{migration_key}:handover",
                               event_type="thread_migration_handover_confirmed", payload={"migration_key": migration_key, "handover": handover})
        return self.thread_migration(migration_key) or {}

    def record_migration_successor(self, *, migration_key: str, successor_thread_id: str,
                                   successor: dict[str, object] | None = None) -> dict[str, object]:
        migration = self.thread_migration(migration_key)
        if migration is None:
            raise RunnerError("thread_migration_missing", "successor requires a durable migration intent")
        if migration["state"] == "owner_transferred":
            if migration.get("successor_thread_id") != successor_thread_id:
                raise RunnerError("thread_successor_conflict", "migration already has a different successor identity")
            return migration
        if migration["state"] not in {"handover_confirmed", "successor_registered"}:
            raise RunnerError("thread_handover_unconfirmed", "cannot create a successor before source handover readback")
        if not successor_thread_id.strip() or successor_thread_id == migration["source_thread_id"]:
            raise RunnerError("thread_successor_identity_invalid", "successor must have a distinct formal thread identity")
        prior = migration.get("successor_thread_id")
        if prior and prior != successor_thread_id:
            raise RunnerError("thread_successor_conflict", "migration already has a different successor identity")
        payload = json.dumps(successor or {"thread_id": successor_thread_id}, ensure_ascii=False, sort_keys=True)
        timestamp = now()
        with self.transaction():
            self.connection.execute(
                "UPDATE thread_migrations SET state = 'successor_registered', successor_thread_id = ?, successor_json = ?, updated_at = ? WHERE migration_key = ?",
                (successor_thread_id, payload, timestamp, migration_key),
            )
            self._insert_event(run_id=str(migration["run_id"]), event_key=f"migration:{migration_key}:successor:{successor_thread_id}",
                               event_type="thread_migration_successor_registered",
                               payload={"migration_key": migration_key, "successor_thread_id": successor_thread_id})
        return self.thread_migration(migration_key) or {}

    def record_migration_uncertainty(self, *, migration_key: str, details: dict[str, object]) -> dict[str, object]:
        migration = self.thread_migration(migration_key)
        if migration is None:
            raise RunnerError("thread_migration_missing", "uncertainty requires a durable migration intent")
        if migration["state"] in {"successor_registered", "owner_transferred"}:
            return migration
        if migration["state"] == "uncertain" and migration.get("uncertainty") == details:
            return migration
        timestamp = now()
        payload = json.dumps(details, ensure_ascii=False, sort_keys=True)
        with self.transaction():
            self.connection.execute(
                "UPDATE thread_migrations SET state = 'uncertain', uncertainty_json = ?, updated_at = ? WHERE migration_key = ?",
                (payload, timestamp, migration_key),
            )
            self._insert_event(run_id=str(migration["run_id"]), event_key=f"migration:{migration_key}:uncertain",
                               event_type="thread_migration_uncertain", payload={"migration_key": migration_key, "details": details})
        return self.thread_migration(migration_key) or {}

    def complete_migration_owner_transfer(self, *, migration_key: str, expected_generation: int,
                                          owner_worker_id: str) -> dict[str, object]:
        migration = self.thread_migration(migration_key)
        if migration is None:
            raise RunnerError("thread_migration_missing", "owner transfer requires a durable migration intent")
        if migration["state"] == "owner_transferred":
            if migration.get("owner_worker_id") != owner_worker_id:
                raise RunnerError("thread_owner_conflict", "migration owner was transferred to another worker")
            return migration
        if migration["state"] != "successor_registered" or int(migration["owner_generation"]) != int(expected_generation):
            raise RunnerError("thread_owner_cas_failed", "successor is not ready for this owner generation")
        timestamp = now()
        with self.transaction():
            updated = self.connection.execute(
                """UPDATE thread_migrations SET state = 'owner_transferred', owner_generation = ?,
                   owner_worker_id = ?, updated_at = ? WHERE migration_key = ? AND state = 'successor_registered' AND owner_generation = ?""",
                (int(expected_generation) + 1, owner_worker_id, timestamp, migration_key, int(expected_generation)),
            ).rowcount
            if updated != 1:
                raise RunnerError("thread_owner_cas_failed", "migration owner generation changed concurrently")
            self._insert_event(run_id=str(migration["run_id"]), event_key=f"migration:{migration_key}:owner:{int(expected_generation)+1}",
                               event_type="thread_migration_owner_transferred",
                               payload={"migration_key": migration_key, "owner_worker_id": owner_worker_id,
                                        "owner_generation": int(expected_generation) + 1})
        return self.thread_migration(migration_key) or {}

    def record_migration_event(self, *, migration_key: str, generation: int, event_key: str,
                               payload: dict[str, object]) -> dict[str, object]:
        """Audit a worker event and reject stale generations from advancing state."""
        migration = self.thread_migration(migration_key)
        if migration is None:
            raise RunnerError("thread_migration_missing", "migration event requires a durable migration")
        current_generation = int(migration["owner_generation"])
        applied = int(generation) == current_generation and migration["state"] == "owner_transferred"
        event = {**payload, "migration_key": migration_key, "generation": int(generation), "applied": applied}
        with self.transaction():
            inserted = self._insert_event(
                run_id=str(migration["run_id"]), event_key=event_key,
                event_type="thread_migration_event_applied" if applied else "thread_migration_stale_event",
                payload=event,
            )
        return {"migration_key": migration_key, "generation": int(generation), "applied": applied, "recorded": inserted}

    def thread_migration(self, migration_key: str) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM thread_migrations WHERE migration_key = ?", (migration_key,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in ("handover_json", "successor_json", "uncertainty_json"):
            value = result.pop(key)
            result[key.removesuffix("_json")] = json.loads(value) if value else None
        return result

    def thread_migrations_for_run(self, run_id: str) -> list[dict[str, object]]:
        return [self.thread_migration(str(row[0])) for row in self.connection.execute(
            "SELECT migration_key FROM thread_migrations WHERE run_id = ? ORDER BY created_at, migration_key", (run_id,)
        ) if self.thread_migration(str(row[0])) is not None]

    def record_migration_milestone(self, *, migration_key: str, milestone: str,
                                   receipt: dict[str, object]) -> dict[str, object]:
        """Persist one idempotent, identity-bound business migration milestone."""
        if not milestone.strip():
            raise RunnerError("thread_migration_milestone_invalid", "migration milestone must be non-empty")
        migration = self.thread_migration(migration_key)
        if migration is None:
            raise RunnerError("thread_migration_missing", "migration milestone requires a durable migration")
        timestamp = now()
        encoded = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
        with self.transaction():
            existing = self.connection.execute(
                "SELECT receipt_json FROM migration_milestones WHERE migration_key = ? AND milestone = ?",
                (migration_key, milestone),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != encoded:
                    raise RunnerError("thread_migration_milestone_conflict", "migration milestone receipt changed")
            else:
                self.connection.execute(
                    "INSERT INTO migration_milestones(migration_key, milestone, receipt_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (migration_key, milestone, encoded, timestamp, timestamp),
                )
                self._insert_event(
                    run_id=str(migration["run_id"]),
                    event_key=f"migration:{migration_key}:milestone:{milestone}",
                    event_type=f"thread_migration_{milestone}",
                    payload={"migration_key": migration_key, "milestone": milestone, "receipt": receipt},
                )
        return self.migration_milestone(migration_key, milestone) or {}

    def migration_milestone(self, migration_key: str, milestone: str) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT * FROM migration_milestones WHERE migration_key = ? AND milestone = ?",
            (migration_key, milestone),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["receipt"] = json.loads(str(result.pop("receipt_json")))
        return result

    def migration_milestones_for_run(self, run_id: str) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """SELECT m.* FROM migration_milestones m
               JOIN thread_migrations t ON t.migration_key = m.migration_key
               WHERE t.run_id = ? ORDER BY m.created_at, m.migration_key, m.milestone""",
            (run_id,),
        )
        result: list[dict[str, object]] = []
        for row in rows:
            item = dict(row)
            item["receipt"] = json.loads(str(item.pop("receipt_json")))
            result.append(item)
        return result

    def upsert_operation(self, *, operation_id: str, run_id: str, operation_kind: str, input_digest: str, state: str = "prepared") -> dict[str, object]:
        timestamp = now()
        with self.transaction():
            existing = self.connection.execute("SELECT * FROM operations WHERE operation_id = ?", (operation_id,)).fetchone()
            if existing:
                if existing["run_id"] != run_id or existing["input_digest"] != input_digest:
                    raise RunnerError("operation_identity_conflict", "operation identity was reused with different input")
            else:
                self.connection.execute("INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?)", (operation_id, run_id, operation_kind, state, input_digest, timestamp, timestamp))
        result = self.operation(operation_id)
        assert result is not None
        return result

    def record_takeover(self, *, takeover_key: str, report: dict[str, object], frontier: dict[str, object]) -> dict[str, object]:
        report_digest = str(report.get("digest", ""))
        frontier_digest = str(frontier.get("digest", ""))
        state = str(frontier.get("state", ""))
        if not takeover_key or not report_digest or not frontier_digest or not state:
            raise RunnerError("invalid_takeover_record", "takeover key, report digest, frontier digest, and state are required")
        record = {"schema_version": "spec-runner-takeover-record/v1", "takeover_key": takeover_key, "report_digest": report_digest, "frontier_digest": frontier_digest, "state": state, "report": report, "frontier": frontier}
        timestamp = now()
        with self.transaction():
            existing = self.connection.execute("SELECT * FROM takeover_records WHERE takeover_key = ?", (takeover_key,)).fetchone()
            if existing:
                if existing["report_digest"] != report_digest:
                    raise RunnerError("takeover_source_changed", "takeover key was reused after the source inventory changed")
                return {"created": False, "record": json.loads(existing["record_json"])}
            self.connection.execute(
                "INSERT INTO takeover_records(takeover_key, report_digest, frontier_digest, state, record_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (takeover_key, report_digest, frontier_digest, state, json.dumps(record, ensure_ascii=False, sort_keys=True), timestamp, timestamp),
            )
        return {"created": True, "record": record}

    def takeover_record(self, takeover_key: str) -> dict[str, object] | None:
        """Read one takeover record without changing its durable state."""
        row = self.connection.execute(
            "SELECT record_json FROM takeover_records WHERE takeover_key = ?",
            (takeover_key,),
        ).fetchone()
        if row is None:
            return None
        try:
            record = json.loads(row["record_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise RunnerError("takeover_record_corrupt", "takeover record is not valid JSON") from exc
        if (
            not isinstance(record, dict)
            or record.get("schema_version") != "spec-runner-takeover-record/v1"
            or record.get("takeover_key") != takeover_key
            or not isinstance(record.get("report"), dict)
            or not isinstance(record.get("frontier"), dict)
        ):
            raise RunnerError("takeover_record_corrupt", "takeover record has an invalid identity or shape")
        return record

    def update_takeover_record(
        self,
        *,
        takeover_key: str,
        state: str,
        event_key: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        """Advance one takeover identity and append an idempotent transition."""
        if not takeover_key or not state or not event_key:
            raise RunnerError("invalid_takeover_transition", "takeover key, state, and event key are required")
        timestamp = now()
        with self.transaction():
            existing = self.connection.execute(
                "SELECT * FROM takeover_records WHERE takeover_key = ?", (takeover_key,)
            ).fetchone()
            if existing is None:
                raise RunnerError("unknown_takeover", "takeover record does not exist")
            transition_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            prior = self.connection.execute(
                "SELECT takeover_key, state, payload_json FROM takeover_transitions WHERE event_key = ?", (event_key,)
            ).fetchone()
            if prior is not None:
                if prior["takeover_key"] != takeover_key:
                    raise RunnerError("takeover_transition_conflict", "transition identity belongs to another takeover")
                if prior["state"] != state or prior["payload_json"] != transition_payload:
                    raise RunnerError("takeover_transition_conflict", "transition identity was reused with different state")
            else:
                self.connection.execute(
                    "INSERT INTO takeover_transitions(takeover_key, event_key, state, payload_json, observed_at) VALUES (?, ?, ?, ?, ?)",
                    (takeover_key, event_key, state, transition_payload, timestamp),
                )
            record = json.loads(existing["record_json"])
            record["state"] = state
            record["last_transition"] = {"event_key": event_key, "state": state, "payload": payload}
            self.connection.execute(
                "UPDATE takeover_records SET state = ?, record_json = ?, updated_at = ? WHERE takeover_key = ?",
                (state, json.dumps(record, ensure_ascii=False, sort_keys=True), timestamp, takeover_key),
            )
        return {"created": prior is None, "record": record}

    def refresh_takeover_evidence(
        self,
        *,
        takeover_key: str,
        report: dict[str, object],
        frontier: dict[str, object],
        event_key: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        """Replace the observed report/frontier under one takeover identity."""
        report_digest = str(report.get("digest", ""))
        frontier_digest = str(frontier.get("digest", ""))
        state = str(frontier.get("state", ""))
        if not takeover_key or not report_digest or not frontier_digest or not state or not event_key:
            raise RunnerError("invalid_takeover_refresh", "takeover evidence refresh needs identity, evidence, and event")
        timestamp = now()
        transition_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self.transaction():
            existing = self.connection.execute(
                "SELECT * FROM takeover_records WHERE takeover_key = ?", (takeover_key,)
            ).fetchone()
            if existing is None:
                raise RunnerError("unknown_takeover", "takeover record does not exist")
            prior = self.connection.execute(
                "SELECT takeover_key, state, payload_json FROM takeover_transitions WHERE event_key = ?", (event_key,)
            ).fetchone()
            if prior is not None:
                if prior["takeover_key"] != takeover_key or prior["state"] != state or prior["payload_json"] != transition_payload:
                    raise RunnerError("takeover_transition_conflict", "takeover evidence event identity was reused with different data")
            else:
                self.connection.execute(
                    "INSERT INTO takeover_transitions(takeover_key, event_key, state, payload_json, observed_at) VALUES (?, ?, ?, ?, ?)",
                    (takeover_key, event_key, state, transition_payload, timestamp),
                )
            record = json.loads(existing["record_json"])
            if not isinstance(record, dict) or record.get("takeover_key") != takeover_key:
                raise RunnerError("takeover_record_corrupt", "takeover record identity is invalid")
            record.update({
                "report_digest": report_digest,
                "frontier_digest": frontier_digest,
                "state": state,
                "report": report,
                "frontier": frontier,
                "last_transition": {"event_key": event_key, "state": state, "payload": payload},
            })
            self.connection.execute(
                "UPDATE takeover_records SET report_digest = ?, frontier_digest = ?, state = ?, record_json = ?, updated_at = ? WHERE takeover_key = ?",
                (report_digest, frontier_digest, state, json.dumps(record, ensure_ascii=False, sort_keys=True), timestamp, takeover_key),
            )
        return {"created": prior is None, "record": record}

    def takeover_transitions(self, takeover_key: str) -> list[dict[str, object]]:
        return [
            {**dict(row), "payload": json.loads(row["payload_json"])}
            for row in self.connection.execute(
                "SELECT * FROM takeover_transitions WHERE takeover_key = ? ORDER BY transition_id", (takeover_key,)
            )
        ]
