"""Pure qualification contracts shared by the validation command line tools.

This module deliberately has no SQLite dependency.  Qualification observes the
controller through its public receipts; it must not make a second controller or
prove a delivery by reading controller tables.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
QUALIFIED = "QUALIFIED"
REJECTED = "REJECTED"
REQUIRED_PROJECT_IDENTITY = ("project_id", "canonical_path")
REQUIRED_TASK_IDENTITY = ("formal_thread_id", "host_id", "project_id", "cwd")
REQUIRED_REPORT_FIELDS = (
    "scenario_version", "skill_digest", "harness_digest", "project_identity_receipt",
    "logical_id_to_external_id_map", "artifact_counts", "recovery_results",
    "recovery_evidence", "final_frontier",
    "release_train_receipts", "task_census", "repository_sync_receipt", "cleanup_receipt",
    "backend_capability_receipt", "route_visibility_receipt", "controller_lifecycle_receipt",
    "takeover_results",
)
EXPECTED_COUNTS = {
    "umbrella_specs": 1, "child_specs": 3, "tickets": 8, "grill_tasks": 1,
    "planning_tasks": 1, "spec_tasks": 3, "spec_pull_requests": 3,
    "ticket_implementation_artifacts": 0,
}
EXPECTED_TAKEOVER_STAGES = {
    "requirement", "planning", "ticketing", "implementation", "verification",
    "merge_cleanup", "final_verification", "release", "synchronization",
    "terminal", "managed_run",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_tree(root: Path, *, exclude: Iterable[Path] = ()) -> str:
    """Digest relative paths and bytes, excluding volatile runtime/report trees."""
    root = root.resolve()
    excluded = {path.resolve() for path in exclude}
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if any(path == item or item in path.parents for item in excluded):
            continue
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big")); digest.update(relative)
        contents = path.read_bytes()
        digest.update(len(contents).to_bytes(8, "big")); digest.update(contents)
    return digest.hexdigest()


def digest_paths(paths: Iterable[Path], *, root: Path) -> str:
    """Digest a declared set of files without including unrelated skills."""
    root = root.resolve()
    digest = hashlib.sha256()
    for path in sorted(path.resolve() for path in paths):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big")); digest.update(relative)
        contents = path.read_bytes()
        digest.update(len(contents).to_bytes(8, "big")); digest.update(contents)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def scenario_errors(scenario: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if scenario.get("schema_version") != SCHEMA_VERSION:
        errors.append("scenario_schema_version")
    if scenario.get("topology") != "whole-spec":
        errors.append("scenario_topology")
    if scenario.get("expected_artifact_counts") != EXPECTED_COUNTS:
        errors.append("scenario_artifact_counts")
    specs = scenario.get("specs")
    if not isinstance(specs, list) or len(specs) != 3:
        errors.append("scenario_specs")
        return errors
    expected_tickets = (3, 3, 2)
    for number, (spec, ticket_count) in enumerate(zip(specs, expected_tickets), 1):
        if spec.get("id") != f"SPEC-{number}" or len(spec.get("tickets", [])) != ticket_count:
            errors.append(f"scenario_spec_{number}")
    if specs[1].get("blocked_by") != ["SPEC-1"] or specs[2].get("blocked_by") != ["SPEC-2"]:
        errors.append("scenario_dependencies")
    if set(scenario.get("takeover_stages", [])) != EXPECTED_TAKEOVER_STAGES:
        errors.append("scenario_takeover_stages")
    return errors


def takeover_errors(results: Any) -> list[str]:
    if not isinstance(results, dict):
        return ["takeover_results_missing"]
    errors: list[str] = []
    stages = results.get("stages")
    if not isinstance(stages, list) or set(stages) != EXPECTED_TAKEOVER_STAGES:
        errors.append("takeover_stage_coverage_incomplete")
    if results.get("all_stages_covered") is not True:
        errors.append("takeover_matrix_incomplete")
    if results.get("resources_created") != 0:
        errors.append("takeover_created_duplicate_resources")
    return errors


def evidence_provenance_errors(report: dict[str, Any]) -> list[str]:
    current_run_id = report.get("run_id")
    source_run_ids: set[str] = set()
    tasks = report.get("task_census")
    if isinstance(tasks, dict) and isinstance(tasks.get("tasks"), list):
        source_run_ids.update(
            task.get("run_id") for task in tasks["tasks"]
            if isinstance(task, dict) and isinstance(task.get("run_id"), str)
        )
    backend = report.get("backend_capability_receipt")
    if isinstance(backend, dict):
        identity = backend.get("identity_readback", backend.get("probe_target"))
        if isinstance(identity, dict) and isinstance(identity.get("run_id"), str):
            source_run_ids.add(identity["run_id"])
    cleanup_run_id = report.get("cleanup_run_id")
    if isinstance(cleanup_run_id, str):
        source_run_ids.add(cleanup_run_id)
    if not source_run_ids or source_run_ids == {current_run_id}:
        return []
    if len(source_run_ids) != 1:
        return ["cross_run_evidence_inconsistent"]
    source_run_id = next(iter(source_run_ids))
    provenance = report.get("evidence_provenance")
    if not isinstance(provenance, dict):
        return ["cross_run_evidence_provenance_missing"]
    valid = (
        provenance.get("mode") == "reverified_existing_live_run"
        and provenance.get("source_run_id") == source_run_id
        and provenance.get("current_run_id") == current_run_id
        and isinstance(provenance.get("evidence"), list)
        and bool(provenance["evidence"])
    )
    return [] if valid else ["cross_run_evidence_provenance_invalid"]


def project_identity_errors(receipt: Any) -> list[str]:
    if not isinstance(receipt, dict):
        return ["project_identity_missing"]
    errors = []
    for field in REQUIRED_PROJECT_IDENTITY:
        if not isinstance(receipt.get(field), str) or not receipt[field].strip():
            errors.append(f"identity_{field}_missing")
    if receipt.get("request_id") is not None:
        errors.append("identity_request_id_forbidden")
    return errors


def task_identity_errors(receipt: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(receipt, dict):
        return ["task_identity_missing"]
    for field in REQUIRED_TASK_IDENTITY:
        if not isinstance(receipt.get(field), str) or not receipt[field].strip():
            errors.append(f"task_identity_{field}_missing")
    for field in ("task_id", "run_id", "attempt_id", "owner_id"):
        if not isinstance(receipt.get(field), str) or not receipt[field].strip():
            errors.append(f"task_identity_{field}_missing")
    return errors


def backend_errors(receipt: Any) -> list[str]:
    if not isinstance(receipt, dict):
        return ["backend_receipt_missing"]
    errors: list[str] = []
    if receipt.get("decision") not in (None, "allow"):
        errors.append("backend_probe_rejected")
    route = receipt.get("route_readback")
    if isinstance(route, dict):
        if route.get("decision") not in (None, "allow"):
            errors.append("backend_route_rejected")
        expected = receipt.get("expected_route")
        if isinstance(expected, dict):
            for field in ("model", "effort"):
                if expected.get(field) is not None and route.get(field) != expected[field]:
                    errors.append("backend_route_mismatch")
    if receipt.get("archive_error"):
        errors.append("backend_archive_failed")
    archive = receipt.get("archive_readback")
    if isinstance(archive, dict):
        if archive.get("decision") not in (None, "allow"):
            errors.append("backend_archive_rejected")
        if archive.get("required") is True and archive.get("archived") is not True:
            errors.append("backend_archive_incomplete")
    required = {"create_thread", "list_tasks", "read_thread", "read_applied_route",
                "send_message_to_thread", "set_thread_archived", "read_archive_state"}
    capabilities = receipt.get("capabilities", receipt.get("operations", []))
    if not isinstance(capabilities, list) or not required.issubset(set(capabilities)):
        errors.append("backend_capability_incomplete")
    target = receipt.get("identity_readback", receipt.get("probe_target"))
    if task_identity_errors(target):
        errors.append("backend_probe_identity_incomplete")
    return errors


def _is_true(receipt: Any, key: str) -> bool:
    return isinstance(receipt, dict) and receipt.get(key) is True


def route_visibility_errors(receipt: Any) -> list[str]:
    if not isinstance(receipt, dict):
        return ["route_visibility_missing"]
    errors = []
    for field in ("planned", "applied", "executed"):
        value = receipt.get(field)
        if not isinstance(value, dict):
            errors.append(f"route_{field}_missing")
            continue
        if field != "executed" and (not value.get("model") or not value.get("effort")):
            errors.append(f"route_{field}_incomplete")
        if field == "executed" and not value.get("turn_id") and value.get("status") != "unavailable":
            errors.append("route_executed_evidence_unavailable")
    if receipt.get("ticket_override"):
        errors.append("ticket_route_override_forbidden")
    if receipt.get("identity_consistent") is not True:
        errors.append("route_identity_mismatch")
    return errors


def lifecycle_errors(receipt: Any) -> list[str]:
    if not isinstance(receipt, dict):
        return ["controller_lifecycle_missing"]
    required = ("continuation_persisted", "watchdog_tested", "cleanup_readback_verified")
    return [f"lifecycle_{field}_missing" for field in required if receipt.get(field) is not True]


def recovery_errors(results: Any, evidence: Any, frontier: Any) -> list[str]:
    """Require execution evidence, not merely a detected recovery condition."""
    required = {"planning_restart", "ticket_restart", "assignment_restart",
                "merge_before_archive_restart", "release_restart", "lost_response",
                "idempotency_retry", "controller_interrupted", "capacity_fallback",
                "stream_disconnect", "uncertain_side_effect"}
    errors: list[str] = []
    if not isinstance(results, dict) or not all(key in results for key in required):
        errors.append("recovery_matrix_incomplete")
    else:
        for key in sorted(required):
            value = results[key]
            if isinstance(value, str):
                errors.append(f"recovery_{key}_evidence_missing")
                continue
            if not isinstance(value, dict):
                errors.append(f"recovery_{key}_evidence_missing")
                continue
            if value.get("detected") is not True:
                errors.append(f"recovery_{key}_detection_missing")
            if value.get("executed") is not True:
                errors.append(f"recovery_{key}_execution_missing")
    if not isinstance(evidence, dict):
        errors.append("recovery_evidence_missing")
    else:
        if not isinstance(evidence.get("detection"), list) or not evidence["detection"]:
            errors.append("recovery_detection_receipt_missing")
        if not isinstance(evidence.get("execution"), list) or not evidence["execution"]:
            errors.append("recovery_execution_receipt_missing")
    if not isinstance(frontier, dict) or frontier.get("successor_reached") is not True:
        errors.append("recovery_successor_not_reached")
    return errors


def verify_report(report: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    """Independently decide whether an externally collected report is qualified."""
    reasons = scenario_errors(scenario)
    for field in REQUIRED_REPORT_FIELDS:
        if field not in report:
            reasons.append(f"report_{field}_missing")
    reasons.extend(project_identity_errors(report.get("project_identity_receipt")))
    reasons.extend(backend_errors(report.get("backend_capability_receipt")))
    reasons.extend(route_visibility_errors(report.get("route_visibility_receipt")))
    reasons.extend(lifecycle_errors(report.get("controller_lifecycle_receipt")))
    reasons.extend(takeover_errors(report.get("takeover_results")))
    reasons.extend(evidence_provenance_errors(report))
    counts = report.get("artifact_counts")
    if counts != EXPECTED_COUNTS:
        reasons.append("artifact_counts_mismatch")
    tasks = report.get("task_census")
    expected_kinds = {"grill": 1, "planning": 1, "spec": 3}
    task_rows = tasks.get("tasks") if isinstance(tasks, dict) else None
    actual_kinds: dict[str, int] = {}
    identities: set[tuple[str, str]] = set()
    if isinstance(task_rows, list):
        for task in task_rows:
            if isinstance(task, dict):
                actual_kinds[task.get("kind")] = actual_kinds.get(task.get("kind"), 0) + 1
                reasons.extend(task_identity_errors(task))
                identity = (task.get("formal_thread_id"), task.get("host_id"))
                if identity in identities:
                    reasons.append("duplicate_task_identity")
                identities.add(identity)
    if (not _is_true(tasks, "all_archived") or not _is_true(tasks, "no_orphans")
            or actual_kinds != expected_kinds):
        reasons.append("task_census_incomplete")
    sync = report.get("repository_sync_receipt")
    if not _is_true(sync, "local_remote_head_equal"):
        reasons.append("repository_not_synchronized")
    cleanup = report.get("cleanup_receipt")
    if not _is_true(cleanup, "complete"):
        reasons.append("cleanup_incomplete")
    elif not cleanup.get("receipts") or any(not isinstance(item, dict) or item.get("archive_readback", {}).get("archived") is not True
                                             for item in cleanup.get("receipts", [])):
        reasons.append("cleanup_readback_missing")
    reasons.extend(recovery_errors(report.get("recovery_results"), report.get("recovery_evidence"), report.get("final_frontier")))
    release = report.get("release_train_receipts")
    if not isinstance(release, dict) or not all(release.get(level) == "passed" for level in ("L0", "L1", "L2", "L3", "L4", "L5")):
        reasons.append("release_train_incomplete")
    mapping = report.get("logical_id_to_external_id_map")
    if not isinstance(mapping, dict) or len(mapping) < 20:
        reasons.append("external_id_map_incomplete")
    if report.get("manual_waiver"):
        reasons.append("manual_waiver_forbidden")
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": report.get("run_id"),
        "decision": QUALIFIED if not reasons else REJECTED,
        "reasons": sorted(set(reasons)),
        "scenario_version": scenario.get("version"),
        "skill_digest": report.get("skill_digest"),
        "harness_digest": report.get("harness_digest"),
    }


def qualification_key(report: dict[str, Any]) -> dict[str, Any]:
    return {key: report.get(key) for key in (
        "scenario_version", "skill_digest", "harness_digest", "backend_kind", "backend_contract_version"
    )}
