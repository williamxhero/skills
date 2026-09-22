from __future__ import annotations

import json
import sqlite3
import os
import socket
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from .errors import RunnerError

SCHEMA_VERSION = "spec-runner-store/v1"


def now() -> str:
    return datetime.now(UTC).isoformat()


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
        return {"run_id": run_id, "requested_state": requested_state, "generation": generation, "updated_at": timestamp}

    def control_for_run(self, run_id: str) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM run_controls WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def clear_control(self, run_id: str) -> None:
        with self.transaction():
            self.connection.execute("DELETE FROM run_controls WHERE run_id = ?", (run_id,))

    def set_run_state(self, run_id: str, state: str) -> None:
        with self.transaction():
            self.connection.execute("UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?", (state, now(), run_id))

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
                (operation_id, run.run_id, "deterministic_test_stage", "intent", run.input_digest, run.created_at, run.updated_at),
            )
            self.connection.execute(
                "INSERT INTO workers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (f"{run.backend_kind}:{run.run_id}", run.run_id, run.backend_kind, None, None, "pending", run.created_at, run.updated_at),
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
        record = self.find_by_run_id(run_id)
        assert record is not None
        return record

    def complete_codex_stage(
        self, run_id: str, operation_id: str, *, thread_id: str, turn_id: str, state: str,
        step_name: str = "codex_example", worker_id: str | None = None
    ) -> RunRecord:
        timestamp = now()
        with self.transaction():
            self.connection.execute(
                "UPDATE operations SET state = ?, updated_at = ? WHERE operation_id = ?",
                (state, timestamp, operation_id),
            )
            self.connection.execute(
                """UPDATE workers SET external_thread_id = ?, external_turn_id = ?, state = ?, updated_at = ?
                   WHERE worker_id = ?""",
                (thread_id, turn_id, state, timestamp, worker_id or f"codex_sdk:{run_id}"),
            )
            self.connection.execute(
                "UPDATE steps SET state = ?, updated_at = ? WHERE run_id = ? AND step_name = ?",
                (state, timestamp, run_id, step_name),
            )
            self.connection.execute(
                "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                (state, timestamp, run_id),
            )
        record = self.find_by_run_id(run_id)
        assert record is not None
        return record

    def begin_stage(self, run_id: str, *, step_name: str, operation_id: str, backend_kind: str) -> None:
        timestamp = now()
        with self.transaction():
            self.connection.execute(
                "INSERT INTO steps VALUES (?, ?, ?, ?, ?)",
                (run_id, step_name, "pending", timestamp, timestamp),
            )
            self.connection.execute(
                "INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?)",
                (operation_id, run_id, f"{backend_kind}_stage", "intent", "", timestamp, timestamp),
            )
            self.connection.execute(
                "INSERT INTO workers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (f"{backend_kind}:{run_id}:{step_name}", run_id, backend_kind, None, None, "pending", timestamp, timestamp),
            )
            self.connection.execute(
                "UPDATE runs SET current_step = ?, state = ?, updated_at = ? WHERE run_id = ?",
                (step_name, "starting", timestamp, run_id),
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

    def mark_archived(self, run_id: str, *, state: str = "completed") -> None:
        timestamp = now()
        with self.transaction():
            self.connection.execute("UPDATE workers SET state = ?, updated_at = ? WHERE run_id = ?", ("archived", timestamp, run_id))
            self.connection.execute("UPDATE steps SET state = ?, updated_at = ? WHERE run_id = ?", ("archived", timestamp, run_id))
            self.connection.execute("UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?", (state, timestamp, run_id))

    def mark_cleanup_pending(self, run_id: str) -> None:
        timestamp = now()
        with self.transaction():
            self.connection.execute("UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?", ("cleanup_pending", timestamp, run_id))

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

    def workers_for_run(self, run_id: str) -> list[dict[str, object]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM workers WHERE run_id = ?", (run_id,))]

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
            "workers": self.workers_for_run(run_id),
            "verification": self.verification_for_run(run_id),
            "runtime": self.runtime_for_run(run_id),
            "control": self.control_for_run(run_id),
            "events": self.events_for_run(run_id),
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

    def acquire_lease(self, *, scope: str, run_id: str, owner_token: str, pid: int | None = None) -> dict[str, object]:
        """Acquire a durable writer lease; an existing lease is never displaced silently."""
        timestamp = now()
        pid = pid or os.getpid()
        with self.transaction():
            existing = self.connection.execute("SELECT * FROM runner_leases WHERE scope = ?", (scope,)).fetchone()
            if existing and existing["owner_token"] != owner_token:
                raise RunnerError("writer_busy", "another Spec Runner writer owns this scope", details={"scope": scope, "run_id": existing["run_id"], "pid": existing["pid"]})
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

    def append_event(self, *, run_id: str, event_key: str, event_type: str, payload: dict[str, object]) -> bool:
        with self.transaction():
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO events(run_id, event_key, event_type, payload_json, observed_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, event_key, event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True), now()),
            )
        return cursor.rowcount == 1

    def events_for_run(self, run_id: str) -> list[dict[str, object]]:
        return [
            {**dict(row), "payload": json.loads(row["payload_json"])}
            for row in self.connection.execute("SELECT * FROM events WHERE run_id = ? ORDER BY event_id", (run_id,))
        ]

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
