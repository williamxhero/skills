from __future__ import annotations

import json
import hashlib
import sqlite3
import os
import socket
import ctypes
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from .errors import RunnerError

SCHEMA_VERSION = "spec-runner-store/v1"


def now() -> str:
    return datetime.now(UTC).isoformat()


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
                connection.close()
                raise RunnerError("control_not_ready", "Spec Runner control database schema is not ready")
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
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

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
            "writer_leases": [dict(row) for row in self.connection.execute("SELECT * FROM runner_leases WHERE run_id = ?", (run_id,))],
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
                   migration_attempts=excluded.migration_attempts, no_progress_attempts=excluded.no_progress_attempts,
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
        return {"episodes": episodes}

    def operation(self, operation_id: str) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM operations WHERE operation_id = ?", (operation_id,)).fetchone()
        return dict(row) if row else None

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
