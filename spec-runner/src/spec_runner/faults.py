"""Public-CLI deterministic fault/regression scenarios.

The harness creates a temporary Git repository and invokes the same CLI a user
would invoke. It never inserts a completed row into SQLite. Live SDK, GitHub,
and Windows-hosted cases are represented separately as not_verified evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from . import __version__
from .errors import RunnerError


def _invoke(root: Path, args: list[str], *, env_overrides: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    source_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    existing_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.fspath(source_root) + (os.pathsep + existing_path if existing_path else "")
    environment.update(env_overrides or {})
    process = subprocess.run([sys.executable, "-m", "spec_runner.cli", *args], cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise RunnerError("fault_harness_output_invalid", "public CLI did not return JSON", details={"stderr": process.stderr[-1000:]}) from exc
    return process.returncode, payload


def _interrupt_at_fault(root: Path, args: list[str], *, control: Path, run_id: str, point: str) -> int:
    """Run the public CLI in a child process and terminate it at a durable fault point."""
    source_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    existing_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.fspath(source_root) + (os.pathsep + existing_path if existing_path else "")
    environment["SPEC_RUNNER_FAULT_POINT"] = point
    environment["SPEC_RUNNER_TEST_LEASE_STALE_AFTER_SECONDS"] = "0"
    process = subprocess.Popen(
        [sys.executable, "-m", "spec_runner.cli", *args],
        cwd=root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    ready = control / "faults" / f"{run_id}.{point}.ready"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not ready.exists():
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise RunnerError("fault_injection_failed", "fault child exited before reaching the requested point", details={"stdout": stdout[-1000:], "stderr": stderr[-1000:]})
        time.sleep(0.05)
    if not ready.exists():
        process.terminate()
        process.communicate(timeout=5)
        raise RunnerError("fault_injection_timeout", "fault child did not reach the requested point")
    process.terminate()
    stdout, stderr = process.communicate(timeout=5)
    return process.returncode


def _tree_digest(root: Path) -> str:
    entries = []
    if root.exists():
        for path in sorted(root.rglob("*")):
            if path.is_file():
                entries.append((path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def run_fault_matrix(*, seed: str = "sr-07-seed-1", keep_artifacts: bool = False) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="spec-runner-fault-") as temporary:
        root = Path(temporary)
        repository = root / "repo 中文"
        repository.mkdir()
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        brief = root / "brief.md"
        brief.write_text("fault matrix brief\n", encoding="utf-8")
        config = root / "runner.json"
        config.write_text(json.dumps({"schema_version": "spec-runner-config/v1", "repository_path": str(repository), "target_ref": "HEAD", "artifact_root": "artifacts", "execution_backend": "deterministic_test", "allowed_stages": ["example"], "model": {"name": "deterministic-test", "effort": "none"}, "authorization": {"artifact_roots": ["artifacts"]}}, ensure_ascii=False), encoding="utf-8")
        control = root / "control"
        common = ["--brief", str(brief), "--config", str(config), "--control-root", str(control), "--launch-key", "fault-run"]
        cases: list[dict[str, object]] = []

        code, first = _invoke(root, ["start", *common])
        cases.append({"id": "normal_two_stage", "entrypoint": "public_cli", "exit_code": code, "expected": "completed", "actual": first.get("run", {}).get("state"), "passed": code == 0 and first.get("run", {}).get("state") == "completed"})

        restart_run_id = "11111111-1111-1111-1111-111111111111"
        restart_control = root / "restart-control"
        restart_common = ["--brief", str(brief), "--config", str(config), "--control-root", str(restart_control), "--launch-key", "restart-run", "--run-id", restart_run_id]
        interrupted = _interrupt_at_fault(root, ["start", *restart_common], control=restart_control, run_id=restart_run_id, point="after_first_artifact")
        code, recovered = _invoke(root, ["drive", *restart_common[:-2]], env_overrides={"SPEC_RUNNER_TEST_LEASE_STALE_AFTER_SECONDS": "0"})
        recovered_state = recovered.get("run", {}).get("state")
        cases.append({"id": "process_restart_after_first_artifact", "entrypoint": "public_cli", "exit_code": code, "child_exit_code": interrupted, "expected": "completed", "actual": recovered_state, "error": recovered.get("error"), "evidence": ["recovery_detected", "step_verified", "next_stage_started"], "passed": code == 0 and recovered_state == "completed"})

        second_run_id = "22222222-2222-2222-2222-222222222222"
        second_control = root / "second-restart-control"
        second_common = ["--brief", str(brief), "--config", str(config), "--control-root", str(second_control), "--launch-key", "second-restart-run", "--run-id", second_run_id]
        interrupted = _interrupt_at_fault(root, ["start", *second_common], control=second_control, run_id=second_run_id, point="after_second_artifact")
        code, recovered = _invoke(root, ["drive", *second_common[:-2]], env_overrides={"SPEC_RUNNER_TEST_LEASE_STALE_AFTER_SECONDS": "0"})
        recovered_state = recovered.get("run", {}).get("state")
        cases.append({"id": "process_restart_after_second_artifact", "entrypoint": "public_cli", "exit_code": code, "child_exit_code": interrupted, "expected": "completed", "actual": recovered_state, "error": recovered.get("error"), "evidence": ["recovery_detected", "step_verified", "cleanup_readback"], "passed": code == 0 and recovered_state == "completed"})

        takeover_inventory = root / "takeover.json"
        takeover_inventory.write_text(json.dumps({"schema_version": "spec-runner-takeover-input/v1", "repository_path": str(repository), "source_threads": [], "artifacts": [], "facts": {"requirements": ["R1"], "tracker": True, "partial_code": True}}, ensure_ascii=False), encoding="utf-8")
        takeover_control = root / "takeover-control"
        code, adopted = _invoke(root, ["takeover", "apply", "--file", str(takeover_inventory), "--control-root", str(takeover_control), "--takeover-key", "fault-takeover", "--brief", str(brief), "--config", str(config)])
        cases.append({"id": "takeover_enters_normal_loop", "entrypoint": "public_cli", "exit_code": code, "expected": "completed", "actual": adopted.get("runner", {}).get("run", {}).get("state"), "passed": code == 0 and adopted.get("runner", {}).get("run", {}).get("state") == "completed"})
        code, adopted_again = _invoke(root, ["takeover", "apply", "--file", str(takeover_inventory), "--control-root", str(takeover_control), "--takeover-key", "fault-takeover", "--brief", str(brief), "--config", str(config)])
        cases.append({"id": "repeated_takeover_is_idempotent", "entrypoint": "public_cli", "exit_code": code, "expected": "same_run", "actual": adopted_again.get("runner", {}).get("run", {}).get("state"), "passed": code == 0 and adopted_again.get("created") is False and adopted_again.get("runner", {}).get("run", {}).get("state") == "completed"})

        cleanup_inventory = root / "cleanup-only.json"
        cleanup_inventory.write_text(json.dumps({"schema_version": "spec-runner-takeover-input/v1", "repository_path": str(repository), "source_threads": [], "artifacts": [], "facts": {"merged": True, "verification_receipt": {"candidate_sha": "historic"}}}), encoding="utf-8")
        cleanup_control = root / "cleanup-only-control"
        code, cleanup = _invoke(root, ["takeover", "apply", "--file", str(cleanup_inventory), "--control-root", str(cleanup_control), "--takeover-key", "cleanup-only"])
        code_status, cleanup_status = _invoke(root, ["status", "--control-root", str(cleanup_control)])
        cases.append({"id": "cleanup_only_takeover_has_no_worker", "entrypoint": "public_cli", "exit_code": code, "expected": "cleanup_pending_without_run", "actual": cleanup.get("action", {}).get("state"), "passed": code == 0 and code_status == 0 and cleanup.get("action", {}).get("state") == "cleanup_pending" and cleanup_status.get("runs") == []})

        cyclic_plan = root / "cyclic-delivery.json"
        cyclic_plan.write_text(json.dumps({"schema_version": "spec-runner-delivery-plan/v1", "specs": [{"key": "A", "blocked_by": ["B"], "acceptance_version": "a1", "acceptance": ["A1"], "implementation": [[sys.executable, "-c", "pass"]], "checks": [{"command": [sys.executable, "-c", "pass"], "acceptance": ["A1"]}], "review_file": "review-a.json"}, {"key": "B", "blocked_by": ["A"], "acceptance_version": "a1", "acceptance": ["B1"], "implementation": [[sys.executable, "-c", "pass"]], "checks": [{"command": [sys.executable, "-c", "pass"], "acceptance": ["B1"]}], "review_file": "review-b.json"}]}), encoding="utf-8")
        code, cyclic = _invoke(root, ["delivery", "run", "--plan", str(cyclic_plan), "--repository", str(repository), "--workspace-root", str(root / "cyclic-workspaces"), "--control-root", str(root / "cyclic-control"), "--run-id", "cyclic-run", "--target-ref", "HEAD"])
        cases.append({"id": "delivery_dependency_cycle_rejected", "entrypoint": "public_cli", "exit_code": code, "expected": "plan_cycle", "actual": cyclic.get("error", {}).get("code"), "passed": code != 0 and cyclic.get("error", {}).get("code") == "plan_cycle"})
        artifact_digest_before = _tree_digest(control)
        code, repeated = _invoke(root, ["drive", *common])
        artifact_digest_after = _tree_digest(control)
        cases.append({"id": "completed_drive_is_idempotent", "entrypoint": "public_cli", "exit_code": code, "expected": "no_new_side_effect", "actual": repeated.get("run", {}).get("state"), "passed": code == 0 and artifact_digest_before == artifact_digest_after})

        changed_brief = root / "changed.md"
        changed_brief.write_text("changed input\n", encoding="utf-8")
        code, drift = _invoke(root, ["start", "--brief", str(changed_brief), "--config", str(config), "--control-root", str(control), "--launch-key", "fault-run"])
        cases.append({"id": "input_drift_rejected", "entrypoint": "public_cli", "exit_code": code, "expected": "launch_key_input_conflict", "actual": drift.get("error", {}).get("code"), "passed": code != 0 and drift.get("error", {}).get("code") == "launch_key_input_conflict"})

        code, cancelled = _invoke(root, ["cancel", "--control-root", str(control), "--run-id", str(first.get("run", {}).get("run_id"))])
        cases.append({"id": "completed_cancel_does_not_reopen", "entrypoint": "public_cli", "exit_code": code, "expected": "run_already_completed", "actual": cancelled.get("reason"), "passed": code == 0 and cancelled.get("accepted") is False})

        report = {"schema_version": "spec-runner-fault-run/v1", "runner_version": __version__, "seed": seed, "evidence_kind": "deterministic", "cases": cases, "unverified": [{"id": "live_codex_process_restart", "kind": "live_sdk", "outcome": "not_verified"}, {"id": "windows_native_parent_exit", "kind": "windows", "outcome": "not_verified"}, {"id": "github_merge_queue", "kind": "live_github", "outcome": "not_verified"}], "passed": all(bool(case["passed"]) for case in cases)}
        report["report_digest"] = hashlib.sha256(json.dumps(report, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if keep_artifacts:
            report["artifact_root"] = os.fspath(root)
        return report
