"""Exercise production cleanup replay through the public CLI on Windows."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from spec_runner.config import RunnerConfig
from spec_runner.delivery import prepare_workspace
from spec_runner.plans import validate_spec_plan, validate_ticket_plan
from spec_runner.store import RunRecord, Store, now


_LOCK_HOLDER = r'''
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

path, ready, release = map(Path, sys.argv[1:])
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
create_file = kernel32.CreateFileW
create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
create_file.restype = wintypes.HANDLE
handle = create_file(str(path), 0x80000000, 0x00000001 | 0x00000002, None, 3, 0, None)
if handle == wintypes.HANDLE(-1).value:
    raise ctypes.WinError(ctypes.get_last_error())
ready.write_text("ready", encoding="utf-8")
while not release.exists():
    time.sleep(0.01)
if not kernel32.CloseHandle(wintypes.HANDLE(handle)):
    raise ctypes.WinError(ctypes.get_last_error())
'''


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args], check=True,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return result.stdout.strip()


def _invoke(args: list[str], cwd: Path, env: dict[str, str]) -> tuple[int, dict[str, Any]]:
    process = subprocess.run(
        [sys.executable, "-m", "spec_runner.cli", *args], cwd=cwd, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    if not process.stdout.strip():
        raise RuntimeError(f"public CLI returned no JSON: {process.stderr[-1000:]}")
    return process.returncode, json.loads(process.stdout)


def _json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def _seed_merged_delivery(repository: Path, control: Path, brief: Path, config_file: Path, run_id: str) -> dict[str, Any]:
    repository.mkdir(parents=True)
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.name", "Spec Runner Acceptance")
    _git(repository, "config", "user.email", "acceptance@example.invalid")
    _write(repository / "tracked.txt", "base\n")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-qm", "base")

    workspace_root = control / "delivery-workspaces"
    prepared = prepare_workspace(
        repository=repository, workspace_root=workspace_root, run_id=run_id,
        spec_key="S1", base_ref="refs/heads/main",
    )
    workspace = Path(str(prepared["workspace"]))
    _write(workspace / "tracked.txt", "merged candidate\n")
    _git(workspace, "add", "tracked.txt")
    _git(workspace, "commit", "-qm", "candidate S1")
    candidate_sha = _git(workspace, "rev-parse", "HEAD")
    merge_base_sha = str(prepared["base_sha"])
    _git(repository, "merge", "--no-ff", str(prepared["branch"]), "-m", "merge S1")
    merge_sha = _git(repository, "rev-parse", "refs/heads/main")
    _git(repository, "merge-base", "--is-ancestor", candidate_sha, merge_sha)

    config_document = {
        "schema_version": "spec-runner-config/v1",
        "repository_path": str(repository),
        "target_ref": "refs/heads/main",
        "artifact_root": "artifacts",
        "execution_backend": "deterministic_test",
        "allowed_stages": ["example"],
        "model": {"name": "deterministic-test", "effort": "none"},
        "authorization": {"artifact_roots": ["artifacts"]},
        "workflow": {"mode": "production"},
    }
    _json(config_file, config_document)
    config = RunnerConfig.from_file(config_file, control)
    _write(brief, "# Windows cleanup replay acceptance\n")

    spec_plan = validate_spec_plan({
        "schema_version": "spec-runner-spec-plan/v1",
        "requirements": ["R1"],
        "specs": [{
            "key": "S1", "title": "Cleanup replay", "body": "Retry cleanup after merge.",
            "blocked_by": [], "covers": ["R1"],
            "route": {"model": "deterministic-test", "effort": "none", "reason": "acceptance fixture"},
        }],
    })
    ticket_plan = validate_ticket_plan({
        "schema_version": "spec-runner-ticket-plan/v1",
        "spec_key": "S1", "base_sha": merge_base_sha,
        "tickets": [{"key": "S1.1", "title": "Cleanup", "body": "Verify cleanup replay.",
                     "blocked_by": [], "acceptance": ["R1"]}],
    }, expected_spec_key="S1")
    artifact = control / "artifacts" / run_id
    _json(artifact / "spec-plan.json", spec_plan)
    _json(artifact / "ticket-plan-S1.json", ticket_plan)
    delivery = {
        "schema_version": "spec-runner-production-delivery/v1",
        "run_id": run_id,
        "spec_key": "S1",
        "plan_digest": spec_plan["digest"],
        "ticket_plan_digest": ticket_plan["digest"],
        "candidate": {"candidate_sha": candidate_sha, "base_sha": merge_base_sha},
        "review": {"approved": True, "outcome": "approved"},
        "merge": {"merged": True, "merge_commit": merge_sha, "target_ref": "refs/heads/main"},
        "cleanup": {"outcome": "pending", "workspace": str(workspace), "manifest": str(prepared["manifest"])},
    }
    _json(artifact / "delivery-S1.json", delivery)

    timestamp = now()
    store = Store.open(control, create=True)
    try:
        store.create_run(RunRecord(
            run_id=run_id, launch_key="windows-cleanup-replay",
            input_digest=hashlib.sha256(brief.read_bytes()).hexdigest(), config_digest=config.digest,
            repository_path=str(repository), target_ref="refs/heads/main", artifact_root="artifacts",
            backend_kind="deterministic_test", state="cleanup_pending", current_step="production_delivery",
            log_path=f"logs/{run_id}.jsonl", created_at=timestamp, updated_at=timestamp,
        ), f"start:{run_id}")
    finally:
        store.close()
    return {
        "workspace": workspace,
        "manifest": Path(str(prepared["manifest"])),
        "candidate_sha": candidate_sha,
        "merge_sha": merge_sha,
        "receipt": delivery,
    }


def validate_report(report: dict[str, Any]) -> None:
    if report.get("evidence_kind") != "observed_windows_production_cleanup_replay" or report.get("platform") != "Windows":
        raise AssertionError("report must identify a native Windows production cleanup replay")
    if report.get("lock_holder_ready") is not True or report.get("first_public_start_state") != "cleanup_pending":
        raise AssertionError("the public start must retain cleanup_pending while the real delete-denying handle is held")
    if report.get("workspace_retained_while_locked") is not True or report.get("manifest_retained_while_locked") is not True:
        raise AssertionError("the locked cleanup must retain the same managed workspace and manifest")
    if report.get("same_run_replayed") is not True or report.get("final_state") != "completed":
        raise AssertionError("retry must complete the same persisted run")
    if report.get("workspace_removed") is not True or report.get("manifest_removed") is not True:
        raise AssertionError("the successful retry must remove the exact managed workspace and manifest")
    if report.get("main_head_unchanged") is not True or report.get("merge_receipt_unchanged") is not True:
        raise AssertionError("cleanup retry must preserve the already merged commit and receipt")
    if (report.get("worker_count_unchanged") is not True or report.get("operation_count_unchanged") is not True
            or report.get("step_count_unchanged") is not True):
        raise AssertionError("cleanup retry must not create another worker, operation or step")
    if report.get("delivery_cleanup_outcome") != "cleaned":
        raise AssertionError("the durable delivery receipt must record successful cleanup")


def run_probe(output: Path | None = None) -> dict[str, Any]:
    if platform.system() != "Windows":
        raise RuntimeError("native Windows cleanup replay probe requires Windows")
    source_root = Path(__file__).resolve().parents[2] / "src"
    with tempfile.TemporaryDirectory(prefix="spec runner 中文 ") as directory:
        root = Path(directory)
        repository = root / "仓库 with spaces"
        control = root / "控制 root"
        brief = root / "brief.md"
        config_file = root / "runner.json"
        run_id = str(uuid.uuid4())
        seeded = _seed_merged_delivery(repository, control, brief, config_file, run_id)
        workspace = seeded["workspace"]
        manifest = seeded["manifest"]
        locked_file = workspace / "tracked.txt"
        ready = root / "holder.ready"
        release = root / "holder.release"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(source_root)
        holder = subprocess.Popen(
            [sys.executable, "-c", _LOCK_HOLDER, str(locked_file), str(ready), str(release)],
            cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 10
            while not ready.is_file() and holder.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            if holder.poll() is not None or not ready.is_file():
                raise RuntimeError("independent Win32 lock holder failed to acquire the candidate file")
            main_head_before = _git(repository, "rev-parse", "refs/heads/main")
            start_code, first = _invoke([
                "start", "--brief", str(brief), "--config", str(config_file),
                "--control-root", str(control), "--launch-key", "windows-cleanup-replay",
            ], root, env)
            if start_code != 0 or first.get("state") != "cleanup_pending" or first.get("created") is not False:
                raise RuntimeError(f"public cleanup attempt was not safely pending: {first}")
            workspace_retained_while_locked = workspace.is_dir()
            manifest_retained_while_locked = manifest.is_file()
            first_code, first_status = _invoke(["status", "--control-root", str(control), "--run-id", run_id], root, env)
            if first_code != 0:
                raise RuntimeError("public status failed after locked cleanup attempt")
        finally:
            release.touch()
            try:
                holder.wait(timeout=10)
            except subprocess.TimeoutExpired:
                holder.terminate()
                holder.wait(timeout=5)
                raise RuntimeError("known lock-holder process did not exit after release")
        if holder.returncode != 0:
            raise RuntimeError("known Win32 lock-holder process did not close its handle cleanly")

        retry_code, retried = _invoke([
            "start", "--brief", str(brief), "--config", str(config_file),
            "--control-root", str(control), "--launch-key", "windows-cleanup-replay",
        ], root, env)
        if retry_code != 0:
            raise RuntimeError(f"public cleanup retry failed: {retried}")
        final_code, final_status = _invoke(["status", "--control-root", str(control), "--run-id", run_id], root, env)
        if final_code != 0:
            raise RuntimeError("public status failed after cleanup retry")
        artifact = control / "artifacts" / run_id
        final_delivery = json.loads((artifact / "delivery-S1.json").read_text(encoding="utf-8"))
        final_merge_sha = _git(repository, "rev-parse", "refs/heads/main")
        before_workers = first_status.get("workers", [])
        after_workers = final_status.get("workers", [])
        before_operations = first_status.get("operations", [])
        after_operations = final_status.get("operations", [])
        before_steps = first_status.get("steps", [])
        after_steps = final_status.get("steps", [])
        report = {
            "schema_version": "spec-runner-windows-production-cleanup-replay/v1",
            "run_marker": "SRAC-20260926-windows-cleanup-replay-01",
            "evidence_kind": "observed_windows_production_cleanup_replay",
            "platform": platform.system(),
            "python": platform.python_version(),
            "run_id": run_id,
            "lock_holder_pid": holder.pid,
            "lock_holder_ready": ready.is_file(),
            "workspace_retained_while_locked": workspace_retained_while_locked,
            "manifest_retained_while_locked": manifest_retained_while_locked,
            "first_public_start_state": first.get("state"),
            "first_run_state": first_status.get("run", {}).get("state"),
            "first_worker_count": len(before_workers),
            "first_operation_count": len(before_operations),
            "second_public_start_state": retried.get("state"),
            "same_run_replayed": retried.get("run", {}).get("run_id") == run_id,
            "final_state": final_status.get("run", {}).get("state"),
            "worker_count_after": len(after_workers),
            "operation_count_after": len(after_operations),
            "worker_count_unchanged": len(before_workers) == len(after_workers),
            "operation_count_unchanged": len(before_operations) == len(after_operations),
            "step_count_unchanged": len(before_steps) == len(after_steps),
            "candidate_sha": seeded["candidate_sha"],
            "merge_commit": seeded["merge_sha"],
            "main_head_before": main_head_before,
            "main_head_after": final_merge_sha,
            "main_head_unchanged": main_head_before == final_merge_sha == seeded["merge_sha"],
            "merge_receipt_unchanged": final_delivery.get("merge") == seeded["receipt"]["merge"],
            "delivery_cleanup_outcome": final_delivery.get("cleanup", {}).get("outcome"),
            "workspace_removed": not workspace.exists(),
            "manifest_removed": not manifest.exists(),
            "control_db_exists_after": (control / "spec-runner.sqlite3").is_file(),
            "unverified": [
                "independent launcher log handle, rotation and final cleanup",
                "control database lock combined with this cleanup replay",
                "external GitHub side-effect reconciliation",
                "remaining #266 acceptance and project-level L3-L5 gates",
            ],
        }
    validate_report(report)
    if output is not None:
        _json(output, report)
    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_probe(args.output)
    print(json.dumps({"report": str(args.output) if args.output else None,
                      "run_id": report["run_id"], "final_state": report["final_state"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
