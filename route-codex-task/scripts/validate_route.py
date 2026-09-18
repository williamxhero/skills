#!/usr/bin/env python3
"""Validate a generic Codex task route and emit a deterministic receipt."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from route_policy import (
    EVIDENCE_MODES,
    EVIDENCE_UNAVAILABLE,
    POLICY_ERROR,
    build_route_receipt,
    decision_hash,
    issue,
    sha256,
    sort_issues,
    validate_capability_readback,
    validate_execution_evidence,
    validate_route,
    validate_task_readback,
)


def read_json(path: Path, label: str, issues: list[dict[str, str]]) -> tuple[Any, bytes | None]:
    try:
        raw = path.read_bytes()
        return json.loads(raw.decode("utf-8-sig")), raw
    except OSError as exc:
        issues.append(issue(f"{label}_unreadable", str(path), f"Cannot read artifact: {exc.__class__.__name__}."))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        issues.append(issue(f"{label}_malformed", str(path), f"Artifact is not valid UTF-8 JSON: {exc.__class__.__name__}."))
    return None, None


def evaluate(
    record_path: Path,
    readback_path: Path,
    *,
    expected_route_id: str,
    expected_target: str,
    expected_host_id: str,
    expected_task_id: str,
    evidence_mode: str = "configured_readback",
    execution_evidence_path: Path | None = None,
) -> tuple[dict[str, Any], int]:
    issues: list[dict[str, str]] = []
    if evidence_mode not in EVIDENCE_MODES:
        issues.append(issue("invalid_evidence_mode", "$.evidence_mode", "Evidence mode must be configured_readback or execution_proof."))
    if POLICY_ERROR is not None:
        issues.append(issue("model_policy_invalid", "$.model_policy", f"route-codex-task policy is invalid: {POLICY_ERROR}."))
    record, record_raw = read_json(record_path, "route_record", issues)
    readback, readback_raw = read_json(readback_path, "route_readback", issues)
    route = None
    host_id = None
    capabilities: dict[str, set[str]] = {}
    xhigh_evidence: list[str] = []
    if isinstance(record, dict):
        expected_fields = {"schema_version", "route_id", "target", "host_capability_readback", "route", "xhigh_evidence"}
        if set(record) != expected_fields:
            issues.append(issue("invalid_route_record", "$", "Route record fields must match the route-codex-task contract."))
        if record.get("schema_version") != 1:
            issues.append(issue("schema_version_mismatch", "$.schema_version", "Route schema version must be 1."))
        if record.get("route_id") != expected_route_id:
            issues.append(issue("route_id_mismatch", "$.route_id", "Route record belongs to another route."))
        if record.get("target") != expected_target:
            issues.append(issue("route_target_mismatch", "$.target", "Route record belongs to another target."))
        host_id, capabilities = validate_capability_readback(record.get("host_capability_readback"), "$.host_capability_readback", issues, expected_host_id=expected_host_id)
        evidence = record.get("xhigh_evidence")
        if isinstance(evidence, list) and all(isinstance(item, str) and item.strip() for item in evidence):
            xhigh_evidence = evidence
        else:
            issues.append(issue("invalid_xhigh_evidence", "$.xhigh_evidence", "xhigh_evidence must be an array of non-empty strings."))
        route = validate_route(record.get("route"), "$.route", capabilities, issues, xhigh_evidence=xhigh_evidence)
    elif record is not None:
        issues.append(issue("invalid_route_record", "$", "Route record must be an object."))
    if readback is not None:
        validate_task_readback(
            readback,
            "$readback",
            issues,
            identity_field="route_id",
            expected_identity=expected_route_id,
            expected_target=expected_target,
            expected_task_id=expected_task_id,
            route=route,
            capabilities=capabilities,
            xhigh_evidence=xhigh_evidence,
        )
    execution_evidence = EVIDENCE_UNAVAILABLE
    if execution_evidence_path is not None:
        raw_evidence = read_json(execution_evidence_path, "execution_evidence", issues)
        execution_evidence = validate_execution_evidence(raw_evidence[0], "$execution", issues)
    if evidence_mode == "execution_proof" and execution_evidence == EVIDENCE_UNAVAILABLE:
        issues.append(issue("execution_evidence_missing", "$.execution", "Execution-proof mode requires provider or turn level execution evidence."))
    issues = sort_issues(issues)
    if issues:
        payload: dict[str, Any] = {"schema_version": 1, "decision": "reject", "gate": "codex_task_route", "route_id": expected_route_id, "target": expected_target, "reasons": issues}
        payload["decision_sha256"] = decision_hash(payload)
        return payload, 1
    assert isinstance(record, dict) and isinstance(readback, dict) and route is not None
    assert record_raw is not None and readback_raw is not None and host_id is not None
    return build_route_receipt(
        identity_name="route_id",
        identity=expected_route_id,
        target=expected_target,
        task_id=readback["task_id"],
        selection=readback["selection"],
        applied=readback["applied"],
        route=route,
        record_hash_name="route_record_sha256",
        record_hash=sha256(record_raw),
        readback_hash_name="route_readback_sha256",
        readback_hash=sha256(readback_raw),
        identity_evidence="formal_readback",
        capability_evidence="host_capability_readback",
        configured_route_evidence="post_create_readback",
        execution_evidence=execution_evidence,
        gate="codex_task_route",
        extra_fields={"host_id": host_id, "evidence_mode": evidence_mode},
    ), 0


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", required=True, type=Path)
    parser.add_argument("--readback", required=True, type=Path)
    parser.add_argument("--expected-route-id", required=True)
    parser.add_argument("--expected-target", required=True)
    parser.add_argument("--expected-host-id", required=True)
    parser.add_argument("--expected-task-id", required=True)
    parser.add_argument("--evidence-mode", default="configured_readback", choices=list(EVIDENCE_MODES))
    parser.add_argument("--execution-evidence", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    payload, code = evaluate(
        args.record,
        args.readback,
        expected_route_id=args.expected_route_id,
        expected_target=args.expected_target,
        expected_host_id=args.expected_host_id,
        expected_task_id=args.expected_task_id,
        evidence_mode=args.evidence_mode,
        execution_evidence_path=args.execution_evidence,
    )
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    if args.receipt is not None:
        write_atomic(args.receipt, serialized)
    sys.stdout.write(serialized)
    return code


if __name__ == "__main__":
    sys.exit(main())
