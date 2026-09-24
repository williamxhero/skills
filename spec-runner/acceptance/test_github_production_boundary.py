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


@pytest.mark.parametrize("merge_receipt", [{"merged": True}, {"merged": False}])
@pytest.mark.parametrize("close_fails_once", [False, True])
def test_waiting_ci_resume_reuses_durable_evidence_and_only_cleans_after_merge(
    monkeypatch, github_context, merge_receipt, close_fails_once,
):
    root, config, store, run = github_context
    artifact = root / "artifacts" / run.run_id
    artifact.mkdir(parents=True)
    candidate_sha = "a" * 40
    candidate = {"outcome": "verified", "candidate_sha": candidate_sha}
    review = {
        "approved": True, "blocking": [], "candidate_sha": candidate_sha,
        "findings": [], "review_digest": "review-digest",
    }
    (artifact / "github-S1.json").write_text(json.dumps({
        "spec_key": "S1", "state": "waiting_ci", "candidate": candidate, "review": review,
    }), encoding="utf-8")
    (artifact / "github-S0.json").write_text(json.dumps({
        "spec_key": "S0", "state": "waiting_ci", "candidate": {"candidate_sha": "b" * 40},
        "review": review,
    }), encoding="utf-8")
    store.complete_production_spec(
        run_id=run.run_id, spec_key="S0", plan_digest="plan-0", delivery_digest="delivery-0",
    )
    (artifact / "candidate-S1.json").write_text(json.dumps(candidate), encoding="utf-8")
    (artifact / f"review-S1-{candidate_sha[:12]}.json").write_text(json.dumps({
        **review, "worker": {"thread_id": "review-thread", "turn_id": "review-turn"},
    }), encoding="utf-8")
    (artifact / f"review-worker-S1-{candidate_sha[:12]}.json").write_text(json.dumps({
        "status": "completed", "final_response": "raw worker receipt without validated approval",
    }), encoding="utf-8")
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
        {"state": "waiting_ci", "spec_key": "S1", "candidate": candidate, "review": review},
        {"state": "github_completed", "spec_key": "S1", "candidate": candidate,
         "review": review, "merge": merge_receipt},
    ])

    def fake_delivery(**kwargs):
        calls.append(kwargs)
        assert kwargs["candidate_receipt"] == candidate
        assert kwargs["review"] == review
        return next(responses)

    monkeypatch.setattr(workflow, "_execute_github_delivery", fake_delivery)
    cleanup_calls = []
    sequence = []
    monkeypatch.setattr(workflow, "cleanup_managed_workspace",
                        lambda **kwargs: (cleanup_calls.append(kwargs), sequence.append("cleanup"), {"outcome": "cleaned"})[-1])
    close_calls = []
    def fake_close(**kwargs):
        close_calls.append("close")
        sequence.append("close")
        if close_fails_once and len(close_calls) == 1:
            raise RunnerError("github_close_unknown", "close response is unresolved")
        return {"complete": True}
    monkeypatch.setattr(workflow, "_close_published_ticket_plan", fake_close)
    first = workflow._resume_waiting_github(control_root=root, config=config, run=run, store=store)
    assert first["state"] == "waiting_ci"
    assert calls[0]["push"] is False
    if merge_receipt["merged"]:
        second = workflow._resume_waiting_github(control_root=root, config=config, run=run, store=store, finalize_run=False)
        if close_fails_once:
            assert second["state"] == "cleanup_pending"
            assert len(cleanup_calls) == 1
            assert store.production_completed_specs(run.run_id) == {"S0"}
            current = store.find_by_run_id(run.run_id)
            assert current is not None
            recovered = workflow._retry_production_cleanup(
                control_root=root, config=config, run=current, store=store,
            )
            assert recovered["state"] == "spec_completed"
            assert close_calls == ["close", "close"]
            assert len(cleanup_calls) == 3
            assert sequence == ["cleanup", "close", "cleanup", "close", "cleanup"]
        else:
            assert second["state"] == "spec_completed"
            assert close_calls == ["close"]
            assert len(cleanup_calls) == 2
            assert sequence == ["cleanup", "close", "cleanup"]
    else:
        with pytest.raises(RunnerError, match="confirmed merge receipt"):
            workflow._resume_waiting_github(control_root=root, config=config, run=run, store=store, finalize_run=False)
        assert close_calls == []
        assert cleanup_calls == []
    assert len(calls) == 2
    assert calls[1]["push"] is False
    if merge_receipt["merged"]:
        assert store.find_by_run_id(run.run_id).state == "spec_completed"
        assert json.loads((artifact / "completed-specs.json").read_text())["specs"] == ["S0", "S1"]
    else:
        assert store.find_by_run_id(run.run_id).state == "waiting_ci"
        assert not (artifact / "delivery-S1.json").exists()


def test_waiting_ci_resume_recovers_a_definitive_failed_candidate(monkeypatch, github_context):
    root, config, store, run = github_context
    artifact = root / "artifacts" / run.run_id
    artifact.mkdir(parents=True)
    candidate_sha = "a" * 40
    candidate = {"outcome": "verified", "candidate_sha": candidate_sha, "acceptance_version": "ticket-1"}
    review = {
        "approved": True, "blocking": [], "candidate_sha": candidate_sha,
        "findings": [], "review_digest": "review-digest",
    }
    (artifact / "github-S1.json").write_text(json.dumps({
        "spec_key": "S1", "state": "waiting_ci", "candidate": candidate, "review": review,
    }), encoding="utf-8")
    (artifact / "candidate-S1.json").write_text(json.dumps(candidate), encoding="utf-8")
    (artifact / f"review-S1-{candidate_sha[:12]}.json").write_text(json.dumps(review), encoding="utf-8")
    (artifact / "spec-plan.json").write_text(json.dumps({"digest": "plan-1", "specs": [{"key": "S1"}]}), encoding="utf-8")
    (artifact / "ticket-plan-S1.json").write_text(json.dumps({"digest": "ticket-1", "spec_key": "S1"}), encoding="utf-8")
    workspaces = root / "delivery-workspaces"
    workspaces.mkdir()
    manifest = workspaces / "run.manifest.json"
    manifest.write_text(json.dumps({"run_id": run.run_id, "spec_key": "S1", "branch": "spec-runner/S1", "workspace": str(root / "workspace")}), encoding="utf-8")
    store.set_run_state(run.run_id, "reviewed")
    run = store.find_by_run_id(run.run_id)
    assert run is not None
    failed_checks = {
        "candidate_sha": candidate_sha,
        "required": ["ci"],
        "states": {"ci": {"source": "check_run", "status": "completed", "conclusion": "failure", "sha": candidate_sha}},
        "missing": [], "wrong_sha": [], "pending": [], "failed": ["ci"], "unknown": [],
        "ready": False,
    }
    calls = []

    def fake_delivery(**kwargs):
        calls.append(kwargs)
        if kwargs["candidate_receipt"]["candidate_sha"] == candidate_sha:
            return {
                "state": "waiting_ci", "spec_key": "S1", "pr": {"number": 7},
                "checks": failed_checks, "candidate": candidate, "review": review,
            }
        return {
            "state": "github_completed", "spec_key": "S1", "pr": {"number": 8},
            "checks": {**failed_checks, "candidate_sha": "b" * 40, "failed": [], "ready": True},
            "candidate": kwargs["candidate_receipt"], "review": kwargs["review"],
            "merge": {"merged": True},
        }

    monkeypatch.setattr(workflow, "_execute_github_delivery", fake_delivery)
    recovery = {}
    recovery_workspace = workspaces / "recovered"
    recovery_manifest = workspaces / "recovered.manifest.json"
    recovered_candidate = {"outcome": "verified", "candidate_sha": "b" * 40}
    recovered_review = {
        "approved": True, "candidate_sha": "b" * 40, "blocking": [],
        "findings": [], "review_digest": "fresh",
    }

    def fake_recovery(**kwargs):
        recovery.update(kwargs)
        recovery_manifest.write_text(json.dumps({
            "run_id": run.run_id, "spec_key": "S1", "branch": "spec-runner/S1-recovered",
            "workspace": str(recovery_workspace),
        }), encoding="utf-8")
        (artifact / "candidate-S1.json").write_text(json.dumps(recovered_candidate), encoding="utf-8")
        (artifact / f"review-S1-{'b' * 12}.json").write_text(json.dumps(recovered_review), encoding="utf-8")
        return {
            "state": "waiting_ci", "spec_key": "S1", "branch": "spec-runner/S1-recovered", "pr": {"number": 8},
            "checks": {**failed_checks, "candidate_sha": "b" * 40, "states": {}, "failed": [], "pending": ["ci"], "ready": False},
            "candidate": recovered_candidate,
            "review": recovered_review,
            "_workspace_manifest": str(recovery_manifest),
        }

    monkeypatch.setattr(workflow, "_recover_failed_github_candidate", fake_recovery)
    cleanup_calls = []
    sequence = []
    monkeypatch.setattr(workflow, "cleanup_managed_workspace",
                        lambda **kwargs: (cleanup_calls.append(kwargs), sequence.append("cleanup"), {"outcome": "cleaned"})[-1])
    monkeypatch.setattr(workflow, "_close_published_ticket_plan",
                        lambda **kwargs: sequence.append("close") or {"complete": True})
    first = workflow._resume_waiting_github(control_root=root, config=config, run=run, store=store)

    assert first["state"] == "waiting_ci"
    assert first["candidate"]["candidate_sha"] == "b" * 40
    assert store.find_by_run_id(run.run_id).state == "waiting_ci"
    assert cleanup_calls == []
    assert recovery["failed_checks"] == failed_checks
    assert recovery["candidate"]["candidate_sha"] == candidate_sha

    second = workflow._resume_waiting_github(
        control_root=root, config=config, run=run, store=store, finalize_run=False,
    )

    assert second["state"] == "spec_completed"
    assert sequence == ["cleanup", "cleanup", "close", "cleanup", "cleanup"]
    assert len(calls) == 2
    assert {Path(call["workspace"]) for call in cleanup_calls} == {root / "workspace", recovery_workspace}
    assert all(call["manifest"] in {manifest, recovery_manifest} for call in cleanup_calls)


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
