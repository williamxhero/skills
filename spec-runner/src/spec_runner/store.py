from __future__ import annotations

import json
import sqlite3
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
                """
            )
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

    @staticmethod
    def _record(row: sqlite3.Row | None) -> RunRecord | None:
        if row is None:
            return None
        return RunRecord(**dict(row))

    def find_by_launch_key(self, launch_key: str) -> RunRecord | None:
        return self._record(self.connection.execute("SELECT * FROM runs WHERE launch_key = ?", (launch_key,)).fetchone())

    def find_by_run_id(self, run_id: str) -> RunRecord | None:
        return self._record(self.connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone())

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
        self, run_id: str, operation_id: str, *, thread_id: str, turn_id: str, state: str
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
                (thread_id, turn_id, state, timestamp, f"codex_sdk:{run_id}"),
            )
            self.connection.execute(
                "UPDATE steps SET state = ?, updated_at = ? WHERE run_id = ? AND step_name = ?",
                (state, timestamp, run_id, "codex_example"),
            )
            self.connection.execute(
                "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                (state, timestamp, run_id),
            )
        record = self.find_by_run_id(run_id)
        assert record is not None
        return record

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
        }

    def list_status(self) -> list[dict[str, str]]:
        return [record.public() for record in (self._record(row) for row in self.connection.execute("SELECT * FROM runs ORDER BY created_at")) if record]

    def write_log(self, control_root: Path, run_id: str, event: dict[str, object]) -> Path:
        log_path = control_root / "logs" / f"{run_id}.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"observed_at": now(), **event}, ensure_ascii=False, sort_keys=True) + "\n")
        return log_path
