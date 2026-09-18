"""Bounded, progress-aware recovery decisions for controller actions."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from control_db import _canonical_json, _evidence, now, transaction


CATEGORIES = frozenset({
    "transient", "unknown_outcome", "code_defect", "no_progress", "capability_denied",
})


def _digest(value) -> str:
    if isinstance(value, str):
        value = value.strip()
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _deadline(seconds):
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 1:
        raise ValueError("budget_seconds must be a positive integer")
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _expired(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) <= datetime.now(timezone.utc)


def record_failure(db, run_id, action_id, category, error_fingerprint,
                   code_digest, environment_digest, strategy_digest,
                   progress_marker, retry_owner, evidence, max_attempts=3,
                   budget_seconds=3600, budget_version="recovery-v1"):
    if category not in CATEGORIES:
        raise ValueError(f"unsupported recovery category: {category}")
    if not isinstance(retry_owner, str) or not retry_owner.strip():
        raise ValueError("retry_owner is required")
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    evidence = _evidence(evidence)
    fingerprint = _digest({"category": category, "error": error_fingerprint, "code": code_digest, "environment": environment_digest})
    strategy = _digest(strategy_digest)
    progress = _digest(progress_marker)
    evidence_digest = _digest(sorted(set(evidence)))
    with transaction(db.conn):
        action = db.conn.execute("SELECT run_id,status FROM actions WHERE action_id=?", (action_id,)).fetchone()
        if not action or action["run_id"] != run_id:
            raise ValueError("action does not belong to run")
        existing = db.conn.execute("SELECT * FROM recovery_states WHERE action_id=? AND fingerprint=?", (action_id, fingerprint)).fetchone()
        stamp = now()
        if existing:
            if existing["retry_owner"] != retry_owner:
                raise ValueError("recovery retry owner cannot change")
            attempts = existing["attempts"] + 1
            same_no_progress = (
                existing["last_strategy_digest"] == strategy
                and existing["last_progress_marker"] == progress
                and existing["last_evidence_digest"] == evidence_digest
            )
            budget_exhausted = attempts > existing["max_attempts"] or _expired(existing["deadline_at"])
            status = "paused" if budget_exhausted else ("escalated" if same_no_progress else "retry")
            deadline_at = existing["deadline_at"]
            db.conn.execute(
                "UPDATE recovery_states SET attempts=?,last_strategy_digest=?,last_progress_marker=?,last_evidence_digest=?,status=?,evidence=?,updated_at=? WHERE recovery_id=?",
                (attempts, strategy, progress, evidence_digest, status, json.dumps(sorted(set(evidence))), stamp, existing["recovery_id"]),
            )
            recovery_id = existing["recovery_id"]
        else:
            attempts = 1
            status = "retry"
            deadline_at = _deadline(budget_seconds)
            cursor = db.conn.execute(
                "INSERT INTO recovery_states(run_id,action_id,category,fingerprint,retry_owner,budget_version,max_attempts,deadline_at,attempts,last_strategy_digest,last_progress_marker,last_evidence_digest,status,evidence,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, action_id, category, fingerprint, retry_owner, budget_version,
                 max_attempts, deadline_at, attempts, strategy, progress, evidence_digest,
                 status, json.dumps(sorted(set(evidence))), stamp, stamp),
            )
            recovery_id = cursor.lastrowid
        if status in {"escalated", "paused"}:
            db.conn.execute("UPDATE actions SET status='blocked',updated_at=? WHERE action_id=?", (stamp, action_id))
        db.event(run_id, "recovery", str(recovery_id), "recovery_failure_recorded", {
            "action_id": action_id, "category": category, "fingerprint": fingerprint,
            "attempts": attempts, "status": status, "retry_owner": retry_owner,
            "budget_version": budget_version, "evidence": evidence,
        })
        return {"recovery_id": recovery_id, "action_id": action_id, "status": status, "attempts": attempts, "fingerprint": fingerprint, "deadline_at": deadline_at}


def resume_recovery(db, recovery_id, old_attempt_archived, strategy_digest, progress_marker, evidence):
    if not old_attempt_archived:
        raise ValueError("old attempt must be terminal and archived before recovery resumes")
    evidence = _evidence(evidence)
    with transaction(db.conn):
        row = db.conn.execute("SELECT * FROM recovery_states WHERE recovery_id=?", (recovery_id,)).fetchone()
        if not row:
            raise ValueError("unknown recovery state")
        if row["status"] not in {"retry", "escalated", "paused"}:
            raise ValueError(f"recovery cannot resume from {row['status']}")
        new_strategy = _digest(strategy_digest)
        new_progress = _digest(progress_marker)
        if row["status"] in {"escalated", "paused"} and new_strategy == row["last_strategy_digest"] and new_progress == row["last_progress_marker"]:
            raise ValueError("recovery needs a new strategy or progress marker")
        stamp = now()
        db.conn.execute(
            "UPDATE recovery_states SET status='resolved',last_strategy_digest=?,last_progress_marker=?,evidence=?,updated_at=? WHERE recovery_id=?",
            (new_strategy, new_progress, json.dumps(sorted(set(evidence))), stamp, recovery_id),
        )
        db.conn.execute("UPDATE actions SET status='pending',updated_at=? WHERE action_id=?", (stamp, row["action_id"]))
        db.event(row["run_id"], "recovery", str(recovery_id), "recovery_resumed", {
            "action_id": row["action_id"], "old_attempt_archived": True,
            "strategy_digest": new_strategy, "progress_marker": new_progress,
            "evidence": evidence,
        })
        return {"recovery_id": recovery_id, "action_id": row["action_id"], "status": "resolved", "next_action": "retry_original_action"}
