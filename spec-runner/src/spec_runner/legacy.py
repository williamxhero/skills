"""Read-only observation of legacy implement-needs databases.

This adapter intentionally has no migration or write method.  A later explicit
takeover can copy observations into the new store, but opening an old database
must not mutate it or make old rows look like Runner receipts.
"""
from __future__ import annotations

import json
import sqlite3
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import RunnerError


def read_legacy_database(database: Path) -> dict[str, object]:
    database = database.expanduser().resolve()
    if not database.is_file() or database.is_symlink():
        raise RunnerError("legacy_database_missing", "legacy database must be an existing regular file")
    uri = f"file:{database.as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        raise RunnerError("legacy_database_unreadable", "legacy database cannot be opened read-only") from exc
    try:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        known = {name: [dict(row) for row in connection.execute(f'SELECT * FROM "{name.replace(chr(34), chr(34) * 2)}" LIMIT 1000')] for name in tables if not name.startswith("sqlite_")}
        snapshot = {"tables": known, "table_names": tables}
        snapshot_digest = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        return {
            "schema_version": "spec-runner-legacy-observation/v1",
            "source": str(database),
            "read_only": True,
            "observed_at": datetime.now(UTC).isoformat(),
            "tables": known,
            "table_names": tables,
            "snapshot_digest": snapshot_digest,
            "historical_only": True,
        }
    except sqlite3.Error as exc:
        raise RunnerError("legacy_database_unreadable", "legacy database schema cannot be read") from exc
    finally:
        connection.close()


def legacy_takeover_inventory(*, database: Path, repository: Path) -> dict[str, object]:
    """Convert only observed legacy facts into the common takeover input.

    The adapter deliberately does not infer old thread identity, completion,
    or verification from arbitrary column names. Those facts remain historical
    evidence for the normal takeover planner to classify and reverify.
    """
    observation = read_legacy_database(database)
    repository = repository.expanduser().resolve()
    if not repository.is_dir():
        raise RunnerError("takeover_repository_invalid", "legacy inventory repository is not a directory")
    return {
        "schema_version": "spec-runner-takeover-input/v1",
        "repository_path": str(repository),
        "source_threads": [],
        "artifacts": [],
        "facts": {
            "legacy_database": {
                "source": observation["source"],
                "schema_version": observation["schema_version"],
                "snapshot_digest": observation["snapshot_digest"],
                "observed_at": observation["observed_at"],
                "table_names": observation["table_names"],
                "historical_only": True,
            },
            "legacy_tables": observation["tables"],
            "requirements": [],
            "tracker": False,
            "partial_code": False,
        },
        "source": {"kind": "legacy_database", "read_only": True, "observation": observation},
    }
