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
from pathlib import Path
from typing import Any

from . import __version__
from .errors import RunnerError


def _invoke(root: Path, args: list[str]) -> tuple[int, dict[str, Any]]:
    source_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    existing_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.fspath(source_root) + (os.pathsep + existing_path if existing_path else "")
    process = subprocess.run([sys.executable, "-m", "spec_runner.cli", *args], cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise RunnerError("fault_harness_output_invalid", "public CLI did not return JSON", details={"stderr": process.stderr[-1000:]}) from exc
    return process.returncode, payload


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
