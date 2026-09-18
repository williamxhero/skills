"""Persisted run-level lifecycle rules and phase receipt validation."""
from __future__ import annotations

from typing import Any


PHASES = (
    "initialized",
    "preflight_passed",
    "grilling",
    "planning",
    "implementing",
    "final_verification",
    "release",
    "synchronization",
    "completed",
)

PHASE_TRANSITIONS = {
    "initialized": {"preflight_passed"},
    "preflight_passed": {"grilling"},
    "grilling": {"planning"},
    "planning": {"implementing"},
    "implementing": {"final_verification"},
    "final_verification": {"release"},
    "release": {"synchronization"},
    "synchronization": {"completed"},
    "completed": set(),
}
RUN_RESULTS = frozenset(("blocked", "user_stopped", "no_change", "completed"))


class RunStateError(ValueError):
    def __init__(self, code: str, details: dict[str, Any] | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(f"{code}: {self.details}")


def _reject(code: str, **details: Any) -> None:
    raise RunStateError(code, details)


def validate_phase_receipt(
    receipt: Any,
    *,
    run_id: str,
    from_phase: str,
    to_phase: str,
    expected_version: int,
) -> dict[str, Any]:
    """Require a verified, identity- and version-bound phase boundary receipt."""
    if not isinstance(receipt, dict):
        _reject("phase_receipt_missing", run_id=run_id, to_phase=to_phase)
    required = {"run_id", "from_phase", "to_phase", "status", "business_version", "evidence"}
    missing = sorted(required - set(receipt))
    if missing:
        _reject("phase_receipt_incomplete", missing=missing)
    if receipt["run_id"] != run_id:
        _reject("phase_receipt_run_mismatch", expected=run_id, actual=receipt["run_id"])
    if receipt["from_phase"] != from_phase or receipt["to_phase"] != to_phase:
        _reject(
            "phase_receipt_transition_mismatch",
            expected={"from_phase": from_phase, "to_phase": to_phase},
            actual={"from_phase": receipt["from_phase"], "to_phase": receipt["to_phase"]},
        )
    if receipt["status"] != "verified":
        _reject("phase_receipt_unverified", status=receipt["status"])
    if receipt["business_version"] != expected_version:
        _reject("phase_receipt_stale", expected=expected_version, actual=receipt["business_version"])
    if not isinstance(receipt["business_version"], int) or isinstance(receipt["business_version"], bool):
        _reject("phase_receipt_invalid_version")
    evidence = receipt["evidence"]
    if not isinstance(evidence, list) or not evidence or any(not isinstance(item, str) or not item.strip() for item in evidence):
        _reject("phase_receipt_evidence_missing")
    return receipt


def validate_result_receipt(
    receipt: Any,
    *,
    run_id: str,
    phase: str,
    result: str,
    expected_version: int,
) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        _reject("result_receipt_missing", result=result)
    required = {"run_id", "phase", "result", "status", "business_version", "reason", "evidence"}
    missing = sorted(required - set(receipt))
    if missing:
        _reject("result_receipt_incomplete", missing=missing)
    if receipt["run_id"] != run_id or receipt["phase"] != phase:
        _reject("result_receipt_identity_mismatch", run_id=run_id, phase=phase)
    if receipt["result"] != result:
        _reject("result_receipt_result_mismatch", expected=result, actual=receipt["result"])
    if receipt["status"] != "verified":
        _reject("result_receipt_unverified", status=receipt["status"])
    if receipt["business_version"] != expected_version:
        _reject("result_receipt_stale", expected=expected_version, actual=receipt["business_version"])
    if not isinstance(receipt["business_version"], int) or isinstance(receipt["business_version"], bool):
        _reject("result_receipt_invalid_version")
    if not isinstance(receipt["reason"], str) or not receipt["reason"].strip():
        _reject("result_reason_missing")
    evidence = receipt["evidence"]
    if not isinstance(evidence, list) or not evidence or any(not isinstance(item, str) or not item.strip() for item in evidence):
        _reject("result_evidence_missing")
    if result in {"blocked", "user_stopped"} and (
        not isinstance(receipt.get("recovery_action"), str) or not receipt["recovery_action"].strip()
    ):
        _reject("recovery_action_missing")
    return receipt
