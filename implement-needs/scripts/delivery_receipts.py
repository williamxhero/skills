"""Version-bound delivery receipts and deterministic applicability projections."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from control_db import ControlDB, _canonical_json, now, transaction


REQUIRED = (
    "repository", "target_sha", "target_ref", "test_plan", "test_selection",
    "environment_fingerprint", "acceptance_version", "validator_version", "result",
    "source_kind", "provenance", "source_uri", "observed_at",
)
REUSE_KEY = ("repository", "target_sha", "test_plan", "test_selection", "environment_fingerprint", "acceptance_version", "validator_version")
PERSISTED_FIELDS = (*REQUIRED, "artifact_digest", "equivalence_policy")


def _valid_text(receipt: dict[str, Any], field: str) -> bool:
    return isinstance(receipt.get(field), str) and bool(receipt[field].strip())


def receipt_issues(receipt: Any, expected: dict[str, Any] | None = None) -> list[str]:
    if not isinstance(receipt, dict):
        return ["receipt_payload_malformed"]
    issues = [f"receipt_{field}_missing" for field in REQUIRED if not _valid_text(receipt, field)]
    if receipt.get("result") != "passed":
        issues.append("receipt_not_passed")
    if receipt.get("source_kind") != "controller_ci":
        issues.append("receipt_source_not_trusted_ci")
    try:
        datetime.fromisoformat(str(receipt.get("observed_at", "")).replace("Z", "+00:00"))
    except ValueError:
        issues.append("receipt_observed_at_invalid")
    for field in REUSE_KEY:
        if expected and expected.get(field) != receipt.get(field):
            issues.append(f"receipt_{field}_mismatch")
    return sorted(set(issues))


def _persisted_issues(row: dict[str, Any], expected: dict[str, Any] | None) -> list[str]:
    try:
        receipt = json.loads(row["payload"])
    except (TypeError, json.JSONDecodeError):
        return ["receipt_payload_malformed"]
    issues = receipt_issues(receipt, expected)
    if isinstance(receipt, dict):
        issues.extend(
            f"receipt_{field}_persisted_mismatch"
            for field in PERSISTED_FIELDS
            if row.get(field) != receipt.get(field)
        )
    return sorted(set(issues))


def record(db: ControlDB, run_id: str, entity_type: str, entity_id: str, receipt: dict[str, Any]) -> dict[str, Any]:
    if receipt_issues(receipt):
        raise ValueError("invalid delivery receipt: " + ",".join(receipt_issues(receipt)))
    values = [receipt[field] for field in REQUIRED]
    with transaction(db.conn):
        db.conn.execute(
            "INSERT OR REPLACE INTO delivery_receipts(run_id,entity_type,entity_id,repository,target_sha,target_ref,artifact_digest,test_plan,test_selection,environment_fingerprint,acceptance_version,validator_version,result,source_kind,provenance,source_uri,observed_at,equivalence_policy,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [run_id, entity_type, entity_id, *values[:3], receipt.get("artifact_digest"), *values[3:], receipt.get("equivalence_policy"), _canonical_json(receipt, "receipt")],
        )
        row = db.conn.execute("SELECT * FROM delivery_receipts WHERE receipt_id=last_insert_rowid()").fetchone()
        db.event(run_id, "delivery_receipt", str(row["receipt_id"]), "delivery_receipt_recorded", {"entity_type": entity_type, "entity_id": entity_id, "source_uri": receipt["source_uri"]})
        return dict(row)


def project(db: ControlDB, run_id: str, entity_type: str, entity_id: str, expected: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = db.conn.execute("SELECT * FROM delivery_receipts WHERE run_id=? AND entity_type=? AND entity_id=? ORDER BY receipt_id DESC", (run_id, entity_type, entity_id)).fetchall()
    if not rows:
        return {"decision": "reject", "missing": ["delivery_receipt_missing"], "invalid": [], "receipt": None}
    candidates = [dict(row) for row in rows]
    issues = [_persisted_issues(row, expected) for row in candidates]
    for row, row_issues in zip(candidates, issues):
        if not row_issues:
            return {"decision": "allow", "missing": [], "invalid": [], "receipt": row}
    smallest = min(issues, key=lambda value: (len(value), value))
    return {"decision": "reject", "missing": [], "invalid": smallest, "receipt": None}
