from __future__ import annotations

import sys
import tempfile
import json
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
        {"state": "github_completed", "spec_key": "S1"},
    ])

    def fake_delivery(**kwargs):
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(workflow, "_execute_github_delivery", fake_delivery)
    monkeypatch.setattr(workflow, "cleanup_managed_workspace", lambda **kwargs: {"outcome": "cleaned"})
    first = workflow._resume_waiting_github(control_root=root, config=config, run=run, store=store)
    assert first["state"] == "waiting_ci"
    assert calls[0]["push"] is False
    second = workflow._resume_waiting_github(control_root=root, config=config, run=run, store=store)
    assert second["state"] == "github_completed"
    assert len(calls) == 2
    assert calls[1]["push"] is False
    assert store.find_by_run_id(run.run_id).state == "completed"
