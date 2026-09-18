"""Small, serializable envelopes for host and external tool boundaries."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def success(result: Any, identifiers: dict[str, str], version: int, evidence_uri: str) -> dict[str, Any]:
    if not isinstance(identifiers, dict) or not evidence_uri:
        raise ValueError("success requires identifiers and evidence_uri")
    return {
        "ok": True,
        "status": "succeeded",
        "result": result,
        "identifiers": identifiers,
        "version": version,
        "evidence_uri": evidence_uri,
    }


def failure(category: str, error_fragment: str, log_uri: str, version: int | None = None) -> dict[str, Any]:
    if not all(isinstance(value, str) and value.strip() for value in (category, error_fragment, log_uri)):
        raise ValueError("failure requires category, error_fragment and log_uri")
    return {
        "ok": False,
        "status": "failed",
        "error": {"category": category, "fragment": error_fragment, "log_uri": log_uri},
        "version": version,
    }


def waiting_external(external_request_id: str, event_cursor: int, wake_condition: str, next_safe_check_at: str) -> dict[str, Any]:
    if not all(isinstance(value, str) and value.strip() for value in (external_request_id, wake_condition, next_safe_check_at)):
        raise ValueError("waiting_external requires request id, wake condition and next safe check")
    return {
        "ok": True,
        "status": "waiting_external",
        "external_request_id": external_request_id,
        "event_cursor": event_cursor,
        "wake_condition": wake_condition,
        "next_safe_check_at": next_safe_check_at,
    }


def host_operation(operation: str, arguments: dict[str, Any], required_capability: str, capabilities: set[str] | list[str], capability_evidence: list[str] | None = None) -> dict[str, Any]:
    if required_capability not in set(capabilities):
        return {"ok": False, "status": "capability_required", "operation": operation, "required_capability": required_capability, "arguments": arguments}
    if not isinstance(capability_evidence, list) or not capability_evidence:
        return {"ok": False, "status": "capability_evidence_required", "operation": operation, "required_capability": required_capability, "arguments": arguments}
    return {"ok": True, "status": "capability_confirmed", "operation": operation, "required_capability": required_capability, "arguments": arguments, "capability_evidence": capability_evidence}


def result_digest(result: Any) -> str:
    return hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
