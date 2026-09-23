"""Production orchestration contracts; SDK responses are explicitly simulated."""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from spec_runner import workflow
from spec_runner.codex_adapter import CodexWorkerResult
from spec_runner.config import RunnerConfig
from spec_runner.errors import RunnerError
from spec_runner.store import RunRecord, Store, now
from spec_runner.tracker import read_local
from spec_runner.plans import validate_spec_plan, validate_ticket_plan


@pytest.fixture
def context():
    runtime = Path(__file__).parent / ".runtime"
    runtime.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="planning-", dir=runtime) as directory:
        root = Path(directory)
        repository = Path(__file__).resolve().parents[2]
        config = RunnerConfig(repository, "HEAD", Path("artifacts"), "codex_sdk", ("production",),
                              "fake", "high", (Path("artifacts"),), None, None, (), "example", "config")
        store = Store.open(root, create=True)
        timestamp = now()
        run = RunRecord("test-production", "launch", "brief", "config", str(repository), "HEAD", "artifacts",
                        "codex_sdk", "starting", "codex_planning", "logs/test.jsonl", timestamp, timestamp)
        store.create_run(run, "start:test-production")
        try:
            yield root, config, store, run
        finally:
            store.close()


def spec_document():
    return {"outcome": "planned", "requirements": ["R1"], "questions": [], "specs": [
        {"key": "S1", "title": "Scope", "body": "Build the requested function", "blocked_by": [],
         "covers": ["R1"], "route": {"model": "fake", "effort": "high", "reason": "test"}}]}


def adapter(monkeypatch, documents, *, status="completed"):
    calls = []

    class Fake:
        def archive_and_readback(self, **kwargs):
            return {"thread_id": kwargs["thread_id"], "archived": True, "pages_read": 1}

        def run_semantic(self, **kwargs):
            calls.append(kwargs)
            thread = kwargs.get("thread_id") or f"thread-{len(calls)}"
            turn = f"turn-{len(calls)}"
            kwargs["on_turn_started"](thread, turn)
            return CodexWorkerResult(thread, turn, status, None, json.dumps(documents[len(calls)-1]), 1, 1, 2)

    monkeypatch.setattr(workflow, "CodexAdapter", Fake)
    return calls


def test_planning_persists_real_callback_identity_and_publishes_local_parent(context, monkeypatch):
    root, config, store, run = context
    calls = adapter(monkeypatch, [spec_document(), {"outcome": "planned", "questions": [], "tickets": [
        {"key": "T1", "title": 'Title: "quoted"', "body": "Implement R1", "acceptance": ["R1"], "blocked_by": []}]}])
    planned = workflow._execute_codex_planning(control_root=root, config=config, brief="Requirement", brief_digest="brief", run=run, store=store)
    plan = json.loads((root / "artifacts" / run.run_id / "spec-plan.json").read_text())
    ticketed = workflow._execute_codex_tickets(control_root=root, config=config, brief_digest="brief", run=planned, store=store, spec_plan=plan)
    assert ticketed.state == "tickets_ready"
    assert [call["phase"] for call in calls] == ["to-spec", "to-tickets"]
    assert "specs" in calls[0]["schema"]["properties"]
    assert "tickets" in calls[1]["schema"]["properties"]
    records = {item.key: item for item in read_local(root / "tracker").records}
    assert records["T1"].parent == "S1"
    workers = store.workers_for_run(run.run_id)
    assert {item["external_thread_id"] for item in workers if item["external_thread_id"]} == {"thread-1", "thread-2"}


def test_github_tracker_is_published_after_local_plan_and_keeps_body_link_mode(context, monkeypatch):
    root, config, store, run = context
    config = replace(config, github_repository="williamxhero/skills",
        github_receipt_root=root / "github-receipts")
    adapter(monkeypatch, [spec_document(), {"outcome": "planned", "questions": [], "tickets": [
        {"key": "T1", "title": "Ticket", "body": "Implement R1", "acceptance": ["R1"], "blocked_by": []}] }])
    published = []

    class FakeGitHubTracker:
        def publish_draft(self, **kwargs):
            published.append(kwargs)
            return {"created": True, "receipt": {"operation_id": kwargs["operation_id"], "complete": True}}

    monkeypatch.setattr(workflow, "GitHubTracker", FakeGitHubTracker)
    planned = workflow._execute_codex_planning(control_root=root, config=config, brief="Requirement", brief_digest="brief", run=run, store=store)
    plan = json.loads((root / "artifacts" / run.run_id / "spec-plan.json").read_text())
    workflow._execute_codex_tickets(control_root=root, config=config, brief_digest="brief", run=planned, store=store, spec_plan=plan)
    assert len(published) == 1
    assert published[0]["repository"] == "williamxhero/skills"
    assert published[0]["relation_mode"] == "body_links"
    assert published[0]["draft"]["umbrella"]["key"] == "S1"
    assert published[0]["draft"]["specs"][0]["key"] == "T1"


def test_production_queue_drives_dependency_ordered_specs_without_parent_dispatch(context, monkeypatch):
    root, config, store, run = context
    config = replace(config, workflow_mode="production")
    plan = {"digest": "plan-2", "specs": [
        {"key": "S1", "title": "First", "body": "one", "blocked_by": []},
        {"key": "S2", "title": "Second", "body": "two", "blocked_by": ["S1"]},
        {"key": "S3", "title": "Third", "body": "three", "blocked_by": ["S2"]},
    ]}
    calls = []

    def fake_tickets(*, run, spec_plan, **kwargs):
        key = spec_plan["specs"][0]["key"]
        calls.append(("tickets", key))
        artifact = root / "artifacts" / run.run_id
        artifact.mkdir(parents=True, exist_ok=True)
        (artifact / f"ticket-plan-{key}.json").write_text(json.dumps({"spec_key": key}), encoding="utf-8")
        store.set_run_state(run.run_id, "tickets_ready")
        current = store.find_by_run_id(run.run_id)
        assert current is not None
        return current

    def fake_implementation(*, run, ticket_plan, **kwargs):
        key = ticket_plan["spec_key"]
        calls.append(("implementation", key))
        store.set_run_state(run.run_id, "spec_completed")
        return {"state": "spec_completed", "spec_key": key}

    monkeypatch.setattr(workflow, "_execute_codex_tickets", fake_tickets)
    monkeypatch.setattr(workflow, "_execute_codex_implementation", fake_implementation)
    original_load_json = workflow.load_json
    def load_for_test(path):
        if path.name.startswith("ticket-plan-"):
            return {"spec_key": path.stem.removeprefix("ticket-plan-")}
        return original_load_json(path)
    monkeypatch.setattr(workflow, "load_json", load_for_test)
    result = workflow._run_production_queue(control_root=root, config=config, brief_digest="brief", run=run, store=store, spec_plan=plan)
    assert result["run"]["state"] == "completed"
    assert calls == [("tickets", "S1"), ("implementation", "S1"), ("tickets", "S2"), ("implementation", "S2"), ("tickets", "S3"), ("implementation", "S3")]
    assert json.loads((root / "artifacts" / run.run_id / "completed-specs.json").read_text())["specs"] == ["S1", "S2", "S3"]


def test_production_cleanup_pending_retries_cleanup_without_implementation(context, monkeypatch):
    root, config, store, run = context
    artifact = root / "artifacts" / run.run_id
    artifact.mkdir(parents=True)
    (artifact / "ticket-plan-S1.json").write_text(json.dumps({"spec_key": "S1"}), encoding="utf-8")
    workspaces = root / "delivery-workspaces"
    workspaces.mkdir()
    workspace = workspaces / "run-S1"
    workspace.mkdir()
    manifest = workspaces / "run-S1.manifest.json"
    manifest.write_text(json.dumps({"run_id": run.run_id, "workspace": str(workspace)}), encoding="utf-8")
    store.set_run_state(run.run_id, "cleanup_pending")
    calls = []
    monkeypatch.setattr(workflow, "cleanup_managed_workspace", lambda **kwargs: calls.append(kwargs) or {"outcome": "cleaned"})
    current = store.find_by_run_id(run.run_id)
    assert current is not None
    result = workflow._retry_production_cleanup(control_root=root, config=config, run=current, store=store)
    assert result["state"] == "spec_completed"
    assert len(calls) == 1
    assert json.loads((artifact / "completed-specs.json").read_text())["specs"] == ["S1"]


@pytest.mark.parametrize("status", ["failed", "interrupted", "unknown"])
def test_nonterminal_or_failed_planner_cannot_publish_valid_looking_plan(context, monkeypatch, status):
    root, config, store, run = context
    adapter(monkeypatch, [spec_document()], status=status)
    with pytest.raises(RunnerError):
        workflow._execute_codex_planning(control_root=root, config=config, brief="Requirement", brief_digest="brief", run=run, store=store)
    assert not (root / "artifacts" / run.run_id / "spec-plan.json").exists()


@pytest.mark.parametrize("outcome", ["failed", "no_change", "change_request"])
def test_planner_cannot_promote_non_planned_outcome(context, monkeypatch, outcome):
    root, config, store, run = context
    document = spec_document()
    document["outcome"] = outcome
    adapter(monkeypatch, [document])
    with pytest.raises(RunnerError):
        workflow._execute_codex_planning(control_root=root, config=config, brief="Requirement", brief_digest="brief", run=run, store=store)


def test_planner_business_question_waits_without_empty_plan_publication(context, monkeypatch):
    root, config, store, run = context
    adapter(monkeypatch, [{"outcome": "needs_input", "requirements": [], "specs": [],
                          "questions": [{"id": "Q1", "question": "Which format?", "options": ["csv", "json"]}]}])
    waiting = workflow._execute_codex_planning(control_root=root, config=config, brief="Requirement", brief_digest="brief", run=run, store=store)
    assert waiting.state == "needs_input"
    assert not (root / "artifacts" / run.run_id / "spec-plan.json").exists()


def test_production_brief_runs_grill_before_spec_planning(context, monkeypatch):
    root, config, store, run = context
    config = replace(config, workflow_mode="production")
    calls = adapter(monkeypatch, [{"outcome": "planned", "scope": "Implement R1 only",
        "constraints": ["fixture only"], "acceptance": ["R1 functional oracle passes"], "questions": []}, spec_document()])
    planned = workflow._execute_codex_planning(control_root=root, config=config, brief="Requirement",
        brief_digest="brief", run=run, store=store)
    assert planned.state == "planned"
    assert [call["phase"] for call in calls] == ["grill", "to-spec"]
    assert "Implement R1 only" in calls[1]["trusted"]["legacy_prompt"]
    assert (root / "artifacts" / run.run_id / "grill-handoff.json").exists()


def test_grill_question_stops_before_spec_and_resumes_same_thread(context, monkeypatch):
    root, config, store, run = context
    config = replace(config, workflow_mode="production")
    calls = adapter(monkeypatch, [
        {"outcome": "needs_input", "scope": "", "constraints": [], "acceptance": [],
         "questions": [{"id": "Q1", "question": "Which format?", "options": ["json"]}]},
        {"outcome": "planned", "scope": "JSON output", "constraints": [], "acceptance": ["Parse JSON"], "questions": []},
        spec_document(),
        {"outcome": "needs_input", "tickets": [], "questions": [{"id": "Q2", "question": "Which limits?", "options": []}]},
    ])
    waiting = workflow._execute_codex_planning(control_root=root, config=config, brief="Requirement",
        brief_digest="brief", run=run, store=store)
    assert waiting.state == "needs_input" and waiting.current_step == "codex_grill"
    assert len(calls) == 1
    store.submit_answer(run_id=run.run_id, question_id="Q1", value="json")
    resumed = workflow._resume_codex_stage(control_root=root, config=config, run=waiting,
        brief="Requirement", brief_digest="brief", store=store)
    assert resumed["run"]["current_step"] == "codex_ticket_planning"
    assert calls[1]["thread_id"] == "thread-1"
    assert calls[1]["trusted"]["answers"][0]["value"] == "json"
    assert [call["phase"] for call in calls] == ["grill", "grill", "to-spec", "to-tickets"]


@pytest.mark.parametrize("mutation", ["empty", "blank_body", "unsafe_key", "order", "empty_coverage"])
def test_spec_validation_rejects_incomplete_or_unsafe_queue(mutation):
    document = {**spec_document(), "schema_version": "spec-runner-spec-plan/v1"}
    if mutation == "empty":
        document.update(requirements=[], specs=[])
    elif mutation == "blank_body":
        document["specs"][0]["body"] = "  "
    elif mutation == "unsafe_key":
        document["specs"][0]["key"] = "../outside"
    elif mutation == "empty_coverage":
        document["specs"].append({**document["specs"][0], "key": "S2", "covers": []})
    else:
        document["specs"].append({**document["specs"][0], "key": "S2"})
        document["specs"][0]["blocked_by"] = ["S2"]
    with pytest.raises(RunnerError):
        validate_spec_plan(document)


def test_empty_ticket_plan_is_not_deliverable():
    with pytest.raises(RunnerError):
        validate_ticket_plan({"schema_version": "spec-runner-ticket-plan/v1", "spec_key": "S1", "base_sha": "abcdef0", "tickets": []})


def test_business_answer_resumes_planning_thread_and_advances_without_parent_dispatch(context, monkeypatch):
    root, config, store, run = context
    config = replace(config, workflow_mode="example")
    documents = [{"outcome": "needs_input", "requirements": [], "specs": [],
                  "questions": [{"id": "Q1", "question": "Which format?", "options": ["json"]}]},
                 spec_document(), {"outcome": "planned", "questions": [], "tickets": [
                     {"key": "T1", "title": "Ticket", "body": "Implement", "acceptance": ["R1"], "blocked_by": []}]}]
    calls = adapter(monkeypatch, documents)
    waiting = workflow._execute_codex_planning(control_root=root, config=config, brief="Requirement", brief_digest="brief", run=run, store=store)
    assert waiting.state == "needs_input"
    store.submit_answer(run_id=run.run_id, question_id="Q1", value="json")
    store.clear_control(run.run_id)
    current = store.find_by_run_id(run.run_id)
    assert current is not None
    resumed = workflow._resume_codex_stage(control_root=root, config=config, run=current, brief="Requirement", brief_digest="brief", store=store)
    assert resumed["run"]["state"] == "tickets_ready"
    assert calls[0]["thread_id"] is None
    assert calls[1]["thread_id"] == "thread-1"
    assert "Runner-recorded business answers" in calls[1]["trusted"]["legacy_prompt"]
