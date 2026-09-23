"""Repair failure contracts: simulated SDK, real Store, no repository writes."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from spec_runner import workflow
from spec_runner.codex_adapter import CodexWorkerResult
from spec_runner.config import RunnerConfig
from spec_runner.errors import RunnerError
from spec_runner.store import RunRecord, Store, now


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
        (workspace / "result.py").write_text("result = 1\n", encoding="utf-8")
        artifacts = root / "artifacts"
        artifacts.mkdir()
        config = RunnerConfig(workspace, "HEAD", Path("artifacts"), "codex_sdk", ("production",),
            "fake", "high", (Path("artifacts"),), None, None, (), "production", "config")
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
                assert "questions" in kwargs["schema"]["required"]
                thread = "other" if case == "wrong_owner" else "owner"
                kwargs["on_turn_started"](thread, "repair-turn")
                return CodexWorkerResult(thread, "repair-turn", "failed" if case == "failed" else "completed",
                                         None, json.dumps(document), 1, 1, 2)

        def git(repository, *args):
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
            assert len(git_calls) == (1 if case == "no_progress" else 0)
            assert len(list(artifacts.glob("repair-worker-*.json"))) == 1
            assert store.find_by_run_id(run.run_id).state != "completed"
        finally:
            store.close()
