"""Read-only diagnostics, fault-matrix contracts, and release-evidence checks."""
from __future__ import annotations

import json
import os
import platform
import zipfile
from pathlib import Path
from typing import Any

from .errors import RunnerError
from .plans import digest

EVIDENCE_KINDS = {"deterministic", "local_git", "live_sdk", "live_github", "windows"}
_RELEASE_SUBJECT_SCALAR_FIELDS = {
    "build_digest",
    "config_contract",
    "os",
    "trust_mode",
    "scenario_version",
}
_RELEASE_CONTRACT_DIGEST_FIELDS = {"prompt_templates", "schemas", "validators"}


def load_json(path: Path, *, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError(code, "diagnostic input must be UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise RunnerError(code, "diagnostic input must be an object")
    return value


def validate_fault_matrix(document: dict[str, Any]) -> dict[str, object]:
    if document.get("schema_version") != "spec-runner-fault-matrix/v1":
        raise RunnerError("invalid_fault_matrix", "unexpected fault matrix schema")
    scenarios = document.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise RunnerError("invalid_fault_matrix", "fault matrix needs scenarios")
    identifiers: set[str] = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise RunnerError("invalid_fault_matrix", "scenario must be an object")
        identifier = scenario.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise RunnerError("invalid_fault_matrix", "scenario IDs must be unique")
        identifiers.add(identifier)
        if scenario.get("entrypoint") != "public_cli" or not isinstance(scenario.get("expected"), dict):
            raise RunnerError("invalid_fault_matrix", "scenario must use public_cli and declare expected result")
        if scenario.get("evidence_kind") not in EVIDENCE_KINDS:
            raise RunnerError("invalid_fault_matrix", "unknown evidence kind")
    return {"schema_version": "spec-runner-fault-matrix-receipt/v1", "scenario_count": len(scenarios), "digest": digest(document), "outcome": "validated"}


def runtime_report(*, runner_version: str, store_status: dict[str, Any] | None = None) -> dict[str, object]:
    runs = (store_status or {}).get("runs", [])
    states: dict[str, int] = {}
    if isinstance(runs, list):
        for run in runs:
            if isinstance(run, dict):
                state = str(run.get("state", "unknown"))
                states[state] = states.get(state, 0) + 1
    return {
        "schema_version": "spec-runner-runtime-report/v1",
        "runner_version": runner_version,
        "environment": {"os": platform.platform(), "python": platform.python_version(), "pid": os.getpid()},
        "runs_observed": len(runs) if isinstance(runs, list) else 0,
        "states": states,
        "progress_basis": "verified_step_events_and_receipts",
        "token_cost": "unknown",
        "cost_coverage": "unknown",
        "telemetry_is_business_progress": False,
    }


def canonical_release_subject(subject: dict[str, Any], *, expected_runner_version: str) -> dict[str, object]:
    """Validate the immutable identity that a release report qualifies."""
    if not isinstance(subject, dict) or subject.get("runner_version") != expected_runner_version:
        raise RunnerError("release_subject_missing", "release report must bind a subject with the current Runner version")
    for field in _RELEASE_SUBJECT_SCALAR_FIELDS:
        if not isinstance(subject.get(field), str) or not subject[field].strip():
            raise RunnerError("release_subject_missing", f"release subject needs a non-empty {field}")
    sdk_runtime = subject.get("sdk_runtime")
    if not isinstance(sdk_runtime, dict) or not all(
        isinstance(sdk_runtime.get(field), str) and sdk_runtime[field].strip()
        for field in ("package", "version")
    ):
        raise RunnerError("release_subject_missing", "release subject needs sdk_runtime package and version")
    contract_digests = subject.get("contract_digests")
    if not isinstance(contract_digests, dict) or not all(
        isinstance(contract_digests.get(field), str) and contract_digests[field].strip()
        for field in _RELEASE_CONTRACT_DIGEST_FIELDS
    ):
        raise RunnerError(
            "release_subject_missing",
            "release subject needs prompt_templates, schemas, and validators contract digests",
        )
    return {
        "runner_version": expected_runner_version,
        **{field: subject[field].strip() for field in sorted(_RELEASE_SUBJECT_SCALAR_FIELDS)},
        "sdk_runtime": {field: sdk_runtime[field].strip() for field in ("package", "version")},
        "contract_digests": {field: contract_digests[field].strip() for field in sorted(_RELEASE_CONTRACT_DIGEST_FIELDS)},
    }


def validate_release_report(document: dict[str, Any], *, expected_runner_version: str) -> dict[str, object]:
    if document.get("schema_version") != "spec-runner-release-report/v1":
        raise RunnerError("invalid_release_report", "unexpected release report schema")
    if document.get("runner_version") != expected_runner_version:
        raise RunnerError("release_version_mismatch", "release report describes a different Runner version")
    subject = canonical_release_subject(document.get("subject"), expected_runner_version=expected_runner_version)
    if document.get("subject_digest") != digest(subject):
        raise RunnerError("release_subject_digest_mismatch", "release subject digest does not match the supplied subject")
    evidence = document.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise RunnerError("release_evidence_missing", "release report needs evidence bodies")
    kinds: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict) or item.get("kind") not in EVIDENCE_KINDS:
            raise RunnerError("release_evidence_missing", "evidence must contain a supported kind")
        body = item.get("body")
        if not isinstance(body, dict):
            raise RunnerError("release_evidence_missing", "evidence must include its complete body")
        expected_digest = digest(body)
        if item.get("body_digest") != expected_digest:
            raise RunnerError("release_evidence_digest_mismatch", "evidence body digest does not match the supplied body")
        if body.get("evidence_kind") != item.get("kind"):
            raise RunnerError("release_evidence_kind_mismatch", "evidence body kind does not match its envelope")
        if body.get("verified") is not True and item.get("outcome") == "passed":
            raise RunnerError("release_evidence_unverified", "passed evidence must contain a verified body")
        if item.get("outcome") not in {"passed", "not_verified", "skipped", "failed"}:
            raise RunnerError("invalid_release_report", "invalid evidence outcome")
        kinds.add(item["kind"])
    required = set(document.get("required_kinds", ["deterministic", "local_git"]))
    missing = required - kinds
    if missing:
        raise RunnerError("release_evidence_missing", "required evidence kind is absent", details={"missing": sorted(missing)})
    failed = [item for item in evidence if item["outcome"] == "failed"]
    if failed:
        raise RunnerError("release_evidence_failed", "release evidence contains failures")
    required_not_passed = sorted(
        item["kind"] for item in evidence if item["kind"] in required and item["outcome"] != "passed"
    )
    return {
        "schema_version": "spec-runner-release-qualification/v1",
        "eligible": not required_not_passed,
        "subject_digest": digest(subject),
        "evidence_digest": digest(evidence),
        "unverified": [item["kind"] for item in evidence if item["outcome"] in {"not_verified", "skipped"}],
        "required_not_passed": required_not_passed,
    }


def build_release_report(*, runner_version: str, subject: dict[str, Any], evidence_documents: list[dict[str, Any]], required_kinds: set[str] | None = None) -> dict[str, object]:
    """Build a release report from complete evidence bodies."""
    canonical_subject = canonical_release_subject(subject, expected_runner_version=runner_version)
    envelopes: list[dict[str, object]] = []
    for body in evidence_documents:
        if not isinstance(body, dict) or body.get("evidence_kind") not in EVIDENCE_KINDS:
            raise RunnerError("release_evidence_missing", "each evidence file needs a supported evidence_kind")
        outcome = body.get("outcome")
        if outcome not in {"passed", "not_verified", "skipped", "failed"}:
            raise RunnerError("invalid_release_report", "each evidence body needs a valid outcome")
        if outcome == "passed" and body.get("verified") is not True:
            raise RunnerError("release_evidence_unverified", "passed evidence must declare verified=true")
        envelopes.append({"kind": body["evidence_kind"], "body": body, "body_digest": digest(body), "outcome": outcome})
    report = {
        "schema_version": "spec-runner-release-report/v1",
        "runner_version": runner_version,
        "subject": canonical_subject,
        "subject_digest": digest(canonical_subject),
        "required_kinds": sorted(required_kinds or {"deterministic", "local_git"}),
        "evidence": envelopes,
    }
    validate_release_report(report, expected_runner_version=runner_version)
    return {**report, "report_digest": digest(report)}


def inspect_wheel(path: Path, *, expected_runner_version: str) -> dict[str, object]:
    """Inspect an install artifact without installing or executing it."""
    if not path.is_file() or path.suffix != ".whl":
        raise RunnerError("invalid_wheel", "package inspection requires an existing .whl file")
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if archive.testzip() is not None:
                raise RunnerError("invalid_wheel", "wheel contains a corrupt member")
    except (OSError, zipfile.BadZipFile) as exc:
        raise RunnerError("invalid_wheel", "wheel is not a readable zip archive") from exc
    forbidden = [name for name in names if any(token in name.lower() for token in (".sqlite", ".db", ".log", "token", "secret", ".env"))]
    required = {
        "spec_runner/cli.py": any(name.endswith("spec_runner/cli.py") for name in names),
        "dependencies.lock.json": any(name.endswith("dependencies.lock.json") for name in names),
        "metadata": any(".dist-info/METADATA" in name for name in names),
    }
    missing = [key for key, present in required.items() if not present]
    if forbidden:
        raise RunnerError("wheel_contains_runtime_data", "wheel contains forbidden runtime data", details={"members": forbidden})
    if missing:
        raise RunnerError("wheel_missing_required_member", "wheel is missing required install members", details={"members": missing})
    return {"schema_version": "spec-runner-wheel-inspection/v1", "path": str(path.resolve()), "runner_version": expected_runner_version, "member_count": len(names), "required_members": required, "forbidden_members": [], "outcome": "verified", "archive_digest": digest(names)}
