"""Production orchestration contracts; SDK responses are explicitly simulated."""
from __future__ import annotations

import json
import hashlib
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
from spec_runner.production_gates import implementation_artifacts


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


def assert_strict_json_schema(schema):
    """The live Codex SDK requires every object property to be required."""
    if not isinstance(schema, dict):
        return
    if schema.get("type") == "object":
        properties = schema.get("properties", {})
        assert schema.get("additionalProperties") is False
        assert set(properties) == set(schema.get("required", []))
        for child in properties.values():
            assert_strict_json_schema(child)
    elif schema.get("type") == "array":
        assert_strict_json_schema(schema.get("items"))


def test_production_worker_schemas_are_strict_json_schema(context, monkeypatch):
    root, config, store, run = context
    calls = adapter(monkeypatch, [spec_document(), {"outcome": "planned", "questions": [], "tickets": [
        {"key": "T1", "title": "Ticket", "body": "Implement R1", "acceptance": ["R1"], "blocked_by": []}]}])
    planned = workflow._execute_codex_planning(control_root=root, config=config, brief="Requirement", brief_digest="brief", run=run, store=store)
    plan = json.loads((root / "artifacts" / run.run_id / "spec-plan.json").read_text())
    workflow._execute_codex_tickets(control_root=root, config=config, brief_digest="brief", run=planned, store=store, spec_plan=plan)
    for call in calls:
        assert_strict_json_schema(call["schema"])


def test_planning_prompt_binds_covers_to_exact_requirements(context, monkeypatch):
    root, config, store, run = context
    calls = adapter(monkeypatch, [spec_document()])
    workflow._execute_codex_planning(control_root=root, config=config, brief="Requirement", brief_digest="brief", run=run, store=store)
    prompt = calls[0]["trusted"]["legacy_prompt"]
    assert "covers list must contain only exact strings copied from that requirements list" in prompt
    assert "Every requirement must appear in at least one covers list" in prompt


def test_production_implementation_prompt_keeps_checks_and_other_runners_outside_worker(monkeypatch, context):
    root, config, store, run = context
    config = replace(
        config,
        workflow_mode="production",
        acceptance_ids=("A1",),
        acceptance_checks=({"command": ["python", "-m", "pytest"], "acceptance": ["A1"]},),
        acceptance_paths=("fixture-app/run-1",),
    )
    workspace = root / "workspace"
    (workspace / "fixture-app" / "run-1").mkdir(parents=True)
    monkeypatch.setattr(workflow, "prepare_workspace", lambda **kwargs: {
        "workspace": str(workspace),
        "base_sha": "base",
        "branch": "spec-runner/S1",
        "manifest": str(root / "workspace.manifest.json"),
    })
    captured = {}

    def fake_worker(**kwargs):
        captured["prompt"] = kwargs["prompt"]
        kwargs["on_turn_started"]("implementation-thread", "implementation-turn")
        return CodexWorkerResult(
            "implementation-thread", "implementation-turn", "completed", None,
            json.dumps({
                "outcome": "needs_input",
                "questions": [{"id": "Q1", "question": "Need a fact?", "options": []}],
                "artifacts": [],
                "blockers": [],
            }),
            1, 1, 2,
        )

    monkeypatch.setattr(workflow, "_run_worker", fake_worker)
    result = workflow._execute_codex_implementation(
        control_root=root,
        config=config,
        brief_digest="brief",
        run=run,
        store=store,
        ticket_plan={"spec_key": "S1", "digest": "ticket-digest", "tickets": []},
    )
    assert result["state"] == "needs_input"
    assert "Do not run repository-wide test discovery" in captured["prompt"]
    assert "start another Runner" in captured["prompt"]
    assert "Runner will execute the exact trusted acceptance checks" in captured["prompt"]


def test_completed_worker_blocker_is_deferred_to_trusted_candidate_gate(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifact = workspace / "result.py"
    artifact.write_text("value = 1\n", encoding="utf-8")
    result = CodexWorkerResult(
        "implementation-thread", "implementation-turn", "completed", None,
        json.dumps({
            "outcome": "completed",
            "artifacts": ["result.py"],
            "blockers": ["worker sandbox could not run its focused check"],
            "questions": [],
        }),
        1, 1, 2,
    )
    document = implementation_artifacts(
        result, workspace, allow_completed_blockers=True,
    )
    assert document["artifacts"] == ["result.py"]
    with pytest.raises(RunnerError, match="incomplete or requires input"):
        implementation_artifacts(result, workspace)


def test_blocked_run_can_reconcile_completed_failed_implementation_worker(context, monkeypatch):
    root, config, store, run = context
    run = replace(run, state="blocked", current_step="codex_implementation")
    spec_key = "S1"
    thread_id = "implementation-thread"
    turn_id = "implementation-turn"
    worker = {
        "backend_kind": "codex_sdk",
        "state": "failed",
        "worker_id": f"codex_sdk:{run.run_id}:codex_implementation:{spec_key}",
        "external_thread_id": thread_id,
        "external_turn_id": turn_id,
    }
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "task.py").write_text("value = 1\n", encoding="utf-8")
    artifact = root / "artifacts" / run.run_id
    artifact.mkdir(parents=True)
    (artifact / f"implementation-{spec_key}.json").write_text(json.dumps({
        "thread_id": thread_id,
        "turn_id": turn_id,
        "status": "completed",
        "error": None,
        "final_response": json.dumps({
            "outcome": "completed",
            "artifacts": ["task.py"],
            "blockers": ["worker sandbox could not run its focused check"],
            "questions": [],
        }),
    }), encoding="utf-8")
    monkeypatch.setattr(store, "workers_for_run", lambda run_id: [worker])
    monkeypatch.setattr(workflow, "_safe_artifact_directory", lambda *args, **kwargs: artifact)
    monkeypatch.setattr(workflow, "_implementation_workspace_path", lambda *args, **kwargs: workspace)
    monkeypatch.setattr(workflow, "_git_checked", lambda *args, **kwargs: "")

    class CompletedThread:
        def read_thread(self, **kwargs):
            return {
                "thread_id": thread_id,
                "thread_status": "idle",
                "started_turn": False,
                "active_flags": [],
                "turn_count": 1,
                "turns": [{"turn_id": turn_id, "status": "completed"}],
            }

    monkeypatch.setattr(workflow, "CodexAdapter", CompletedThread)

    assert workflow._blocked_implementation_retry_identity(
        control_root=root, config=config, run=run, store=store,
    ) == (thread_id, spec_key)


@pytest.mark.parametrize(
    ("run_state", "worker_state"),
    [("blocked", "running"), ("failed", "failed")],
)
def test_interrupted_implementation_resumes_same_thread_without_adopting_partial_work(
    context, monkeypatch, run_state, worker_state,
):
    root, config, store, original_run = context
    run = replace(original_run, state=run_state, current_step="codex_implementation")
    spec_key = "S1"
    thread_id = "interrupted-implementation-thread"
    turn_id = "interrupted-implementation-turn"
    worker = {
        "backend_kind": "codex_sdk",
        "state": worker_state,
        "worker_id": f"codex_sdk:{run.run_id}:codex_implementation:{spec_key}",
        "external_thread_id": thread_id,
        "external_turn_id": turn_id,
        "updated_at": "worker-receipt-time",
    }
    workspace = root / "partial-workspace"
    workspace.mkdir()
    partial_file = workspace / "partial.py"
    partial_file.write_text("partial = True\n", encoding="utf-8")
    resumed_calls = []
    monkeypatch.setattr(store, "workers_for_run", lambda run_id: [worker])
    monkeypatch.setattr(workflow, "_implementation_workspace_path", lambda **kwargs: workspace)

    class InterruptedThread:
        def read_thread(self, *, thread_id, repository_path):
            assert thread_id == "interrupted-implementation-thread"
            assert repository_path == workspace
            return {
                "thread_id": thread_id,
                "thread_status": "idle",
                "started_turn": False,
                "active_flags": [],
                "turn_count": 1,
                "turns": [{"turn_id": turn_id, "status": "interrupted"}],
            }

    monkeypatch.setattr(workflow, "CodexAdapter", InterruptedThread)

    def resume_stage(**kwargs):
        resumed_calls.append(kwargs)
        return {"state": "running", "thread_id": kwargs["thread_id"]}

    monkeypatch.setattr(workflow, "_resume_codex_stage", resume_stage)
    monkeypatch.setattr(
        workflow, "_reconcile_completed_implementation_turn",
        lambda **kwargs: pytest.fail("interrupted work must not enter completed-turn adoption"),
    )

    result = workflow._recover_after_process_exit(
        control_root=root, config=config, run=run, brief="brief", brief_digest="digest", store=store,
    )

    assert result == {"created": False, "state": "running", "thread_id": thread_id}
    assert len(resumed_calls) == 1
    assert resumed_calls[0]["thread_id"] == thread_id
    assert resumed_calls[0]["spec_key"] == spec_key
    assert partial_file.read_text(encoding="utf-8") == "partial = True\n"
    reconciliation = [
        event for event in store.events_for_run(run.run_id)
        if event["event_type"] == "interrupted_sdk_turn_reconciled"
    ]
    assert len(reconciliation) == 1
    assert reconciliation[0]["payload"] == {
        "step": "codex_implementation",
        "spec_key": spec_key,
        "thread_id": thread_id,
        "turn_id": turn_id,
        "thread_status": "idle",
        "turn_status": "interrupted",
    }


def test_planning_persists_real_callback_identity_and_publishes_local_parent(context, monkeypatch):
    root, config, store, run = context
    calls = adapter(monkeypatch, [spec_document(), {"outcome": "planned", "questions": [], "tickets": [
        {"key": "T1", "title": 'Title: "quoted"', "body": "Implement R1", "acceptance": ["R1"], "blocked_by": []}]}])
    planned = workflow._execute_codex_planning(control_root=root, config=config, brief="Requirement", brief_digest="brief", run=run, store=store)
    plan = json.loads((root / "artifacts" / run.run_id / "spec-plan.json").read_text())
    plan["specs"][0]["blocked_by"] = ["S0"]
    ticketed = workflow._execute_codex_tickets(control_root=root, config=config, brief_digest="brief", run=planned, store=store, spec_plan=plan)
    assert ticketed.state == "tickets_ready"
    assert [call["phase"] for call in calls] == ["to-spec", "to-tickets"]
    assert "specs" in calls[0]["schema"]["properties"]
    assert "tickets" in calls[1]["schema"]["properties"]
    assert "must not equal the parent spec_key" in calls[1]["trusted"]["legacy_prompt"]
    assert "metadata belongs to the production queue" in calls[1]["trusted"]["legacy_prompt"]
    assert '"blocked_by": []' in calls[1]["trusted"]["legacy_prompt"]
    records = {item.key: item for item in read_local(root / "tracker").records}
    assert records["T1"].parent == "S1"
    workers = store.workers_for_run(run.run_id)
    assert {item["external_thread_id"] for item in workers if item["external_thread_id"]} == {"thread-1", "thread-2"}


def test_github_tracker_is_published_after_local_plan_and_requests_native_relations(context, monkeypatch):
    root, config, store, run = context
    config = replace(config, github_repository="williamxhero/skills",
        github_receipt_root=root / "github-receipts")
    adapter(monkeypatch, [spec_document(), {"outcome": "planned", "questions": [], "tickets": [
        {"key": "T1", "title": "Ticket", "body": "Implement R1", "acceptance": ["R1"], "blocked_by": []}] }])
    published = []

    class FakeGitHubTracker:
        def publish_draft(self, **kwargs):
            published.append(kwargs)
            return {"created": True, "receipt": {"operation_id": kwargs["operation_id"], "complete": True,
                "draft_digest": "verified-draft", "repository": kwargs["repository"], "issues": [{"number": 7}]}}

    monkeypatch.setattr(workflow, "GitHubTracker", FakeGitHubTracker)
    planned = workflow._execute_codex_planning(control_root=root, config=config, brief="Requirement", brief_digest="brief", run=run, store=store)
    plan = json.loads((root / "artifacts" / run.run_id / "spec-plan.json").read_text())
    workflow._execute_codex_tickets(control_root=root, config=config, brief_digest="brief", run=planned, store=store, spec_plan=plan)
    assert len(published) == 1
    assert published[0]["repository"] == "williamxhero/skills"
    assert published[0]["relation_mode"] == "native"
    assert published[0]["draft"]["umbrella"]["key"] == "S1"
    assert published[0]["draft"]["specs"][0]["key"] == "T1"
    operation = store.external_operation("tickets:test-production:S1")
    assert operation["state"] == "completed"
    assert operation["repository"] == "williamxhero/skills"
    assert operation["receipt"]["issues"] == [{"number": 7}]


def test_production_queue_drives_dependency_ordered_specs_without_parent_dispatch(context, monkeypatch):
    root, config, store, run = context
    config = replace(config, workflow_mode="production")
    plan = {"digest": "plan-2", "specs": [
        {"key": "S1", "title": "First", "body": "one", "blocked_by": []},
        {"key": "S2", "title": "Second", "body": "two", "blocked_by": ["S1"]},
        {"key": "S3", "title": "Third", "body": "three", "blocked_by": ["S2"]},
    ]}
    artifact_root = root / "artifacts" / run.run_id
    artifact_root.mkdir(parents=True, exist_ok=True)
    (artifact_root / "spec-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    # A stale plan file alone must not bypass the durable ticket stage.
    (artifact_root / "ticket-plan-S1.json").write_text(json.dumps({"spec_key": "S1"}), encoding="utf-8")
    calls = []

    def fake_tickets(*, run, spec_plan, **kwargs):
        key = spec_plan["specs"][0]["key"]
        calls.append(("tickets", key))
        artifact = root / "artifacts" / run.run_id
        artifact.mkdir(parents=True, exist_ok=True)
        (artifact / f"ticket-plan-{key}.json").write_text(json.dumps({"spec_key": key, "digest": f"ticket-{key}"}), encoding="utf-8")
        store.set_run_state(run.run_id, "tickets_ready")
        current = store.find_by_run_id(run.run_id)
        assert current is not None
        return current

    def fake_implementation(*, run, ticket_plan, **kwargs):
        key = ticket_plan["spec_key"]
        calls.append(("implementation", key))
        (artifact_root / f"delivery-{key}.json").write_text(json.dumps({
            "run_id": run.run_id, "spec_key": key, "plan_digest": "plan-2",
            "ticket_plan_digest": f"ticket-{key}", "candidate": {"sha": f"candidate-{key}"},
            "review": {"approved": True}, "merge": {"merged": True}}), encoding="utf-8")
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
    assert store.production_completed_specs(run.run_id) == {"S1", "S2", "S3"}


def test_production_queue_failure_closes_run_before_returning_error(context, monkeypatch):
    root, config, store, run = context
    plan = {"digest": "plan-1", "specs": [{"key": "S1", "title": "First", "body": "one", "blocked_by": []}]}

    def fail_tickets(**kwargs):
        raise RunnerError("planning_not_ready", "ticket worker returned an incomplete plan")

    monkeypatch.setattr(workflow, "_execute_codex_tickets", fail_tickets)
    with pytest.raises(RunnerError, match="incomplete plan"):
        workflow._run_production_queue(
            control_root=root,
            config=config,
            brief_digest="brief",
            run=run,
            store=store,
            spec_plan=plan,
        )

    current = store.find_by_run_id(run.run_id)
    assert current is not None
    assert current.state == "failed"
    assert store.operations_for_run(run.run_id)[0]["state"] == "failed"
    assert all(item["state"] == "failed" for item in store.steps_for_run(run.run_id))
    assert all(item["state"] == "failed" for item in store.workers_for_run(run.run_id))
    assert any(item["event_type"] == "run_failed" for item in store.events_for_run(run.run_id))


def test_production_completion_receipt_conflict_is_rejected_atomically(context):
    _, _, store, run = context
    store.complete_production_spec(run_id=run.run_id, spec_key="S1", plan_digest="plan", delivery_digest="delivery")
    with pytest.raises(RunnerError, match="conflicting evidence"):
        store.complete_production_spec(run_id=run.run_id, spec_key="S1", plan_digest="changed", delivery_digest="delivery")
    assert store.production_completed_specs(run.run_id) == {"S1"}
    assert store.find_by_run_id(run.run_id).state == "spec_completed"


def test_github_publication_intent_and_receipt_are_durable_and_identity_bound(context):
    _, _, store, run = context
    intent = store.prepare_external_operation(operation_id="github:run:S1", run_id=run.run_id,
        operation_kind="github_issue_publication", repository="williamxhero/skills", input_digest="draft")
    assert intent["state"] == "intent"
    store.complete_external_operation(operation_id="github:run:S1", receipt={"complete": True, "issues": [7]})
    assert store.external_operation("github:run:S1")["receipt"] == {"complete": True, "issues": [7]}
    with pytest.raises(RunnerError, match="different identity"):
        store.prepare_external_operation(operation_id="github:run:S1", run_id=run.run_id,
            operation_kind="github_issue_publication", repository="someone/else", input_digest="draft")


def test_github_receipt_and_stage_transition_roll_back_together(context):
    _, _, store, run = context
    operation_id = "github:atomic:S1"
    store.begin_stage(run.run_id, step_name="atomic_test", operation_id="stage:atomic",
        backend_kind="codex_sdk", worker_id="worker:atomic")
    store.prepare_external_operation(operation_id=operation_id, run_id=run.run_id,
        operation_kind="github_issue_publication", repository="williamxhero/skills", input_digest="draft")
    with pytest.raises(RunnerError, match="worker"):
        store.complete_codex_stage(run.run_id, "stage:atomic", thread_id="thread", turn_id="turn",
            state="tickets_ready", step_name="atomic_test", worker_id="missing-worker",
            external_operation=(operation_id, {"complete": True}))
    assert store.external_operation(operation_id)["state"] == "intent"
    assert store.find_by_run_id(run.run_id).state == "starting"


def test_production_cleanup_pending_retries_cleanup_without_implementation(context, monkeypatch):
    root, config, store, run = context
    config = replace(config, github_repository="williamxhero/skills", github_receipt_root=root / "receipts")
    artifact = root / "artifacts" / run.run_id
    artifact.mkdir(parents=True)
    (artifact / "ticket-plan-S1.json").write_text(json.dumps({"spec_key": "S1"}), encoding="utf-8")
    (artifact / "spec-plan.json").write_text(json.dumps({"digest": "plan-1", "specs": [{"key": "S1"}]}), encoding="utf-8")
    (artifact / "ticket-plan-S1.json").write_text(json.dumps({"spec_key": "S1", "digest": "ticket-1"}), encoding="utf-8")
    (artifact / "delivery-S1.json").write_text(json.dumps({
        "run_id": run.run_id, "spec_key": "S1", "plan_digest": "plan-1", "ticket_plan_digest": "ticket-1",
        "candidate": {"candidate_sha": "abc"}, "review": {"approved": True}, "merge": {"merged": True}}), encoding="utf-8")
    workspaces = root / "delivery-workspaces"
    workspaces.mkdir()
    workspace = workspaces / "run-S1"
    workspace.mkdir()
    manifest = workspaces / "run-S1.manifest.json"
    manifest.write_text(json.dumps({"run_id": run.run_id, "spec_key": "S1", "workspace": str(workspace)}), encoding="utf-8")
    recovery_workspace = workspaces / "run-S1-recovery"
    recovery_workspace.mkdir()
    recovery_manifest = workspaces / "run-S1-recovery.manifest.json"
    recovery_manifest.write_text(json.dumps({
        "run_id": run.run_id, "spec_key": "S1", "workspace": str(recovery_workspace),
    }), encoding="utf-8")
    store.set_run_state(run.run_id, "cleanup_pending")
    calls = []
    monkeypatch.setattr(workflow, "cleanup_managed_workspace", lambda **kwargs: calls.append(kwargs) or {"outcome": "cleaned"})
    monkeypatch.setattr(workflow, "_close_published_ticket_plan",
                        lambda **kwargs: {"complete": True, "issues": [{"number": 101, "state": "closed"}]})
    current = store.find_by_run_id(run.run_id)
    assert current is not None
    result = workflow._retry_production_cleanup(control_root=root, config=config, run=current, store=store)
    assert result["state"] == "spec_completed"
    assert {call["workspace"] for call in calls} == {workspace, recovery_workspace}
    assert all(call["preserve_manifest"] is True for call in calls[:2])
    assert all(call.get("preserve_manifest", False) is False for call in calls[2:])
    persisted_delivery = json.loads((artifact / "delivery-S1.json").read_text(encoding="utf-8"))
    assert persisted_delivery["issue_closure"]["complete"] is True
    durable_digest = hashlib.sha256(json.dumps(persisted_delivery, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    stored_digest = store.connection.execute(
        "SELECT delivery_digest FROM production_spec_completions WHERE run_id = ? AND spec_key = ?",
        (run.run_id, "S1"),
    ).fetchone()[0]
    assert stored_digest == durable_digest
    assert json.loads((artifact / "completed-specs.json").read_text())["specs"] == ["S1"]


def test_production_cleanup_cannot_infer_completion_from_manifest_or_ticket_guess(context, monkeypatch):
    root, config, store, run = context
    artifact = root / "artifacts" / run.run_id
    artifact.mkdir(parents=True)
    (artifact / "ticket-plan-S1.json").write_text(json.dumps({"spec_key": "S1"}), encoding="utf-8")
    workspaces = root / "delivery-workspaces"
    workspaces.mkdir()
    workspace = workspaces / "run-S1"
    workspace.mkdir()
    manifest = workspaces / "run-S1.manifest.json"
    manifest.write_text(json.dumps({"run_id": run.run_id, "spec_key": "S1", "workspace": str(workspace)}), encoding="utf-8")
    store.set_run_state(run.run_id, "cleanup_pending")
    monkeypatch.setattr(workflow, "cleanup_managed_workspace", lambda **kwargs: {"outcome": "cleaned"})
    current = store.find_by_run_id(run.run_id)
    assert current is not None
    with pytest.raises(RunnerError, match="delivery, plan and ticket evidence"):
        workflow._retry_production_cleanup(control_root=root, config=config, run=current, store=store)
    assert not (artifact / "completed-specs.json").exists()


def test_production_cleanup_requires_a_confirmed_merge_receipt(context, monkeypatch):
    root, config, store, run = context
    artifact = root / "artifacts" / run.run_id
    artifact.mkdir(parents=True)
    (artifact / "ticket-plan-S1.json").write_text(json.dumps({"spec_key": "S1", "digest": "ticket-1"}), encoding="utf-8")
    (artifact / "spec-plan.json").write_text(json.dumps({"digest": "plan-1", "specs": [{"key": "S1"}]}), encoding="utf-8")
    (artifact / "delivery-S1.json").write_text(json.dumps({
        "run_id": run.run_id, "spec_key": "S1", "plan_digest": "plan-1", "ticket_plan_digest": "ticket-1",
        "candidate": {"candidate_sha": "abc"}, "review": {"approved": True}, "merge": {"merged": False},
    }), encoding="utf-8")
    workspaces = root / "delivery-workspaces"
    workspaces.mkdir()
    workspace = workspaces / "run-S1"
    workspace.mkdir()
    manifest = workspaces / "run-S1.manifest.json"
    manifest.write_text(json.dumps({"run_id": run.run_id, "spec_key": "S1", "workspace": str(workspace)}), encoding="utf-8")
    store.set_run_state(run.run_id, "cleanup_pending")
    monkeypatch.setattr(workflow, "cleanup_managed_workspace", lambda **kwargs: pytest.fail("unmerged workspace must not be cleaned"))
    current = store.find_by_run_id(run.run_id)
    assert current is not None
    with pytest.raises(RunnerError, match="does not prove this SPEC was reviewed and merged"):
        workflow._retry_production_cleanup(control_root=root, config=config, run=current, store=store)
    assert not (artifact / "completed-specs.json").exists()


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
    document["tickets"] = [{"key": "T1", "title": "Ticket", "body": "Implement R1",
                            "acceptance": ["R1"], "blocked_by": []}]
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
    answer = store.submit_answer_and_wake(run_id=run.run_id, question_id="Q1", value="json")
    assert answer["value_digest"]
    assert store.control_for_run(run.run_id)["requested_state"] == "resume_requested"
    assert len([event for event in store.events_for_run(run.run_id) if event["event_type"] == "answer_submitted"]) == 1
    assert len([event for event in store.events_for_run(run.run_id) if event["event_type"] == "control_requested"]) == 1
    assert store.submit_answer_and_wake(run_id=run.run_id, question_id="Q1", value="json")["value_digest"] == answer["value_digest"]
    assert len([event for event in store.events_for_run(run.run_id) if event["event_type"] == "control_requested"]) == 1
    resumed = workflow._resume_codex_stage(control_root=root, config=config, run=waiting,
        brief="Requirement", brief_digest="brief", store=store)
    assert resumed["run"]["current_step"] == "codex_ticket_planning"
    assert calls[1]["thread_id"] == "thread-1"
    assert calls[1]["trusted"]["answers"][0]["value"] == "json"
    assert [call["phase"] for call in calls] == ["grill", "grill", "to-spec", "to-tickets"]


def test_answer_and_wake_rejects_stale_or_terminal_run(context):
    _, _, store, run = context
    with pytest.raises(RunnerError, match="currently waiting"):
        store.submit_answer_and_wake(run_id=run.run_id, question_id="Q1", value="json")
    store.set_run_state(run.run_id, "cancelled")
    with pytest.raises(RunnerError, match="cancelled runs"):
        store.submit_answer_and_wake(run_id=run.run_id, question_id="Q1", value="json")


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
