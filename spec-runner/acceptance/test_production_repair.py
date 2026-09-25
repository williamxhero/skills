"""Repair failure contracts: simulated SDK, real Store, no repository writes."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from spec_runner import workflow
from spec_runner.codex_adapter import CodexWorkerResult
from spec_runner.config import RunnerConfig
from spec_runner.errors import RunnerError
from spec_runner.store import RunRecord, Store, now


def test_recovery_rechecks_committed_repair_with_environment_blocker(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    scope = workspace / "scope"
    scope.mkdir()
    artifact = scope / "result.py"
    artifact.write_text("result = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "--all"], check=True)
    commit = ["git", "-C", str(workspace), "-c", "user.name=Spec Runner",
              "-c", "user.email=spec-runner@localhost", "commit", "-qm"]
    subprocess.run([*commit, "base"], check=True)
    base_sha = workflow.git_sha(workspace)
    artifact.write_text("result = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "--all"], check=True)
    subprocess.run([*commit, "repair"], check=True)
    repaired_sha = workflow.git_sha(workspace)

    config = RunnerConfig(workspace, "HEAD", Path("artifacts"), "codex_sdk", ("production",),
        "fake", "high", (Path("artifacts"),), None, None, (), "production", "config",
        acceptance_paths=("scope",))
    result = CodexWorkerResult("owner", "repair-turn", "completed", None, json.dumps({
        "outcome": "completed", "artifacts": ["result.py"],
        "blockers": ["worker sandbox could not run tests"], "questions": [],
    }), 1, 1, 2)
    verified = []

    def verify(**kwargs):
        verified.append(kwargs["candidate_sha"])
        return {"outcome": "verified", "candidate_sha": kwargs["candidate_sha"]}

    monkeypatch.setattr(workflow, "verify_candidate", verify)
    completed = []
    store = SimpleNamespace(complete_codex_stage=lambda *args, **kwargs: completed.append((args, kwargs)))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    candidate, receipt = workflow._finish_repair_candidate_result(
        control_root=tmp_path, config=config, run=SimpleNamespace(run_id="repair-run"),
        store=store, ticket_plan={"spec_key": "S1", "digest": "v1", "base_sha": base_sha},
        workspace=workspace, result=result, operation="repair-op", worker="repair-worker",
        artifact_directory=artifacts, adopt_existing=True, allow_blocked=True,
    )
    assert candidate == repaired_sha
    assert verified == [repaired_sha]
    assert receipt["outcome"] == "verified"
    assert len(completed) == 1


@pytest.mark.parametrize("case,expected", [
    ("failed", "worker_not_successful"),
    ("needs_input", "implementation_not_ready"),
    ("blocked", "implementation_not_ready"),
    ("wrong_owner", "repair_owner_changed"),
    ("missing_artifact", "implementation_artifact_invalid"),
    ("no_progress", "repair_no_progress"),
])
def test_repair_cannot_commit_unverified_semantic_result(monkeypatch, case, expected):
    runtime = Path(__file__).parent / ".runtime"
    runtime.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="repair-", dir=runtime) as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)
        subprocess.run(["git", "-C", str(workspace), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(workspace), "config", "user.name", "Spec Runner Test"], check=True)
        scope = workspace / "scope"
        scope.mkdir()
        (scope / "result.py").write_text("result = 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(workspace), "add", "."], check=True)
        subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "base"], check=True)
        artifacts = root / "artifacts"
        artifacts.mkdir()
        config = RunnerConfig(workspace, "HEAD", Path("artifacts"), "codex_sdk", ("production",),
            "fake", "high", (Path("artifacts"),), None, None, (), "production", "config",
            acceptance_paths=("scope",))
        store = Store.open(root, create=True)
        timestamp = now()
        run = RunRecord("repair-run", "repair-launch", "brief", "config", str(workspace), "HEAD", "artifacts",
            "codex_sdk", "reviewed", "codex_review", "logs/repair.jsonl", timestamp, timestamp)
        store.create_run(run, "start:repair-run")
        document = {"outcome": "needs_input" if case == "needs_input" else "completed",
            "artifacts": ["missing.py" if case == "missing_artifact" else "result.py"],
            "blockers": ["unresolved"] if case == "blocked" else [], "questions": []}
        git_calls = []

        class Fake:
            def run_semantic(self, **kwargs):
                assert kwargs["thread_id"] == "owner"
                assert kwargs["repository_path"] == scope
                assert "questions" in kwargs["schema"]["required"]
                thread = "other" if case == "wrong_owner" else "owner"
                kwargs["on_turn_started"](thread, "repair-turn")
                return CodexWorkerResult(thread, "repair-turn", "failed" if case == "failed" else "completed",
                                         None, json.dumps(document), 1, 1, 2)

        def git(repository, *args, **_kwargs):
            git_calls.append(args)
            assert args == ("status", "--porcelain"), "no git write may pass this failure gate"
            return ""

        monkeypatch.setattr(workflow, "CodexAdapter", Fake)
        monkeypatch.setattr(workflow, "_git_checked", git)
        try:
            with pytest.raises(RunnerError) as error:
                workflow._repair_candidate(control_root=root, config=config, brief_digest="brief", run=run,
                    store=store, ticket_plan={"spec_key": "S1", "digest": "v1"}, workspace=workspace,
                    findings=[{"severity": "high", "status": "open", "description": "missing validation"}],
                    implementation_thread="owner", artifact_directory=artifacts)
            assert error.value.code == expected
            assert len(git_calls) == (1 if case in {"no_progress", "blocked"} else 0)
            assert len(list(artifacts.glob("repair-worker-*.json"))) == 1
            assert store.find_by_run_id(run.run_id).state != "completed"
        finally:
            store.close()
