"""Read-only observation of legacy implement-needs databases.

This adapter intentionally has no migration or write method.  A later explicit
takeover can copy observations into the new store, but opening an old database
must not mutate it or make old rows look like Runner receipts.
"""
from __future__ import annotations

import json
import sqlite3
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
        return {"schema_version": "spec-runner-legacy-observation/v1", "source": str(database), "read_only": True, "tables": known, "historical_only": True}
    except sqlite3.Error as exc:
        raise RunnerError("legacy_database_unreadable", "legacy database schema cannot be read") from exc
    finally:
        connection.close()
