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
from pathlib import Path
from typing import Any, Callable

from .delivery import git_sha, merge_local, prepare_workspace, verify_candidate, validate_review
from .errors import RunnerError
from .plans import digest, validate_delivery_plan


def _run_command(command: list[str], *, cwd: Path) -> dict[str, object]:
    try:
        process = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", shell=False)
    except OSError as exc:
        raise RunnerError("delivery_command_failed", "implementation command could not start", details={"command": command}) from exc
    result = {
        "command": command,
        "exit_code": process.returncode,
        "stdout_digest": digest(process.stdout),
        "stderr_digest": digest(process.stderr),
        "passed": process.returncode == 0,
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


def run_local_delivery(
    *,
    plan: dict[str, Any],
    repository: Path,
    workspace_root: Path,
    control_root: Path,
    run_id: str,
    target_ref: str,
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
    completed: set[str] = {key for key, item in receipt["specs"].items() if isinstance(item, dict) and item.get("state") == "merged"}

    for spec in _topological(list(validated["specs"])):
        key = str(spec["key"])
        if key in completed:
            continue
        blockers = set(spec.get("blocked_by", [])) - completed
        if blockers:
            raise RunnerError("delivery_dependency_blocked", f"SPEC {key} is blocked", details={"blocked_by": sorted(blockers)})
        base_sha = git_sha(repository, target_ref)
        item = receipt["specs"].get(key) if isinstance(receipt["specs"].get(key), dict) else {}
        recovering_implementation = bool(item) and item.get("state") == "implementing"
        if item and item.get("base_sha") != base_sha and item.get("state") not in {"merged"}:
            raise RunnerError("delivery_base_changed", f"SPEC {key} base changed before recovery", details={"previous": item.get("base_sha"), "current": base_sha})
        if on_event:
            on_event("spec_started", {"spec_key": key, "base_sha": base_sha})
        workspace_info = prepare_workspace(repository=repository, workspace_root=workspace_root, run_id=run_id, spec_key=key, base_ref=target_ref)
        workspace = Path(str(workspace_info["workspace"]))
        item = {**item, "spec_key": key, "base_sha": base_sha, "workspace": str(workspace), "branch": workspace_info["branch"], "state": "implementing"}
        receipt["specs"][key] = item
        _write_receipt(receipt_path, receipt)
        if on_event:
            on_event("spec_intent_recorded", {"spec_key": key, "base_sha": base_sha})

        candidate_sha = str(item.get("candidate_sha", ""))
        if not candidate_sha:
            if recovering_implementation:
                actual_sha = git_sha(workspace)
                try:
                    dirty = subprocess.run(["git", "-C", os.fspath(workspace), "status", "--porcelain"], check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()
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
                candidate_sha = git_sha(workspace)
            item["candidate_sha"] = candidate_sha
            item["state"] = "candidate"
            receipt["specs"][key] = item
            _write_receipt(receipt_path, receipt)
        elif git_sha(workspace) != candidate_sha:
            raise RunnerError("delivery_candidate_changed", f"SPEC {key} candidate no longer matches its receipt")

        candidate_receipt = verify_candidate(
            workspace=workspace,
            candidate_sha=candidate_sha,
            acceptance_version=str(spec["acceptance_version"]),
            checks=list(spec["checks"]),
            acceptance=list(spec["acceptance"]),
        )
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
        merged = merge_local(repository=repository, candidate_branch=str(workspace_info["branch"]), target_ref=target_ref, expected_target_sha=base_sha, workspace_root=workspace_root, run_id=f"{key}-{run_id}")
        item.update({"state": "merged", "merge": merged})
        receipt["specs"][key] = item
        completed.add(key)
        if on_verified:
            on_verified({"schema_version": "spec-runner-spec-verification/v1", "stage": key, "run_id": run_id, "candidate_sha": candidate_sha, "merge_sha": merged["merge_sha"], "acceptance_version": spec["acceptance_version"], "outcome": "verified"})
        if on_event:
            on_event("spec_merged", {"spec_key": key, "candidate_sha": candidate_sha, "merge_sha": merged["merge_sha"]})
        _write_receipt(receipt_path, receipt)

    receipt["state"] = "completed"
    receipt["completed_specs"] = sorted(completed)
    _write_receipt(receipt_path, receipt)
    return receipt
