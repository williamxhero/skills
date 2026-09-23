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
                              "fake", "high", (Path("artifacts"),), None, None, (), "production", "config")
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
