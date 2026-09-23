"""Trust-minimising Git workspaces, candidate checks, review, and local delivery."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .errors import RunnerError
from .plans import digest

WORKTREE_CLEANUP_TIMEOUT_SECONDS = 2.0


def _git(repository: Path, *args: str, check: bool = True) -> str:
    try:
        result = subprocess.run(["git", "-C", os.fspath(repository), *args], check=check, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RunnerError("git_operation_failed", "Git command failed", details={"args": list(args)}) from exc
    return result.stdout.strip()


def git_sha(repository: Path, ref: str = "HEAD") -> str:
    return _git(repository, "rev-parse", "--verify", ref)


def _safe_child(root: Path, child: Path) -> Path:
    root = root.resolve()
    child = child.resolve()
    if root not in (child, *child.parents):
        raise RunnerError("workspace_path_escape", "workspace escapes configured root")
    return child


def _worktree_is_registered(*, repository: Path, workspace: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", os.fspath(repository), "worktree", "list", "--porcelain"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise RunnerError("workspace_cleanup_failed", "could not inspect managed worktree registration") from exc
    if result.returncode != 0:
        raise RunnerError("workspace_cleanup_failed", "could not inspect managed worktree registration")
    expected = workspace.resolve()
    return any(
        line.startswith("worktree ") and Path(line.removeprefix("worktree ")).resolve() == expected
        for line in result.stdout.splitlines()
    )


def _remove_managed_worktree(*, repository: Path, workspace: Path) -> None:
    """Bound one cleanup command so an exclusive Windows lock cannot hang a run."""
    try:
        result = subprocess.run(
            ["git", "-C", os.fspath(repository), "worktree", "remove", os.fspath(workspace)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=WORKTREE_CLEANUP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunnerError(
            "workspace_cleanup_timeout",
            "managed worktree cleanup exceeded its bounded timeout",
            details={"workspace": os.fspath(workspace), "timeout_seconds": WORKTREE_CLEANUP_TIMEOUT_SECONDS},
        ) from exc
    except OSError as exc:
        raise RunnerError("workspace_cleanup_failed", "managed worktree cleanup could not start") from exc
    if result.returncode != 0:
        # Windows Git can unregister the worktree before it loses the final
        # file handle. In that case the now-orphaned directory is still safe
        # to remove only after the manifest check below has proven ownership.
        if not _worktree_is_registered(repository=repository, workspace=workspace):
            return
        raise RunnerError(
            "workspace_cleanup_failed",
            "managed worktree cleanup was rejected",
            details={
                "workspace": os.fspath(workspace),
                "exit_code": result.returncode,
                "stderr_digest": hashlib.sha256(result.stderr.encode("utf-8", errors="replace")).hexdigest(),
            },
        )


def cleanup_managed_workspace(*, repository: Path, workspace_root: Path, workspace: Path, manifest: Path | None = None) -> dict[str, object]:
    """Remove one Runner-owned candidate worktree without touching user files.

    A Windows process can keep a worktree file open after the merge is durable.
    Cleanup is therefore a separately persisted operation: a lock returns a
    structured ``pending`` result and the next drive/recovery pass retries it.
    The caller must only pass paths recorded by ``prepare_workspace``.
    """
    repository = repository.resolve()
    workspace_root = workspace_root.resolve()
    safe_workspace = _safe_child(workspace_root, workspace)
    if safe_workspace == workspace_root:
        raise RunnerError("workspace_cleanup_unsafe", "managed cleanup cannot target the workspace root")
    safe_manifest = _safe_child(
        workspace_root,
        manifest if manifest is not None else workspace_root / f"{safe_workspace.name}.manifest.json",
    )
    if safe_manifest.is_symlink():
        raise RunnerError("workspace_cleanup_unsafe", "managed workspace manifest cannot be a symbolic link")
    if not safe_manifest.is_file():
        return {
            "outcome": "pending",
            "reason": "manifest_missing",
            "workspace": os.fspath(safe_workspace),
            "manifest": os.fspath(safe_manifest),
        }
    try:
        manifest_document = json.loads(safe_manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {
            "outcome": "pending",
            "reason": "manifest_invalid",
            "workspace": os.fspath(safe_workspace),
            "manifest": os.fspath(safe_manifest),
        }
    if (
        not isinstance(manifest_document, dict)
        or Path(str(manifest_document.get("workspace", ""))).resolve() != safe_workspace
        or Path(str(manifest_document.get("repository", ""))).resolve() != repository
    ):
        return {
            "outcome": "pending",
            "reason": "manifest_ownership_mismatch",
            "workspace": os.fspath(safe_workspace),
            "manifest": os.fspath(safe_manifest),
        }

    if safe_workspace.exists():
        try:
            _remove_managed_worktree(repository=repository, workspace=safe_workspace)
        except RunnerError as exc:
            return {
                "outcome": "pending",
                "reason": "worktree_remove_failed",
                "error_code": exc.code,
                "error_details": exc.details,
                "workspace": os.fspath(safe_workspace),
                "manifest": os.fspath(safe_manifest),
            }
        if safe_workspace.exists():
            try:
                shutil.rmtree(safe_workspace)
            except OSError as exc:
                return {
                    "outcome": "pending",
                    "reason": "workspace_directory_remove_failed",
                    "error_type": type(exc).__name__,
                    "workspace": os.fspath(safe_workspace),
                    "manifest": os.fspath(safe_manifest),
                }
            if safe_workspace.exists():
                return {
                    "outcome": "pending",
                    "reason": "worktree_still_exists",
                    "workspace": os.fspath(safe_workspace),
                    "manifest": os.fspath(safe_manifest),
                }

    if safe_manifest.exists():
        try:
            safe_manifest.unlink()
        except OSError as exc:
            return {
                "outcome": "pending",
                "reason": "manifest_remove_failed",
                "error_type": type(exc).__name__,
                "workspace": os.fspath(safe_workspace),
                "manifest": os.fspath(safe_manifest),
            }
    return {
        "outcome": "cleaned",
        "workspace": os.fspath(safe_workspace),
        "manifest": os.fspath(safe_manifest),
    }


def prepare_workspace(*, repository: Path, workspace_root: Path, run_id: str, spec_key: str, base_ref: str, branch: str | None = None) -> dict[str, object]:
    repository = repository.resolve()
    base_sha = git_sha(repository, base_ref)
    workspace_root.mkdir(parents=True, exist_ok=True)
    if workspace_root.is_symlink():
        raise RunnerError("workspace_path_escape", "workspace root cannot be a symbolic link")
    safe_key = "".join(char if char.isalnum() or char in "._-" else "-" for char in spec_key)
    workspace = _safe_child(workspace_root, workspace_root / f"{safe_key}-{run_id[:8]}")
    branch = branch or f"spec-runner/{safe_key}-{run_id[:8]}"
    manifest = workspace_root / f"{safe_key}-{run_id[:8]}.manifest.json"
    if manifest.exists():
        previous = json.loads(manifest.read_text(encoding="utf-8"))
        if previous.get("run_id") != run_id or previous.get("base_sha") != base_sha or Path(str(previous.get("workspace", ""))).resolve() != workspace:
            raise RunnerError("workspace_adoption_conflict", "existing workspace manifest belongs to another candidate")
        if workspace.is_dir() and git_sha(workspace) :
            return previous
    if workspace.exists():
        raise RunnerError("workspace_path_conflict", "workspace path already exists without an adopted manifest")
    # A worktree never touches the caller's currently checked-out files.
    _git(repository, "worktree", "add", "--detach", os.fspath(workspace), base_sha)
    try:
        _git(workspace, "switch", "-c", branch)
    except RunnerError:
        _git(repository, "worktree", "remove", "--force", os.fspath(workspace))
        raise
    result = {"schema_version": "spec-runner-workspace/v1", "run_id": run_id, "spec_key": spec_key, "workspace": os.fspath(workspace), "branch": branch, "base_sha": base_sha, "repository": os.fspath(repository), "trust_mode": "local_workspace_write", "automatic_merge_allowed": False}
    descriptor, temporary = tempfile.mkstemp(prefix=".workspace-", suffix=".json", dir=workspace_root)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, manifest)
    result["manifest"] = os.fspath(manifest)
    return result


def _run_check(workspace: Path, command: list[str], timeout: int) -> dict[str, object]:
    if not command or any(not isinstance(part, str) or not part for part in command):
        raise RunnerError("invalid_test_contract", "test commands must be non-empty argument arrays")
    started = time.monotonic()
    try:
        process = subprocess.run(command, cwd=workspace, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False)
        timed_out = False
        code = process.returncode
        stdout, stderr = process.stdout, process.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out, code, stdout, stderr = True, None, exc.stdout or "", exc.stderr or ""
    return {"command": command, "exit_code": code, "timed_out": timed_out, "duration_seconds": round(time.monotonic() - started, 3), "stdout_digest": hashlib.sha256(str(stdout).encode()).hexdigest(), "stderr_digest": hashlib.sha256(str(stderr).encode()).hexdigest(), "passed": not timed_out and code == 0}


def verify_candidate(*, workspace: Path, candidate_sha: str, acceptance_version: str, checks: list[dict[str, Any]], acceptance: list[str]) -> dict[str, object]:
    if not acceptance or any(not isinstance(value, str) or not value for value in acceptance):
        raise RunnerError("missing_acceptance_coverage", "candidate verification requires acceptance IDs")
    actual_before = git_sha(workspace)
    if actual_before != candidate_sha:
        raise RunnerError("candidate_sha_mismatch", "workspace HEAD differs from candidate SHA")
    results = []
    covered: set[str] = set()
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("command"), list):
            raise RunnerError("invalid_test_contract", "trusted checks need command arrays")
        ids = check.get("acceptance", [])
        if not isinstance(ids, list) or any(item not in acceptance for item in ids):
            raise RunnerError("invalid_test_contract", "check acceptance mapping is invalid")
        covered.update(ids)
        result = _run_check(workspace, check["command"], int(check.get("timeout_seconds", 120)))
        result["acceptance"] = ids
        results.append(result)
    actual_after = git_sha(workspace)
    if actual_after != candidate_sha or _git(workspace, "status", "--porcelain"):
        raise RunnerError("candidate_changed_during_verification", "candidate changed while checks ran")
    if set(acceptance) - covered:
        raise RunnerError("missing_acceptance_coverage", "acceptance IDs lack trusted evidence", details={"missing": sorted(set(acceptance)-covered)})
    if not results or not all(result["passed"] for result in results):
        raise RunnerError("candidate_verification_failed", "one or more required candidate checks failed", details={"checks": results})
    return {"schema_version": "spec-runner-candidate-receipt/v1", "candidate_sha": candidate_sha, "acceptance_version": acceptance_version, "checks": results, "test_plan_digest": digest(checks), "environment": {"os_name": os.name, "python": os.sys.version.split()[0]}, "outcome": "verified"}


def validate_review(*, result: dict[str, Any], candidate_sha: str, acceptance_version: str, blocking_severity: set[str] = frozenset({"critical", "high"})) -> dict[str, object]:
    if result.get("schema_version") != "spec-runner-review-result/v1" or result.get("candidate_sha") != candidate_sha or result.get("acceptance_version") != acceptance_version:
        raise RunnerError("invalid_review", "review result does not bind the verified candidate and acceptance version")
    findings = result.get("findings")
    if not isinstance(findings, list):
        raise RunnerError("invalid_review", "review requires a findings list")
    for finding in findings:
        if (not isinstance(finding, dict)
            or finding.get("severity") not in {"critical", "high", "medium", "low", "info"}
            or finding.get("status") not in {"open", "resolved"}
            or not isinstance(finding.get("description"), str)
            or not finding["description"].strip()):
            raise RunnerError("invalid_review", "each finding needs a known severity, status and description")
    blocking = [finding for finding in findings if isinstance(finding, dict) and finding.get("severity") in blocking_severity and finding.get("status") != "resolved"]
    return {"candidate_sha": candidate_sha, "findings": findings, "blocking": blocking, "approved": not blocking, "review_digest": digest(result)}


def merge_local(*, repository: Path, candidate_branch: str, target_ref: str, expected_target_sha: str, workspace_root: Path, run_id: str) -> dict[str, object]:
    """Merge in a disposable managed worktree and never reset a user checkout."""
    if git_sha(repository, target_ref) != expected_target_sha:
        raise RunnerError("target_ref_changed", "target ref moved before local merge")
    merge_root = workspace_root / "merge"
    merge_root.mkdir(parents=True, exist_ok=True)
    workspace = _safe_child(merge_root, merge_root / f"{run_id[:8]}")
    if workspace.exists():
        raise RunnerError("workspace_path_conflict", "merge workspace already exists")
    _git(repository, "worktree", "add", "--detach", os.fspath(workspace), expected_target_sha)
    cleanup: dict[str, object] = {"outcome": "not_attempted"}
    merged = False
    try:
        _git(workspace, "merge", "--no-ff", "--no-edit", candidate_branch)
        merge_sha = git_sha(workspace)
        _git(repository, "update-ref", target_ref, merge_sha, expected_target_sha)
        merged = True
    except RunnerError:
        raise
    finally:
        # Preserve a conflicted/dirty workspace for diagnosis. A cleanup lock
        # must not turn a durable merge into an unknown external outcome.
        if merged and workspace.is_dir():
            try:
                if not _git(workspace, "status", "--porcelain"):
                    _remove_managed_worktree(repository=repository, workspace=workspace)
                    cleanup = {"outcome": "cleaned", "workspace": os.fspath(workspace)}
                else:
                    cleanup = {"outcome": "pending", "reason": "merge_workspace_dirty", "workspace": os.fspath(workspace)}
            except RunnerError as exc:
                cleanup = {"outcome": "pending", "reason": "merge_workspace_remove_failed", "error_code": exc.code, "workspace": os.fspath(workspace)}
    return {"schema_version": "spec-runner-local-merge/v1", "target_ref": target_ref, "tested_head": _git(repository, "rev-parse", candidate_branch), "previous_target_sha": expected_target_sha, "merge_sha": git_sha(repository, target_ref), "outcome": "merged", "cleanup": cleanup}
