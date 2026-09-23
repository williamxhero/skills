from __future__ import annotations

import sys
import tempfile
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from spec_runner import workflow
from spec_runner.config import RunnerConfig
from spec_runner.errors import RunnerError
from spec_runner.store import RunRecord, Store, now


@pytest.fixture
def github_context(tmp_path: Path):
    config = RunnerConfig(tmp_path, "refs/heads/main", Path("artifacts"), "codex_sdk", ("production",),
        "fake", "high", (Path("artifacts"),), None, None, (), "production", "digest",
        github_repository="owner/repo", github_required_checks=("ci",),
        github_receipt_root=tmp_path / "receipts", github_base="refs/heads/main", github_merge_authorized=True)
    store = Store.open(tmp_path / "control", create=True)
    stamp = now()
    run = RunRecord("github-run", "github-launch", "brief", "digest", str(tmp_path), "refs/heads/main", "artifacts",
        "codex_sdk", "reviewed", "codex_review", "logs/github.jsonl", stamp, stamp)
    store.create_run(run, "start:github-run")
    yield tmp_path, config, store, run
    store.close()


def test_github_pending_is_waiting_and_does_not_merge(monkeypatch, github_context):
    root, config, store, run = github_context
    calls = []

    class FakeGitHub:
        def create_or_adopt_pr(self, **kwargs):
            calls.append("pr")
            return {"created": True, "receipt": {"number": 7}}

        def checks(self, **kwargs):
            calls.append("checks")
            return {"ready": False, "pending": ["ci"], "failed": [], "missing": [], "wrong_sha": []}

        def merge(self, **kwargs):
            calls.append("merge")
            raise AssertionError("pending CI must not merge")

    monkeypatch.setattr(workflow, "GitHubDelivery", FakeGitHub)
    monkeypatch.setattr(workflow, "_git_checked", lambda *args: "")
    result = workflow._execute_github_delivery(control_root=root, config=config, run=run, spec_key="S1",
        candidate_sha="abc1234", branch="spec-runner/S1", candidate_receipt={"candidate_sha": "abc1234"},
        review={"approved": True})
    assert result["state"] == "waiting_ci"
    assert calls == ["pr", "checks"]


def test_github_merge_requires_explicit_authorization(monkeypatch, github_context):
    root, config, store, run = github_context
    config = replace(config, github_merge_authorized=False)

    class FakeGitHub:
        def create_or_adopt_pr(self, **kwargs):
            return {"created": True, "receipt": {"number": 7}}

        def checks(self, **kwargs):
            return {"ready": True, "pending": [], "failed": [], "missing": [], "wrong_sha": []}

    monkeypatch.setattr(workflow, "GitHubDelivery", FakeGitHub)
    monkeypatch.setattr(workflow, "_git_checked", lambda *args: "")
    with pytest.raises(RunnerError, match="authorization"):
        workflow._execute_github_delivery(control_root=root, config=config, run=run, spec_key="S1",
            candidate_sha="abc1234", branch="spec-runner/S1", candidate_receipt={"candidate_sha": "abc1234"},
            review={"approved": True})


def test_waiting_ci_resume_reuses_durable_evidence_and_only_cleans_after_merge(monkeypatch, github_context):
    root, config, store, run = github_context
    artifact = root / "artifacts" / run.run_id
    artifact.mkdir(parents=True)
    (artifact / "github-S1.json").write_text(json.dumps({"spec_key": "S1", "state": "waiting_ci"}), encoding="utf-8")
    (artifact / "candidate-S1.json").write_text(json.dumps({"candidate_sha": "abc1234"}), encoding="utf-8")
    (artifact / "review-S1.json").write_text(json.dumps({"approved": True}), encoding="utf-8")
    (artifact / "spec-plan.json").write_text(json.dumps({"digest": "plan-1", "specs": [{"key": "S1"}]}), encoding="utf-8")
    (artifact / "ticket-plan-S1.json").write_text(json.dumps({"digest": "ticket-1", "spec_key": "S1"}), encoding="utf-8")
    workspaces = root / "delivery-workspaces"
    workspaces.mkdir()
    manifest = workspaces / "run.manifest.json"
    manifest.write_text(json.dumps({"run_id": run.run_id, "spec_key": "S1", "branch": "spec-runner/S1", "workspace": str(root / "workspace")}), encoding="utf-8")
    store.set_run_state(run.run_id, "waiting_ci")
    run = store.find_by_run_id(run.run_id)
    assert run is not None
    calls = []
    responses = iter([
        {"state": "waiting_ci", "spec_key": "S1"},
        {"state": "github_completed", "spec_key": "S1", "candidate": {"candidate_sha": "abc1234"},
         "review": {"approved": True}, "merge": {"merged": True}},
    ])

    def fake_delivery(**kwargs):
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(workflow, "_execute_github_delivery", fake_delivery)
    monkeypatch.setattr(workflow, "cleanup_managed_workspace", lambda **kwargs: {"outcome": "cleaned"})
    first = workflow._resume_waiting_github(control_root=root, config=config, run=run, store=store)
    assert first["state"] == "waiting_ci"
    assert calls[0]["push"] is False
    second = workflow._resume_waiting_github(control_root=root, config=config, run=run, store=store, finalize_run=False)
    assert second["state"] == "spec_completed"
    assert len(calls) == 2
    assert calls[1]["push"] is False
    assert store.find_by_run_id(run.run_id).state == "spec_completed"
    assert json.loads((artifact / "completed-specs.json").read_text())["specs"] == ["S1"]


def test_github_merge_reconciles_local_base_before_next_spec(tmp_path):
    remote = tmp_path / "remote.git"
    repository = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "master", str(repository)], check=True, capture_output=True)
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True, text=True).stdout.strip()
    git("config", "user.name", "test")
    git("config", "user.email", "test@example.invalid")
    (repository / "state.txt").write_text("base\n", encoding="utf-8")
    git("add", "state.txt")
    git("commit", "-m", "base")
    git("remote", "add", "origin", str(remote))
    git("push", "-u", "origin", "master")
    remote_clone = tmp_path / "remote-clone"
    subprocess.run(["git", "clone", str(remote), str(remote_clone)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(remote_clone), "config", "user.name", "test"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(remote_clone), "config", "user.email", "test@example.invalid"], check=True, capture_output=True)
    (remote_clone / "state.txt").write_text("merged\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(remote_clone), "add", "state.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(remote_clone), "commit", "-m", "merged"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(remote_clone), "push", "origin", "master"], check=True, capture_output=True)

    synced = workflow._reconcile_github_base(repository=repository, target_ref="refs/heads/master", base="master")
    assert synced["outcome"] == "fast_forwarded"
    assert (repository / "state.txt").read_text(encoding="utf-8") == "merged\n"
