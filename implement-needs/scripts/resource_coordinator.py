"""Coordinate cross-run resources outside individual Implement Needs ledgers."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from control_db import ActionClaimConflict, UnsafeLeaseTakeover, _is_expired, _time_after, now


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS resource_claims(
    resource_key TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    supports_fencing INTEGER NOT NULL DEFAULT 0,
    outcome_reconciled INTEGER NOT NULL DEFAULT 0
);
"""


class ResourceCoordinator:
    """A shared coordinator for repository, worktree, merge, and deployment keys."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self):
        self.conn.close()

    def acquire(self, resource_key, owner_id, run_id, lease_seconds=60,
                supports_fencing=False, outcome_reconciled=False):
        if not all(isinstance(value, str) and value.strip() for value in (resource_key, owner_id, run_id)):
            raise ValueError("resource_key, owner_id, and run_id are required")
        expires_at = _time_after(lease_seconds)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            current = self.conn.execute("SELECT * FROM resource_claims WHERE resource_key=?", (resource_key,)).fetchone()
            if current:
                if current["owner_id"] == owner_id and current["run_id"] == run_id and not _is_expired(current["lease_expires_at"]):
                    self.conn.execute("COMMIT")
                    return dict(current)
                if not _is_expired(current["lease_expires_at"]):
                    raise ActionClaimConflict(f"resource {resource_key} is owned by {current['owner_id']}")
                if not (supports_fencing and outcome_reconciled):
                    raise UnsafeLeaseTakeover(
                        "expired resource claim needs fencing support and outcome reconciliation before takeover"
                    )
                self.conn.execute("DELETE FROM resource_claims WHERE resource_key=?", (resource_key,))
            stamp = now()
            self.conn.execute(
                "INSERT INTO resource_claims(resource_key,owner_id,run_id,claimed_at,lease_expires_at,supports_fencing,outcome_reconciled) VALUES(?,?,?,?,?,?,?)",
                (resource_key, owner_id, run_id, stamp, expires_at, int(supports_fencing), int(outcome_reconciled)),
            )
            result = dict(self.conn.execute("SELECT * FROM resource_claims WHERE resource_key=?", (resource_key,)).fetchone())
            self.conn.execute("COMMIT")
            return result
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
