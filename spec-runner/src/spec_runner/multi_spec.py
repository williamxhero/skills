"""Deterministic whole-SPEC local delivery loop.

The plan is trusted configuration, not model output. Semantic implementation
and review may be represented by bounded external receipts, while ordering,
worktree creation, candidate verification, merge, and restart reconciliation
remain mechanical.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from .delivery import cleanup_managed_workspace, git_sha, git_status, merge_local, prepare_workspace, verify_candidate, validate_review
from .errors import RunnerError
from .plans import digest, validate_delivery_plan

IMPLEMENTATION_TIMEOUT_SECONDS = 1800


def _run_command(command: list[str], *, cwd: Path, timeout_seconds: int = IMPLEMENTATION_TIMEOUT_SECONDS) -> dict[str, object]:
    if timeout_seconds <= 0:
        raise RunnerError("delivery_timeout_invalid", "implementation timeout must be positive")
    try:
        process = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", shell=False, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        result = {"command": command, "exit_code": None, "timed_out": True, "timeout_seconds": timeout_seconds, "stdout_digest": digest(str(exc.stdout or "")), "stderr_digest": digest(str(exc.stderr or "")), "passed": False}
        raise RunnerError("delivery_command_timeout", "implementation command exceeded its bounded timeout", details=result) from exc
    except OSError as exc:
        raise RunnerError("delivery_command_failed", "implementation command could not start", details={"command": command}) from exc
    result = {
        "command": command,
        "exit_code": process.returncode,
        "stdout_digest": digest(process.stdout),
        "stderr_digest": digest(process.stderr),
        "passed": process.returncode == 0,
        "timed_out": False,
        "timeout_seconds": timeout_seconds,
    }
    if process.returncode != 0:
        raise RunnerError("delivery_command_failed", "implementation command failed", details=result)
    return result


def _write_receipt(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _read_receipt(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError("delivery_receipt_corrupt", "delivery receipt is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise RunnerError("delivery_receipt_corrupt", "delivery receipt must be an object")
    return value


def _ancestor(repository: Path, candidate_sha: str, target_ref: str,
              git_timeout_seconds: float = 120.0) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", os.fspath(repository), "merge-base", "--is-ancestor", candidate_sha, target_ref],
            check=False, capture_output=True, timeout=git_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunnerError(
            "implementation_git_timeout",
            "Git command exceeded its bounded timeout",
            details={"args": ["merge-base", "--is-ancestor", candidate_sha, target_ref],
                     "timeout_seconds": git_timeout_seconds},
        ) from exc
    except OSError as exc:
        raise RunnerError("delivery_git_read_failed", "could not reconcile candidate ancestry") from exc
    return result.returncode == 0


def _topological(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    remaining = {str(spec["key"]): spec for spec in specs}
    ordered: list[dict[str, Any]] = []
    while remaining:
        ready = [spec for key, spec in remaining.items() if set(spec.get("blocked_by", [])) <= {str(item["key"]) for item in ordered}]
        if not ready:
            raise RunnerError("delivery_dependency_blocked", "delivery plan has no ready SPEC")
        for spec in sorted(ready, key=lambda item: str(item["key"])):
            ordered.append(spec)
            remaining.pop(str(spec["key"]))
    return ordered


def _retry_merge_cleanup(*, repository: Path, workspace_root: Path, merge: dict[str, Any]) -> dict[str, Any]:
    cleanup = merge.get("cleanup")
    if not isinstance(cleanup, dict) or cleanup.get("outcome") == "cleaned" or not cleanup.get("workspace"):
        return merge
    merge["cleanup"] = cleanup_managed_workspace(
        repository=repository,
        workspace_root=workspace_root / "merge",
        workspace=Path(str(cleanup["workspace"])),
    )
    return merge


def _test_fault_pause_after_candidate_verified(*, control_root: Path, run_id: str, spec_key: str) -> None:
    """Expose one public-CLI fault boundary without changing normal delivery."""
    point = "after_candidate_verified"
    if os.environ.get("SPEC_RUNNER_FAULT_POINT") != point:
        return
    fault_root = control_root / "faults"
    fault_root.mkdir(parents=True, exist_ok=True)
    ready = fault_root / f"{run_id}.{spec_key}.{point}.ready"
    release = fault_root / f"{run_id}.{spec_key}.{point}.continue"
    ready.write_text(json.dumps({"run_id": run_id, "spec_key": spec_key, "point": point}) + "\n", encoding="utf-8", newline="\n")
    while not release.exists():
        time.sleep(0.05)


def run_local_delivery(
    *,
    plan: dict[str, Any],
    repository: Path,
    workspace_root: Path,
    control_root: Path,
    run_id: str,
    target_ref: str,
    git_timeout_seconds: float = 120.0,
    on_verified: Callable[[dict[str, object]], None] | None = None,
    on_event: Callable[[str, dict[str, object]], None] | None = None,
) -> dict[str, object]:
    validated = validate_delivery_plan(plan)
    repository = repository.resolve()
    receipt_path = control_root / "delivery" / run_id / "delivery-receipt.json"
    prior = _read_receipt(receipt_path)
    if prior and prior.get("plan_digest") != validated["digest"]:
        raise RunnerError("delivery_plan_changed", "delivery plan changed after this run started")
    receipt: dict[str, Any] = prior or {"schema_version": "spec-runner-delivery-receipt/v1", "run_id": run_id, "plan_digest": validated["digest"], "target_ref": target_ref, "specs": {}, "state": "running"}
    completed: set[str] = {
        key
        for key, item in receipt["specs"].items()
        if isinstance(item, dict) and item.get("state") in {"merged", "cleanup_pending"}
    }

    for spec in _topological(list(validated["specs"])):
        key = str(spec["key"])
        if key in completed:
            item = receipt["specs"].get(key)
            if isinstance(item, dict) and isinstance(item.get("merge"), dict):
                item["merge"] = _retry_merge_cleanup(
                    repository=repository,
                    workspace_root=workspace_root,
                    merge=item["merge"],
                )
                merge_cleanup = item["merge"].get("cleanup")
                if not isinstance(merge_cleanup, dict) or merge_cleanup.get("outcome") != "cleaned":
                    receipt["specs"][key] = item
                    receipt["state"] = "cleanup_pending"
                    receipt["completed_specs"] = sorted(completed)
                    _write_receipt(receipt_path, receipt)
                    return receipt
            cleanup_record = item.get("cleanup") if isinstance(item, dict) else None
            cleanup_outcome = cleanup_record.get("outcome") if isinstance(cleanup_record, dict) else None
            if isinstance(item, dict) and cleanup_outcome != "cleaned" and item.get("workspace"):
                cleanup = cleanup_managed_workspace(
                    repository=repository,
                    workspace_root=workspace_root,
                    workspace=Path(str(item["workspace"])),
                    manifest=Path(str(item["manifest"])) if item.get("manifest") else None,
                )
                item["cleanup"] = cleanup
                if cleanup.get("outcome") != "cleaned":
                    item["state"] = "cleanup_pending"
                    receipt["specs"][key] = item
                    receipt["state"] = "cleanup_pending"
                    receipt["completed_specs"] = sorted(completed)
                    _write_receipt(receipt_path, receipt)
                    return receipt
                item["state"] = "merged"
                receipt["specs"][key] = item
                _write_receipt(receipt_path, receipt)
            continue
        blockers = set(spec.get("blocked_by", [])) - completed
        if blockers:
            raise RunnerError("delivery_dependency_blocked", f"SPEC {key} is blocked", details={"blocked_by": sorted(blockers)})
        base_sha = git_sha(repository, target_ref, timeout_seconds=git_timeout_seconds)
        item = receipt["specs"].get(key) if isinstance(receipt["specs"].get(key), dict) else {}
        recovering_implementation = bool(item) and item.get("state") == "implementing"
        if item and item.get("base_sha") != base_sha and item.get("state") not in {"merged"}:
            merge_was_applied = (
                item.get("state") == "verified_candidate"
                and item.get("candidate_sha")
                and _ancestor(
                    repository, str(item["candidate_sha"]), target_ref,
                    git_timeout_seconds=git_timeout_seconds,
                )
            )
            if not merge_was_applied:
                raise RunnerError("delivery_base_changed", f"SPEC {key} base changed before recovery", details={"previous": item.get("base_sha"), "current": base_sha})
        if on_event:
            on_event("spec_started", {"spec_key": key, "base_sha": base_sha})
        # A provider/local Git merge can be applied before the process records
        # its final receipt. If the candidate is already reachable from the
        # target and the durable state had reached verified_candidate, adopt
        # that result instead of creating a second merge.
        if (
            item.get("state") == "verified_candidate"
            and item.get("candidate_sha")
            and _ancestor(
                repository, str(item["candidate_sha"]), target_ref,
                git_timeout_seconds=git_timeout_seconds,
            )
        ):
            item.update({"state": "merged", "merge": {"schema_version": "spec-runner-local-merge/v1", "target_ref": target_ref, "tested_head": item["candidate_sha"], "previous_target_sha": item.get("base_sha"), "merge_sha": base_sha, "outcome": "reconciled"}})
            receipt["specs"][key] = item
            completed.add(key)
            if on_verified:
                on_verified({"schema_version": "spec-runner-spec-verification/v1", "stage": key, "run_id": run_id, "candidate_sha": item["candidate_sha"], "merge_sha": base_sha, "acceptance_version": spec["acceptance_version"], "outcome": "verified", "reconciled": True})
            if on_event:
                on_event("spec_merge_reconciled", {"spec_key": key, "candidate_sha": item["candidate_sha"], "merge_sha": base_sha})
            _write_receipt(receipt_path, receipt)
            continue
        workspace_info = prepare_workspace(
            repository=repository, workspace_root=workspace_root, run_id=run_id,
            spec_key=key, base_ref=target_ref,
            git_timeout_seconds=git_timeout_seconds,
        )
        workspace = Path(str(workspace_info["workspace"]))
        item = {**item, "spec_key": key, "base_sha": base_sha, "workspace": str(workspace), "manifest": str(workspace_info["manifest"]), "branch": workspace_info["branch"], "state": "implementing"}
        receipt["specs"][key] = item
        _write_receipt(receipt_path, receipt)
        if on_event:
            on_event("spec_intent_recorded", {"spec_key": key, "base_sha": base_sha})

        candidate_sha = str(item.get("candidate_sha", ""))
        if not candidate_sha:
            if recovering_implementation:
                actual_sha = git_sha(workspace, timeout_seconds=git_timeout_seconds)
                try:
                    dirty = git_status(workspace, timeout_seconds=git_timeout_seconds)
                except (OSError, subprocess.CalledProcessError) as exc:
                    raise RunnerError("delivery_recovery_unknown", f"SPEC {key} workspace could not be reconciled") from exc
                if actual_sha == base_sha or dirty:
                    raise RunnerError("delivery_recovery_unknown", f"SPEC {key} stopped before a uniquely identifiable candidate")
                candidate_sha = actual_sha
            elif not spec.get("implementation"):
                raise RunnerError("delivery_implementation_missing", f"SPEC {key} has no implementation commands")
            else:
                for command in spec["implementation"]:
                    _run_command(command, cwd=workspace)
                candidate_sha = git_sha(workspace, timeout_seconds=git_timeout_seconds)
            item["candidate_sha"] = candidate_sha
            item["state"] = "candidate"
            receipt["specs"][key] = item
            _write_receipt(receipt_path, receipt)
        elif git_sha(workspace, timeout_seconds=git_timeout_seconds) != candidate_sha:
            raise RunnerError("delivery_candidate_changed", f"SPEC {key} candidate no longer matches its receipt")

        candidate_receipt = verify_candidate(
            workspace=workspace,
            candidate_sha=candidate_sha,
            acceptance_version=str(spec["acceptance_version"]),
            checks=list(spec["checks"]),
            acceptance=list(spec["acceptance"]),
            git_timeout_seconds=git_timeout_seconds,
        )
        _test_fault_pause_after_candidate_verified(control_root=control_root, run_id=run_id, spec_key=key)
        review_path = (control_root / str(spec["review_file"])).resolve()
        if control_root.resolve() not in (review_path, *review_path.parents) or review_path.is_symlink():
            raise RunnerError("delivery_review_path_escape", f"SPEC {key} review file escaped control root")
        try:
            review = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RunnerError("delivery_review_missing", f"SPEC {key} review receipt is unreadable") from exc
        validated_review = validate_review(result=review, candidate_sha=candidate_sha, acceptance_version=str(spec["acceptance_version"]))
        if not validated_review["approved"]:
            raise RunnerError("delivery_review_blocked", f"SPEC {key} has unresolved blocking findings", details={"findings": validated_review["blocking"]})
        item.update({"state": "verified_candidate", "candidate_receipt": candidate_receipt, "review": validated_review})
        receipt["specs"][key] = item
        _write_receipt(receipt_path, receipt)
        merged = merge_local(
            repository=repository, candidate_branch=str(workspace_info["branch"]),
            target_ref=target_ref, expected_target_sha=base_sha,
            workspace_root=workspace_root, run_id=f"{key}-{run_id}",
            git_timeout_seconds=git_timeout_seconds,
        )
        merged = _retry_merge_cleanup(repository=repository, workspace_root=workspace_root, merge=merged)
        item.update({"state": "merged", "merge": merged})
        receipt["specs"][key] = item
        completed.add(key)
        if on_verified:
            on_verified({"schema_version": "spec-runner-spec-verification/v1", "stage": key, "run_id": run_id, "candidate_sha": candidate_sha, "merge_sha": merged["merge_sha"], "acceptance_version": spec["acceptance_version"], "outcome": "verified"})
        if on_event:
            on_event("spec_merged", {"spec_key": key, "candidate_sha": candidate_sha, "merge_sha": merged["merge_sha"]})
        _write_receipt(receipt_path, receipt)
        cleanup = cleanup_managed_workspace(
            repository=repository,
            workspace_root=workspace_root,
            workspace=workspace,
            manifest=Path(str(workspace_info["manifest"])),
        )
        item["cleanup"] = cleanup
        merge_cleanup = merged.get("cleanup")
        if cleanup.get("outcome") != "cleaned" or not isinstance(merge_cleanup, dict) or merge_cleanup.get("outcome") != "cleaned":
            item["state"] = "cleanup_pending"
            receipt["specs"][key] = item
            receipt["state"] = "cleanup_pending"
            receipt["completed_specs"] = sorted(completed)
            _write_receipt(receipt_path, receipt)
            return receipt
        receipt["specs"][key] = item
        _write_receipt(receipt_path, receipt)

    receipt["state"] = "completed"
    receipt["completed_specs"] = sorted(completed)
    _write_receipt(receipt_path, receipt)
    return receipt
