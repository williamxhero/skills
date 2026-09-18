"""Shared applicability gate for terminal controller state transitions."""
from __future__ import annotations

from typing import Any


GATE_SCHEMA_VERSION = 1
EXPECTED_FIELDS = frozenset({"run_id", "target_id", "candidate_sha", "environment", "business_version"})
READBACK_FIELDS = frozenset({"status", "run_id", "target_id", "candidate_sha", "environment"})


class EvidenceGateError(ValueError):
    def __init__(self, code: str, details: dict[str, Any]):
        self.code = code
        self.details = details
        super().__init__(f"{self.code}: {self.details}")

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, **self.details}


def _reject(code: str, **details: Any) -> None:
    raise EvidenceGateError(code, details)


def _required_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _reject("gate_invalid_type", field=name, expected="object")
    return value


def _non_empty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _reject("gate_missing_field", field=name)
    return value


def verify_contract(
    contract: Any,
    *,
    entity_type: str,
    run_id: str,
    target_id: str,
    expected_version: int,
) -> dict[str, Any]:
    """Verify identity, authority, applicability and trusted readback.

    This function is deliberately side-effect free.  A caller's ``succeeded``
    claim is never accepted as proof; only the verifier-shaped readback can
    authorize the state mutation.
    """
    if not isinstance(contract, dict):
        _reject("gate_missing", entity_type=entity_type, target_id=target_id)
    if contract.get("schema_version") != GATE_SCHEMA_VERSION:
        _reject("gate_schema_version", expected=GATE_SCHEMA_VERSION)
    expected = contract.get("expected")
    if expected == {}:
        _reject("expected_empty")
    expected = _required_object(expected, "expected")
    missing = sorted(EXPECTED_FIELDS - set(expected))
    if missing:
        _reject("expected_incomplete", missing=missing)
    for field in EXPECTED_FIELDS - {"business_version"}:
        _non_empty_string(expected.get(field), f"expected.{field}")
    if not isinstance(expected.get("business_version"), int) or isinstance(expected.get("business_version"), bool):
        _reject("expected_invalid_type", field="expected.business_version")
    if expected["run_id"] != run_id:
        _reject("run_mismatch", expected=expected["run_id"], actual=run_id)
    if expected["target_id"] != target_id:
        _reject("target_mismatch", expected=expected["target_id"], actual=target_id)
    if expected["business_version"] != expected_version:
        _reject("business_version_mismatch", expected=expected["business_version"], actual=expected_version)

    actor = _required_object(contract.get("actor"), "actor")
    _non_empty_string(actor.get("id"), "actor.id")
    if actor.get("authorized") is not True:
        _reject("actor_unauthorized")
    source = _required_object(contract.get("source"), "source")
    if source.get("trust") != "verified":
        _reject("source_untrusted", trust=source.get("trust"))
    _non_empty_string(source.get("kind"), "source.kind")

    readback = _required_object(contract.get("readback"), "readback")
    missing = sorted(READBACK_FIELDS - set(readback))
    if missing:
        _reject("readback_incomplete", missing=missing)
    if readback.get("status") != "verified":
        _reject("readback_unverified", status=readback.get("status"))
    for field in READBACK_FIELDS - {"status"}:
        _non_empty_string(readback.get(field), f"readback.{field}")
    for field in ("run_id", "target_id", "candidate_sha", "environment"):
        if readback[field] != expected[field]:
            _reject("readback_mismatch", field=field, expected=expected[field], actual=readback[field])

    return {
        "decision": "allow",
        "entity_type": entity_type,
        "run_id": run_id,
        "target_id": target_id,
        "candidate_sha": expected["candidate_sha"],
        "environment": expected["environment"],
        "actor_id": actor["id"],
    }


def verify_terminal_contract(
    contract: Any,
    *,
    entity_type: str,
    run_id: str,
    target_id: str,
    expected_version: int,
    commits: list[str] | None = None,
    tests: list[str] | None = None,
) -> dict[str, Any]:
    result = verify_contract(
        contract,
        entity_type=entity_type,
        run_id=run_id,
        target_id=target_id,
        expected_version=expected_version,
    )
    expected = contract["expected"]
    candidate = expected["candidate_sha"]
    if entity_type == "ticket":
        if not isinstance(commits, list) or f"commit:{candidate}" not in commits:
            _reject("commit_evidence_inapplicable", candidate_sha=candidate)
        scope = contract.get("test_scope")
        _non_empty_string(scope, "test_scope")
        if not isinstance(tests, list) or f"test://{candidate}/{scope}" not in tests:
            _reject("test_evidence_inapplicable", candidate_sha=candidate, test_scope=scope)
    if entity_type == "thread":
        readback = contract["readback"]
        if contract.get("archive_operation") is not True:
            _reject("archive_operation_missing")
        if contract.get("archive_readback") is not True or readback.get("archived") is not True:
            _reject("archive_readback_missing")
    if entity_type == "action" and contract.get("action_readback") is not True:
        _reject("action_readback_missing")
    if entity_type == "spec":
        for field in ("merged", "tickets_closed", "thread_archived"):
            if contract["readback"].get(field) is not True:
                _reject("spec_delivery_incomplete", field=field)
    return result
