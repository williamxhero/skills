from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .config import RunnerConfig, read_brief
from .codex_adapter import CodexAdapter, CodexWorkerResult
from .errors import RunnerError
from .store import RunRecord, Store, now
from .verification import verify_run
from .plans import load_json, validate_spec_plan, validate_ticket_plan
from .multi_spec import run_local_delivery
from .delivery import cleanup_managed_workspace, git_sha, merge_local, prepare_workspace, validate_review, verify_candidate
from .tracker import read_local, publish_local
from .scope_lock import ScopeLock
from .production_gates import implementation_artifacts, independent_review


def _run_worker(*, adapter: CodexAdapter, phase: str, config: RunnerConfig, prompt: str, model: str, effort: str, thread_id: str | None, repository_path: Path, trusted: dict[str, object], on_turn_started, control_state, on_control_applied=None, schema: dict[str, object] | None = None):
    """Use the live Skill boundary when available; retain test-double compatibility."""
    semantic = getattr(adapter, "run_semantic", None)
    if callable(semantic):
        return semantic(
            phase=phase,
            repository_path=repository_path,
            model=model,
            effort=effort,
            trusted={**trusted, "legacy_prompt": prompt},
            untrusted={},
            schema=schema or {"type": "object", "properties": {"outcome": {"type": "string"}, "artifacts": {"type": "array", "items": {"type": "string"}}, "blockers": {"type": "array", "items": {"type": "string"}}, "questions": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}}, "required": ["id", "question"], "additionalProperties": False}}}, "required": ["outcome", "artifacts", "blockers"], "additionalProperties": False},
            skill_roots=config.skill_roots,
            skill_config=(config.skill_config and (Path(config.skill_config))),
            thread_id=thread_id,
            on_turn_started=on_turn_started,
            control_state=control_state,
            on_control_applied=on_control_applied,
        )
    return adapter.run(prompt=prompt, repository_path=repository_path, model=model, effort=effort, thread_id=thread_id, on_turn_started=on_turn_started, control_state=control_state, on_control_applied=on_control_applied)


def _validate_launch_key(value: str) -> str:
    if not value or len(value) > 200 or any(character.isspace() for character in value):
        raise RunnerError("invalid_launch_key", "launch_key must be non-empty, at most 200 characters, and contain no whitespace")
    return value


def _start_lease_heartbeat(*, control_root: Path, scope: str, owner_token: str, global_path: ScopeLock | None = None) -> tuple[threading.Event, threading.Thread]:
    """Keep a long-running SDK call from looking stale to a recovery process."""
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(1.0):
            try:
                heartbeat_store = Store.open(control_root, create=False)
                try:
                    heartbeat_store.heartbeat_lease(scope=scope, owner_token=owner_token)
                finally:
                    heartbeat_store.close()
            except (OSError, RunnerError):
                return

    thread = threading.Thread(target=beat, name="spec-runner-lease-heartbeat", daemon=True)
    thread.start()
    return stop, thread


def _global_lease_path(scope: str) -> Path:
    key = hashlib.sha256(scope.encode("utf-8")).hexdigest()
    return Path(tempfile.gettempdir()) / "spec-runner-leases" / f"{key}.json"


def _acquire_global_lease(*, scope: str, owner_token: str, stale_after_seconds: float) -> ScopeLock:
    path = _global_lease_path(scope)
    # An older Runner may still hold the timestamp lease. Never steal it merely
    # because its heartbeat is old, or silently mix ownership protocols.
    if path.exists():
        raise RunnerError("legacy_scope_lease_present", "old repository lease requires owner reconciliation", details={"path": str(path)})
    return ScopeLock.acquire(path.with_suffix(".lock"), owner_token)


def _release_global_lease(lease: ScopeLock | None, owner_token: str) -> None:
    if lease is not None:
        lease.release(owner_token)


def _read_control_state(*, control_root: Path, run_id: str) -> str | None:
    """Read a control request from a short-lived connection.

    The SDK adapter's turn-control watcher runs in a background thread, so it
    must not share the Runner's SQLite connection. Each poll uses a read-only
    logical access path and leaves all state transitions to the owning Runner
    connection after the SDK result is returned.
    """
    control_store = Store.open(control_root, create=False)
    try:
        control = control_store.control_for_run(run_id)
        return str(control["requested_state"]) if control else None
    finally:
        control_store.close()


def _safe_artifact_directory(control_root: Path, config: RunnerConfig, run_id: str) -> Path:
    root = (control_root / config.artifact_root).resolve()
    directory = (root / run_id).resolve()
    if root not in (directory, *directory.parents):
        raise RunnerError("artifact_path_escape", "run artifact directory escaped artifact_root")
    return directory


def _test_fault_pause(*, control_root: Path, run_id: str, point: str) -> None:
    """Pause only when an explicit deterministic fault test asks for it."""
    if os.environ.get("SPEC_RUNNER_FAULT_POINT") != point:
        return
    fault_root = control_root / "faults"
    fault_root.mkdir(parents=True, exist_ok=True)
    ready = fault_root / f"{run_id}.{point}.ready"
    release = fault_root / f"{run_id}.{point}.continue"
    ready.write_text(json.dumps({"run_id": run_id, "point": point}) + "\n", encoding="utf-8", newline="\n")
    while not release.exists():
        time.sleep(0.05)


def _execute_deterministic_example(
    *, control_root: Path, config: RunnerConfig, brief: str, brief_digest: str, run: RunRecord, store: Store
) -> RunRecord:
    if "example" not in config.allowed_stages:
        raise RunnerError("stage_not_allowed", "deterministic_test requires the example stage to be allowed")
    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    # A process can exit after the directory is created but before the durable
    # handoff is written. Reusing that directory is safe because the handoff
    # digest and run identity are checked by verification.
    artifact_directory.mkdir(parents=True, exist_ok=True)
    (artifact_directory / "brief.md").write_text(brief, encoding="utf-8", newline="\n")
    handoff = {
        "schema_version": "spec-runner-deterministic-handoff/v1",
        "run_id": run.run_id,
        "stage": "example",
        "brief_digest": brief_digest,
        "backend_kind": "deterministic_test",
        "note": "This artifact is a deterministic SR-01.1 test result, not a Codex worker result.",
    }
    (artifact_directory / "handoff.json").write_text(
        json.dumps(handoff, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    _test_fault_pause(control_root=control_root, run_id=run.run_id, point="after_first_artifact")
    store.write_log(
        control_root,
        run.run_id,
        {"event": "deterministic_test_stage_completed", "artifact_directory": os.fspath(artifact_directory)},
    )
    return store.complete_deterministic_stage(run.run_id, f"start:{run.run_id}")


def _declared_model_result(result: CodexWorkerResult, *, brief_digest: str, stage: str) -> dict[str, object]:
    declared: dict[str, object] = {}
    if result.final_response:
        try:
            parsed = json.loads(result.final_response)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            declared = parsed
    questions = declared.get("questions", [])
    if not isinstance(questions, list) or any(
        not isinstance(question, dict)
        or not isinstance(question.get("id"), str)
        or not question["id"].strip()
        or not isinstance(question.get("question"), str)
        or not question["question"].strip()
        or ("options" in question and (not isinstance(question["options"], list) or any(not isinstance(option, str) or not option.strip() for option in question["options"])))
        for question in questions
    ) or len({question["id"] for question in questions if isinstance(question, dict) and isinstance(question.get("id"), str)}) != len(questions):
        raise RunnerError("invalid_worker_questions", "needs_input questions must have unique IDs and valid text/options")
    return {
        "schema_version": "spec-runner-worker-result/v1",
        "stage": stage,
        "input_digest": brief_digest,
        "outcome": declared.get("outcome", "completed" if result.status == "completed" else result.status),
        "artifacts": declared.get("artifacts", []),
        "blockers": declared.get("blockers", [result.error] if result.error else []),
        "questions": questions,
        **result.public(),
    }


def _record_codex_turn_started(
    store: Store,
    *,
    run_id: str,
    operation_id: str,
    step_name: str,
    worker_id: str,
    thread_id: str,
    turn_id: str,
) -> None:
    store.record_codex_turn_started(
        run_id,
        operation_id,
        thread_id=thread_id,
        turn_id=turn_id,
        step_name=step_name,
        worker_id=worker_id,
    )


def _planning_response(*, result: CodexWorkerResult, control_root: Path, config: RunnerConfig,
                       run: RunRecord, store: Store, operation_id: str, step_name: str,
                       worker_id: str, brief_digest: str) -> dict[str, object] | RunRecord:
    directory = _safe_artifact_directory(control_root, config, run.run_id)
    directory.mkdir(parents=True, exist_ok=True)
    # Retain the actual transport result even if semantic validation fails.
    turn_key = hashlib.sha256(result.turn_id.encode("utf-8")).hexdigest()
    (directory / f"{step_name}-{turn_key}.json").write_text(
        json.dumps(result.public(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if result.status != "completed" or result.error:
        raise RunnerError("planning_worker_failed", "planning requires a successful terminal SDK turn")
    try:
        document = json.loads(result.final_response or "null")
    except json.JSONDecodeError as exc:
        raise RunnerError("invalid_planning_output", "planning worker did not return JSON") from exc
    if not isinstance(document, dict):
        raise RunnerError("invalid_planning_output", "planning worker returned a non-object")
    declared = _declared_model_result(result, brief_digest=brief_digest, stage=step_name)
    if document.get("outcome") == "needs_input":
        if not declared["questions"]:
            raise RunnerError("invalid_worker_questions", "needs_input requires at least one question")
        (directory / "worker-result.json").write_text(json.dumps(declared, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return store.complete_codex_stage(run.run_id, operation_id, thread_id=result.thread_id,
            turn_id=result.turn_id, state="needs_input", step_name=step_name, worker_id=worker_id)
    if document.get("outcome") != "planned" or declared["questions"]:
        raise RunnerError("planning_not_ready", "only planned without unanswered questions may advance",
                          details={"outcome": document.get("outcome")})
    return document


def _execute_codex_planning(
    *, control_root: Path, config: RunnerConfig, brief: str, brief_digest: str, run: RunRecord, store: Store,
    thread_id: str | None = None,
) -> RunRecord:
    """Run the production planning boundary and persist a validated SpecPlan."""
    step_name = "codex_planning"
    operation_id = f"planning:{run.run_id}"
    worker_id = f"codex_sdk:{run.run_id}:{step_name}"
    store.begin_stage(run.run_id, step_name=step_name, operation_id=operation_id, backend_kind="codex_sdk", worker_id=worker_id)
    schema = {"type": "object", "properties": {
        "outcome": {"type": "string"}, "requirements": {"type": "array", "items": {"type": "string"}},
        "specs": {"type": "array", "items": {"type": "object", "properties": {
            "key": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"},
            "blocked_by": {"type": "array", "items": {"type": "string"}}, "covers": {"type": "array", "items": {"type": "string"}},
            "route": {"type": "object", "properties": {"model": {"type": "string"}, "effort": {"type": "string"}, "reason": {"type": "string"}}, "required": ["model", "effort", "reason"], "additionalProperties": False},
        }, "required": ["key", "title", "body", "blocked_by", "covers", "route"], "additionalProperties": False}},
        "questions": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}}, "required": ["id", "question"], "additionalProperties": False}},
    }, "required": ["outcome", "requirements", "specs", "questions"], "additionalProperties": False}
    answers = store.answers_for_run(run.run_id)
    prompt = "Produce a SpecPlan for this requirement. Do not publish issues, create branches, or modify files. If a user decision is required, return outcome needs_input and questions; otherwise return outcome planned with complete requirements and dependency-ordered specs.\n\n" + brief
    if answers:
        prompt += "\n\nRunner-recorded business answers (use as facts, do not ask again):\n" + json.dumps(answers, ensure_ascii=False, sort_keys=True)
    result = _run_worker(
        adapter=CodexAdapter(), phase="to-spec", config=config, prompt=prompt,
        trusted={"brief_digest": brief_digest, "stage": step_name, "repository_scope": os.fspath(config.repository_path)},
        repository_path=config.repository_path, model=config.model_name, effort=config.effort, thread_id=thread_id,
        control_state=lambda: _read_control_state(control_root=control_root, run_id=run.run_id),
        schema=schema,
        on_turn_started=lambda thread_id, turn_id: _record_codex_turn_started(store, run_id=run.run_id, operation_id=operation_id, step_name=step_name, worker_id=worker_id, thread_id=thread_id, turn_id=turn_id),
    )
    document = _planning_response(result=result, control_root=control_root, config=config, run=run,
        store=store, operation_id=operation_id, step_name=step_name, worker_id=worker_id, brief_digest=brief_digest)
    if isinstance(document, RunRecord):
        return document
    document["schema_version"] = "spec-runner-spec-plan/v1"
    document["requirement_digest"] = brief_digest
    validated = validate_spec_plan(document)
    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    artifact_directory.mkdir(parents=True, exist_ok=True)
    (artifact_directory / "spec-plan.json").write_text(json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    completed = store.complete_codex_stage(run.run_id, operation_id, thread_id=result.thread_id, turn_id=result.turn_id, state="planned", step_name=step_name, worker_id=worker_id)
    store.write_log(control_root, run.run_id, {"event": "spec_plan_validated", "thread_id": result.thread_id, "turn_id": result.turn_id, "spec_count": len(validated["specs"])})
    return completed


def _ticket_plan_source(*, artifact_directory: Path, plan: dict[str, object], run_id: str) -> Path:
    """Materialise a validated ticket plan as a local tracker source.

    The model only supplies issue data.  The Runner writes the tracker
    document with its own stable frontmatter and later publishes it through
    the idempotent local tracker adapter.
    """
    source = artifact_directory / "ticket-source"
    source.mkdir(parents=True, exist_ok=True)
    spec_key = str(plan["spec_key"])
    spec_metadata = {
        "key": spec_key,
        "kind": "spec",
        "title": str(plan.get("spec_title") or spec_key),
        "revision": str(plan["digest"]),
        "blocked_by": [],
        "comments": [f"spec-runner-run:{run_id}"],
    }
    spec_frontmatter = "---\n" + "\n".join(
        f"{field}: {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"
        for field, value in spec_metadata.items()
    ) + "\n---\n"
    (source / ("".join(character if character.isalnum() or character in "._-" else "-" for character in spec_key) + ".md")).write_text(
        spec_frontmatter + str(plan.get("spec_body") or "") + "\n", encoding="utf-8", newline="\n"
    )
    for ticket in plan["tickets"]:  # type: ignore[index]
        key = str(ticket["key"])
        filename = "".join(character if character.isalnum() or character in "._-" else "-" for character in key) + ".md"
        body = str(ticket["body"])
        metadata = {
            "key": key,
            "kind": "ticket",
            "title": str(ticket.get("title") or key),
            "revision": str(plan["digest"]),
            "parent": str(plan["spec_key"]),
            "blocked_by": list(ticket.get("blocked_by", [])),
            "comments": [f"spec-runner-run:{run_id}"],
        }
        frontmatter = "---\n" + "\n".join(
            f"{field}: {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"
            for field, value in metadata.items()
        ) + "\n---\n"
        (source / filename).write_text(frontmatter + body.rstrip() + "\n", encoding="utf-8", newline="\n")
    return source


def _execute_codex_tickets(
    *, control_root: Path, config: RunnerConfig, brief_digest: str, run: RunRecord, store: Store,
    spec_plan: dict[str, object], thread_id: str | None = None,
) -> RunRecord:
    """Turn one dependency-ready SPEC into a validated local TicketPlan."""
    specs = spec_plan.get("specs")
    if not isinstance(specs, list) or not specs or not isinstance(specs[0], dict):
        raise RunnerError("invalid_spec_plan", "cannot create tickets without a SPEC")
    spec = specs[0]
    spec_key = str(spec.get("key", ""))
    base_sha = git_sha(config.repository_path, config.target_ref)
    step_name = "codex_ticket_planning"
    operation_id = f"tickets:{run.run_id}:{spec_key}"
    worker_id = f"codex_sdk:{run.run_id}:{step_name}:{spec_key}"
    store.begin_stage(run.run_id, step_name=step_name, operation_id=operation_id, backend_kind="codex_sdk", worker_id=worker_id)
    schema = {
        "type": "object",
        "properties": {
            "outcome": {"type": "string"},
            "tickets": {"type": "array", "items": {"type": "object", "properties": {
                "key": {"type": "string"}, "title": {"type": "string"}, "body": {"type": "string"},
                "blocked_by": {"type": "array", "items": {"type": "string"}},
                "acceptance": {"type": "array", "items": {"type": "string"}},
            }, "required": ["key", "title", "body", "blocked_by", "acceptance"], "additionalProperties": False}},
            "questions": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}}, "required": ["id", "question"], "additionalProperties": False}},
        },
        "required": ["outcome", "tickets", "questions"], "additionalProperties": False,
    }
    prompt = (
        "Produce a TicketPlan for exactly this SPEC. Do not publish issues, create branches, or modify files. "
        "Return needs_input/questions when a real requirement fact is missing; otherwise return planned with concrete "
        "tickets, dependency keys, and acceptance IDs.\n\n" + json.dumps(spec, ensure_ascii=False, sort_keys=True)
    )
    answers = store.answers_for_run(run.run_id)
    if answers:
        prompt += "\n\nRunner-recorded business answers:\n" + json.dumps(answers, ensure_ascii=False, sort_keys=True)
    result = _run_worker(
        adapter=CodexAdapter(), phase="to-tickets", config=config, prompt=prompt,
        trusted={"brief_digest": brief_digest, "stage": step_name, "spec_key": spec_key, "base_sha": base_sha},
        repository_path=config.repository_path, model=config.model_name, effort=config.effort, thread_id=thread_id,
        control_state=lambda: _read_control_state(control_root=control_root, run_id=run.run_id),
        schema=schema,
        on_turn_started=lambda thread_id, turn_id: _record_codex_turn_started(store, run_id=run.run_id, operation_id=operation_id, step_name=step_name, worker_id=worker_id, thread_id=thread_id, turn_id=turn_id),
    )
    document = _planning_response(result=result, control_root=control_root, config=config, run=run,
        store=store, operation_id=operation_id, step_name=step_name, worker_id=worker_id, brief_digest=brief_digest)
    if isinstance(document, RunRecord):
        return document
    document.update({
        "schema_version": "spec-runner-ticket-plan/v1", "spec_key": spec_key, "base_sha": base_sha,
        "spec_title": str(spec.get("title") or spec_key), "spec_body": str(spec.get("body") or ""),
    })
    validated = validate_ticket_plan(document, expected_spec_key=spec_key, expected_base_sha=base_sha)
    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    artifact_directory.mkdir(parents=True, exist_ok=True)
    (artifact_directory / f"ticket-plan-{spec_key}.json").write_text(json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    source = _ticket_plan_source(artifact_directory=artifact_directory, plan=validated, run_id=run.run_id)
    published = publish_local(read_local(source), control_root / "tracker", operation_id=operation_id)
    store.write_log(control_root, run.run_id, {"event": "ticket_plan_published", "spec_key": spec_key, "ticket_count": len(validated["tickets"]), "tracker": published})
    return store.complete_codex_stage(run.run_id, operation_id, thread_id=result.thread_id, turn_id=result.turn_id, state="tickets_ready", step_name=step_name, worker_id=worker_id)


def _git_checked(repository: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", os.fspath(repository), *args], check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RunnerError("implementation_git_failed", "Runner could not reconcile the implementation workspace", details={"args": list(args)}) from exc
    return result.stdout.strip()


def _execute_independent_review(*, control_root: Path, config: RunnerConfig, brief_digest: str,
                                 run: RunRecord, store: Store, ticket_plan: dict[str, object],
                                 workspace: Path, candidate_sha: str, candidate_receipt: dict[str, object],
                                 implementation_thread: str, artifact_directory: Path) -> tuple[dict[str, object], CodexWorkerResult]:
    spec_key = str(ticket_plan["spec_key"])
    review_operation = f"review:{run.run_id}:{spec_key}:{candidate_sha}"
    review_step = "codex_review"
    review_worker = f"codex_sdk:{run.run_id}:{review_step}:{spec_key}:{candidate_sha[:12]}"
    store.begin_stage(run.run_id, step_name=review_step, operation_id=review_operation, backend_kind="codex_sdk", worker_id=review_worker)
    review_schema = {"type": "object", "properties": {"schema_version": {"type": "string"}, "candidate_sha": {"type": "string"}, "acceptance_version": {"type": "string"}, "findings": {"type": "array", "items": {"type": "object", "properties": {"severity": {"type": "string", "enum": ["critical", "high", "medium", "low", "info"]}, "status": {"type": "string", "enum": ["open", "resolved"]}, "description": {"type": "string"}}, "required": ["severity", "status", "description"], "additionalProperties": False}}}, "required": ["schema_version", "candidate_sha", "acceptance_version", "findings"], "additionalProperties": False}
    review_result = _run_worker(
        adapter=CodexAdapter(), phase="review", config=config,
        prompt=("Review the candidate in this read-only workspace against the SPEC and the attached real check receipt. "
                "Return schema_version spec-runner-review-result/v1, candidate_sha, acceptance_version and findings. "
                "Do not edit files, publish, or merge.\n\n" + json.dumps({"spec": ticket_plan, "candidate": candidate_receipt}, ensure_ascii=False, sort_keys=True)),
        model=config.model_name, effort=config.effort, thread_id=None, repository_path=workspace,
        trusted={"brief_digest": brief_digest, "stage": review_step, "spec_key": spec_key, "candidate_sha": candidate_sha, "acceptance_version": ticket_plan["digest"]},
        on_turn_started=lambda thread_id, turn_id: _record_codex_turn_started(store, run_id=run.run_id, operation_id=review_operation, step_name=review_step, worker_id=review_worker, thread_id=thread_id, turn_id=turn_id),
        control_state=lambda: _read_control_state(control_root=control_root, run_id=run.run_id), schema=review_schema,
    )
    (artifact_directory / f"review-worker-{spec_key}-{candidate_sha[:12]}.json").write_text(json.dumps(review_result.public(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    validated = independent_review(review_result, implementation_thread=implementation_thread, candidate_sha=candidate_sha, acceptance_version=str(ticket_plan["digest"]))
    store.complete_codex_stage(run.run_id, review_operation, thread_id=review_result.thread_id, turn_id=review_result.turn_id, state="reviewed", step_name=review_step, worker_id=review_worker)
    (artifact_directory / f"review-{spec_key}-{candidate_sha[:12]}.json").write_text(json.dumps({**validated, "worker": review_result.public()}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    CodexAdapter().archive_and_readback(thread_id=review_result.thread_id, repository_path=workspace)
    return validated, review_result


def _repair_candidate(*, control_root: Path, config: RunnerConfig, brief_digest: str, run: RunRecord,
                      store: Store, ticket_plan: dict[str, object], workspace: Path,
                      findings: list[object], implementation_thread: str, artifact_directory: Path) -> tuple[str, dict[str, object]]:
    spec_key = str(ticket_plan["spec_key"])
    operation = f"repair:{run.run_id}:{spec_key}:{len(store.workers_for_run(run.run_id))}"
    step = "codex_repair"
    worker = f"codex_sdk:{run.run_id}:{step}:{spec_key}"
    store.begin_stage(run.run_id, step_name=step, operation_id=operation, backend_kind="codex_sdk", worker_id=worker)
    result = _run_worker(
        adapter=CodexAdapter(), phase="implement", config=config,
        prompt=("Fix only these independent review findings in the existing assigned workspace. Preserve all acceptance "
                "requirements and do not publish, merge, or edit outside the workspace.\n\n" + json.dumps(findings, ensure_ascii=False, sort_keys=True)),
        model=config.model_name, effort=config.effort, thread_id=implementation_thread, repository_path=workspace,
        trusted={"brief_digest": brief_digest, "stage": step, "spec_key": spec_key, "review_findings": findings},
        on_turn_started=lambda thread_id, turn_id: _record_codex_turn_started(store, run_id=run.run_id, operation_id=operation, step_name=step, worker_id=worker, thread_id=thread_id, turn_id=turn_id),
        control_state=lambda: _read_control_state(control_root=control_root, run_id=run.run_id),
    )
    if result.status != "completed":
        raise RunnerError("repair_worker_failed", "bounded repair worker did not complete", details=result.public())
    if not _git_checked(workspace, "status", "--porcelain"):
        raise RunnerError("repair_no_progress", "repair worker produced no candidate changes")
    _git_checked(workspace, "add", "--all")
    _git_checked(workspace, "-c", "user.name=Spec Runner", "-c", "user.email=spec-runner@localhost", "commit", "-m", f"spec-runner: repair {spec_key}")
    candidate_sha = git_sha(workspace)
    candidate_receipt = verify_candidate(workspace=workspace, candidate_sha=candidate_sha, acceptance_version=str(ticket_plan["digest"]), checks=list(config.acceptance_checks), acceptance=list(config.acceptance_ids))
    store.complete_codex_stage(run.run_id, operation, thread_id=result.thread_id, turn_id=result.turn_id, state="verified_candidate", step_name=step, worker_id=worker)
    (artifact_directory / f"repair-{spec_key}-{candidate_sha[:12]}.json").write_text(json.dumps({"candidate": candidate_receipt, "worker": result.public()}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return candidate_sha, candidate_receipt


def _execute_codex_implementation(
    *, control_root: Path, config: RunnerConfig, brief_digest: str, run: RunRecord, store: Store,
    ticket_plan: dict[str, object],
) -> dict[str, object]:
    """Run one real implementation and independent review for the active SPEC.

    The worker may write only its managed worktree.  Commit, candidate
    verification, review binding, merge and cleanup remain Runner operations.
    """
    if not config.acceptance_ids or not config.acceptance_checks:
        raise RunnerError("acceptance_config_missing", "production implementation requires workflow.acceptance ids and checks")
    spec_key = str(ticket_plan["spec_key"])
    workspace_info = prepare_workspace(
        repository=config.repository_path, workspace_root=control_root / "delivery-workspaces",
        run_id=run.run_id, spec_key=spec_key, base_ref=config.target_ref,
    )
    workspace = Path(str(workspace_info["workspace"]))
    implementation_operation = f"implementation:{run.run_id}:{spec_key}"
    implementation_step = "codex_implementation"
    implementation_worker = f"codex_sdk:{run.run_id}:{implementation_step}:{spec_key}"
    store.begin_stage(run.run_id, step_name=implementation_step, operation_id=implementation_operation, backend_kind="codex_sdk", worker_id=implementation_worker)
    schema = {"type": "object", "properties": {"outcome": {"type": "string"}, "artifacts": {"type": "array", "items": {"type": "string"}}, "blockers": {"type": "array", "items": {"type": "string"}}, "questions": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "question": {"type": "string"}}, "required": ["id", "question"], "additionalProperties": False}}}, "required": ["outcome", "artifacts", "blockers", "questions"], "additionalProperties": False}
    result = _run_worker(
        adapter=CodexAdapter(), phase="implement", config=config,
        prompt=("Implement this SPEC in the assigned workspace. Work on the real code and tests; do not publish, merge, "
                "or modify files outside this workspace. Return JSON only after the implementation is complete.\n\n" + json.dumps(ticket_plan, ensure_ascii=False, sort_keys=True)),
        model=config.model_name, effort=config.effort, thread_id=None, repository_path=workspace,
        trusted={"brief_digest": brief_digest, "stage": implementation_step, "spec_key": spec_key, "workspace": os.fspath(workspace), "ticket_plan_digest": ticket_plan["digest"]},
        on_turn_started=lambda thread_id, turn_id: _record_codex_turn_started(store, run_id=run.run_id, operation_id=implementation_operation, step_name=implementation_step, worker_id=implementation_worker, thread_id=thread_id, turn_id=turn_id),
        control_state=lambda: _read_control_state(control_root=control_root, run_id=run.run_id), schema=schema,
    )
    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    artifact_directory.mkdir(parents=True, exist_ok=True)
    (artifact_directory / f"implementation-{spec_key}.json").write_text(json.dumps(result.public(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    implementation_artifacts(result, workspace)
    if _git_checked(workspace, "status", "--porcelain") == "":
        raise RunnerError("implementation_no_changes", "implementation worker produced no workspace changes")
    _git_checked(workspace, "add", "--all")
    _git_checked(workspace, "-c", "user.name=Spec Runner", "-c", "user.email=spec-runner@localhost", "commit", "-m", f"spec-runner: implement {spec_key}")
    candidate_sha = git_sha(workspace)
    candidate_receipt = verify_candidate(workspace=workspace, candidate_sha=candidate_sha, acceptance_version=str(ticket_plan["digest"]), checks=list(config.acceptance_checks), acceptance=list(config.acceptance_ids))
    store.complete_codex_stage(run.run_id, implementation_operation, thread_id=result.thread_id, turn_id=result.turn_id, state="verified_candidate", step_name=implementation_step, worker_id=implementation_worker)
    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    artifact_directory.mkdir(parents=True, exist_ok=True)
    (artifact_directory / f"candidate-{spec_key}.json").write_text(json.dumps(candidate_receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    CodexAdapter().archive_and_readback(thread_id=result.thread_id, repository_path=workspace)

    validated_review, _ = _execute_independent_review(control_root=control_root, config=config, brief_digest=brief_digest, run=run, store=store, ticket_plan=ticket_plan, workspace=workspace, candidate_sha=candidate_sha, candidate_receipt=candidate_receipt, implementation_thread=result.thread_id, artifact_directory=artifact_directory)
    repair_round = 0
    while not validated_review["approved"] and repair_round < 2:
        repair_round += 1
        candidate_sha, candidate_receipt = _repair_candidate(control_root=control_root, config=config, brief_digest=brief_digest, run=run, store=store, ticket_plan=ticket_plan, workspace=workspace, findings=list(validated_review["blocking"]), implementation_thread=result.thread_id, artifact_directory=artifact_directory)
        validated_review, _ = _execute_independent_review(control_root=control_root, config=config, brief_digest=brief_digest, run=run, store=store, ticket_plan=ticket_plan, workspace=workspace, candidate_sha=candidate_sha, candidate_receipt=candidate_receipt, implementation_thread=result.thread_id, artifact_directory=artifact_directory)
    if not validated_review["approved"]:
        raise RunnerError("review_blocked", "independent review remained blocked after bounded repair rounds", details={"findings": validated_review["blocking"], "repair_rounds": repair_round})
    if git_sha(workspace) != candidate_sha or _git_checked(workspace, "status", "--porcelain"):
        raise RunnerError("candidate_changed_after_review", "candidate changed after the verified check/review pair")
    if git_sha(config.repository_path, str(workspace_info["branch"])) != candidate_sha:
        raise RunnerError("candidate_branch_changed", "candidate branch moved after verification")
    expected_target_sha = str(workspace_info["base_sha"])
    merged = merge_local(repository=config.repository_path, candidate_branch=str(workspace_info["branch"]), target_ref=config.target_ref, expected_target_sha=expected_target_sha, workspace_root=control_root / "delivery-workspaces", run_id=run.run_id)
    cleanup = cleanup_managed_workspace(repository=config.repository_path, workspace_root=control_root / "delivery-workspaces", workspace=workspace, manifest=Path(str(workspace_info["manifest"])))
    if cleanup.get("outcome") != "cleaned":
        store.mark_cleanup_pending(run.run_id)
        return {"state": "cleanup_pending", "candidate": candidate_receipt, "review": validated_review, "merge": merged, "cleanup": cleanup}
    store.mark_archived(run.run_id, state="completed")
    return {"state": "completed", "candidate": candidate_receipt, "review": validated_review, "merge": merged, "cleanup": cleanup}


def _execute_codex_example(
    *, control_root: Path, config: RunnerConfig, brief: str, brief_digest: str, run: RunRecord, store: Store,
    thread_id: str | None = None,
) -> RunRecord:
    operation_id = f"start:{run.run_id}"
    worker_id = f"codex_sdk:{run.run_id}"
    prompt = (
        "You are a bounded Spec Runner worker. Read the supplied brief, create exactly one handoff file at "
        f"spec-runner-output/{run.run_id}/handoff.md inside the repository, and return only a JSON object with keys "
        "outcome, artifacts, and blockers. The handoff must contain the brief digest and stage name. "
        "Do not claim an artifact unless it is real. "
        "Do not publish issues, create a PR, or modify files outside the configured repository.\n\n"
        f"Brief digest: {brief_digest}\n\n{brief}"
    )
    adapter = CodexAdapter()
    result: CodexWorkerResult = _run_worker(
        adapter=adapter, phase="implement", config=config, prompt=prompt,
        trusted={"brief_digest": brief_digest, "stage": "example", "repository_scope": os.fspath(config.repository_path)},
        repository_path=config.repository_path,
        model=config.model_name,
        effort=config.effort,
        thread_id=thread_id,
        control_state=lambda: _read_control_state(control_root=control_root, run_id=run.run_id),
        on_turn_started=lambda thread_id, turn_id: _record_codex_turn_started(
            store,
            run_id=run.run_id,
            operation_id=operation_id,
            step_name="codex_example",
            worker_id=worker_id,
            thread_id=thread_id,
            turn_id=turn_id,
        ),
    )
    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    artifact_directory.mkdir(parents=True, exist_ok=True)
    (artifact_directory / "worker-result.json").write_text(
        json.dumps(_declared_model_result(result, brief_digest=brief_digest, stage="example"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    store.write_log(
        control_root,
        run.run_id,
        {"event": "codex_turn_completed", "thread_id": result.thread_id, "turn_id": result.turn_id, "status": result.status},
    )
    interrupted = result.status == "interrupted"
    control = store.control_for_run(run.run_id) if interrupted else None
    if interrupted and (
        not control or control["requested_state"] not in {"pause_requested", "cancel_requested"}
    ):
        raise RunnerError("unexpected_sdk_interrupt", "Codex interrupted without a durable pause or cancel request")
    state = "paused" if control and control["requested_state"] == "pause_requested" else (
        "cancelled" if interrupted else "turn_completed"
    )
    declared = _declared_model_result(result, brief_digest=brief_digest, stage="example")
    stage_state = "needs_input" if result.status == "completed" and declared.get("outcome") == "needs_input" and isinstance(declared.get("questions"), list) and declared["questions"] else state
    completed = store.complete_codex_stage(
        run.run_id,
        f"start:{run.run_id}",
        thread_id=result.thread_id,
        turn_id=result.turn_id,
        state=stage_state,
    )
    if interrupted and control:
        store.append_event(
            run_id=run.run_id,
            event_key=f"control:{run.run_id}:{control['generation']}:applied",
            event_type="control_applied",
            payload={"requested_state": control["requested_state"], "generation": control["generation"], "turn_id": result.turn_id},
        )
    return completed


def _execute_second_codex(
    *, control_root: Path, config: RunnerConfig, run: RunRecord, brief_digest: str, store: Store,
    thread_id: str | None = None,
) -> RunRecord:
    step_name = "codex_second"
    operation_id = f"second:{run.run_id}"
    store.begin_stage(run.run_id, step_name=step_name, operation_id=operation_id, backend_kind="codex_sdk")
    prompt = (
        "You are the second bounded Spec Runner worker. Consume the first-stage handoff directly. "
        f"Create exactly one final file at spec-runner-output/{run.run_id}/final.md inside the repository, "
        "then return only JSON with outcome, artifacts, and blockers. Do not publish issues or create a PR.\n\n"
        f"First-stage handoff: spec-runner-output/{run.run_id}/handoff.md\nBrief digest: {brief_digest}"
    )
    adapter = CodexAdapter()
    result = _run_worker(
        adapter=adapter, phase="implement", config=config, prompt=prompt,
        trusted={"brief_digest": brief_digest, "stage": step_name, "repository_scope": os.fspath(config.repository_path)},
        repository_path=config.repository_path,
        model=config.model_name,
        effort=config.effort,
        thread_id=thread_id,
        control_state=lambda: _read_control_state(control_root=control_root, run_id=run.run_id),
        on_turn_started=lambda thread_id, turn_id: _record_codex_turn_started(
            store,
            run_id=run.run_id,
            operation_id=operation_id,
            step_name=step_name,
            worker_id=f"codex_sdk:{run.run_id}:{step_name}",
            thread_id=thread_id,
            turn_id=turn_id,
        ),
    )
    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    (artifact_directory / "worker-result-second.json").write_text(
        json.dumps(_declared_model_result(result, brief_digest=brief_digest, stage=step_name), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    store.write_log(
        control_root,
        run.run_id,
        {"event": "codex_second_turn_completed", "thread_id": result.thread_id, "turn_id": result.turn_id, "status": result.status},
    )
    interrupted = result.status == "interrupted"
    control = store.control_for_run(run.run_id) if interrupted else None
    if interrupted and (
        not control or control["requested_state"] not in {"pause_requested", "cancel_requested"}
    ):
        raise RunnerError("unexpected_sdk_interrupt", "Codex interrupted without a durable pause or cancel request")
    state = "paused" if control and control["requested_state"] == "pause_requested" else (
        "cancelled" if interrupted else "turn_completed"
    )
    completed = store.complete_codex_stage(
        run.run_id,
        operation_id,
        thread_id=result.thread_id,
        turn_id=result.turn_id,
        state=state,
        step_name=step_name,
        worker_id=f"codex_sdk:{run.run_id}:{step_name}",
    )
    if interrupted and control:
        store.append_event(
            run_id=run.run_id,
            event_key=f"control:{run.run_id}:{control['generation']}:applied",
            event_type="control_applied",
            payload={"requested_state": control["requested_state"], "generation": control["generation"], "turn_id": result.turn_id},
        )
    return completed


def _verify_and_archive(
    *, control_root: Path, config: RunnerConfig, run: RunRecord, store: Store, final_state: str = "completed"
) -> dict[str, object]:
    worker = store.workers_for_run(run.run_id)[-1]
    try:
        receipt = verify_run(control_root=control_root, run=run, worker=worker)
        store.record_verification(run.run_id, receipt)
        store.append_event(
            run_id=run.run_id,
            event_key=f"verification:{run.run_id}:{receipt.get('stage')}:observed",
            event_type="step_verified",
            payload={"stage": receipt.get("stage"), "evidence_kind": receipt.get("evidence_kind", "local")},
        )
    except RunnerError:
        if run.backend_kind == "codex_sdk" and worker.get("external_thread_id"):
            try:
                CodexAdapter().archive_and_readback(
                    thread_id=str(worker["external_thread_id"]), repository_path=config.repository_path
                )
                store.mark_archived(run.run_id, state="verification_failed")
            except RunnerError:
                store.mark_cleanup_pending(run.run_id)
        raise

    if run.backend_kind == "codex_sdk":
        try:
            CodexAdapter().archive_and_readback(
                thread_id=str(worker["external_thread_id"]), repository_path=config.repository_path
            )
        except RunnerError:
            store.mark_cleanup_pending(run.run_id)
            raise
    store.append_event(
        run_id=run.run_id,
        event_key=f"cleanup:{run.run_id}:{run.current_step}:readback",
        event_type="cleanup_readback",
        payload={"step": run.current_step, "backend_kind": run.backend_kind},
    )
    store.mark_archived(run.run_id, state=final_state)
    return store.public_status(run.run_id)


def _finalize_cancelled_codex(*, config: RunnerConfig, run: RunRecord, store: Store) -> dict[str, object]:
    """Archive a cancelled SDK thread without treating cancellation as success."""
    if run.state != "cancelled" or run.backend_kind != "codex_sdk":
        return store.public_status(run.run_id)
    worker = store.workers_for_run(run.run_id)[-1]
    thread_id = str(worker.get("external_thread_id") or "")
    if not thread_id:
        store.mark_cleanup_pending(run.run_id)
        return store.public_status(run.run_id)
    try:
        CodexAdapter().archive_and_readback(thread_id=thread_id, repository_path=config.repository_path)
    except RunnerError:
        store.mark_cleanup_pending(run.run_id)
        return store.public_status(run.run_id)
    store.append_event(
        run_id=run.run_id,
        event_key=f"cleanup:{run.run_id}:cancelled-thread-readback",
        event_type="cleanup_readback",
        payload={"thread_id": thread_id, "cancelled": True},
    )
    store.mark_archived(run.run_id, state="cancelled")
    return store.public_status(run.run_id)


def _run_delivery_plan(*, control_root: Path, config: RunnerConfig, run: RunRecord, store: Store) -> dict[str, object]:
    if config.delivery_plan is None:
        raise RunnerError("delivery_plan_missing", "delivery workflow was requested without a plan")
    plan = load_json(control_root / config.delivery_plan)

    def on_event(event_type: str, payload: dict[str, object]) -> None:
        spec_key = str(payload.get("spec_key", "unknown"))
        store.append_event(run_id=run.run_id, event_key=f"delivery:{run.run_id}:{event_type}:{spec_key}", event_type=event_type, payload=payload)

    def on_verified(receipt: dict[str, object]) -> None:
        store.record_verification(run.run_id, receipt)

    receipt = run_local_delivery(
        plan=plan,
        repository=config.repository_path,
        workspace_root=control_root / "delivery-workspaces",
        control_root=control_root,
        run_id=run.run_id,
        target_ref=config.target_ref,
        on_verified=on_verified,
        on_event=on_event,
    )
    if receipt.get("state") == "cleanup_pending":
        store.mark_cleanup_pending(run.run_id)
        store.append_event(
            run_id=run.run_id,
            event_key=f"delivery:{run.run_id}:cleanup_pending",
            event_type="cleanup_pending",
            payload={"completed_specs": receipt.get("completed_specs", [])},
        )
        return store.public_status(run.run_id)
    store.append_event(
        run_id=run.run_id,
        event_key=f"delivery:{run.run_id}:completed",
        event_type="delivery_completed",
        payload={"completed_specs": receipt.get("completed_specs", []), "plan_digest": receipt.get("plan_digest")},
    )
    store.mark_archived(run.run_id, state="completed")
    return store.public_status(run.run_id)


def _execute_second_deterministic(*, control_root: Path, config: RunnerConfig, run: RunRecord, store: Store) -> RunRecord:
    step_name = "deterministic_second"
    operation_id = f"second:{run.run_id}"
    store.begin_stage(run.run_id, step_name=step_name, operation_id=operation_id, backend_kind="deterministic_test")
    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    handoff_path = artifact_directory / "handoff.json"
    if not handoff_path.is_file() or handoff_path.is_symlink():
        raise RunnerError("missing_handoff", "second stage cannot start without the verified first-stage handoff")
    handoff_digest = hashlib.sha256(handoff_path.read_bytes()).hexdigest()
    (artifact_directory / "final.json").write_text(
        json.dumps(
            {
                "schema_version": "spec-runner-deterministic-final/v1",
                "run_id": run.run_id,
                "consumed_stage": "deterministic_example",
                "handoff_digest": handoff_digest,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _test_fault_pause(control_root=control_root, run_id=run.run_id, point="after_second_artifact")
    return store.complete_mechanical_stage(run.run_id, operation_id, step_name=step_name)


def _advance_second_stage(*, control_root: Path, config: RunnerConfig, run: RunRecord, brief_digest: str, store: Store) -> dict[str, object]:
    control = store.control_for_run(run.run_id)
    if control and control["requested_state"] in {"pause_requested", "cancel_requested"}:
        stop_state = "paused" if control["requested_state"] == "pause_requested" else "cancelled"
        store.set_run_state(run.run_id, stop_state)
        store.append_event(
            run_id=run.run_id,
            event_key=f"control:{run.run_id}:{control['generation']}:applied",
            event_type="control_applied",
            payload={"requested_state": control["requested_state"], "generation": control["generation"]},
        )
        return store.public_status(run.run_id)
    if config.execution_backend == "deterministic_test":
        second = _execute_second_deterministic(control_root=control_root, config=config, run=run, store=store)
    else:
        second = _execute_second_codex(
            control_root=control_root, config=config, run=run, brief_digest=brief_digest, store=store
        )
    if second.state in {"paused", "cancelled"}:
        if second.state == "cancelled":
            return _finalize_cancelled_codex(config=config, run=second, store=store)
        return store.public_status(second.run_id)
    return _verify_and_archive(control_root=control_root, config=config, run=second, store=store)


def _resume_codex_stage(
    *, control_root: Path, config: RunnerConfig, run: RunRecord, brief: str, brief_digest: str, store: Store
) -> dict[str, object]:
    """Resume the interrupted SDK turn on its persisted thread.

    A paused run is a stage boundary, not permission to start a competing
    thread. The formal thread identity persisted by ``Thread.turn()`` is the
    only identity accepted for this continuation.
    """
    worker = store.workers_for_run(run.run_id)[-1]
    thread_id = str(worker.get("external_thread_id") or "")
    if not thread_id:
        raise RunnerError("resume_thread_missing", "paused Codex run has no persisted thread identity")
    if run.current_step == "codex_example":
        resumed = _execute_codex_example(
            control_root=control_root,
            config=config,
            brief=brief,
            brief_digest=brief_digest,
            run=run,
            store=store,
            thread_id=thread_id,
        )
        if resumed.state in {"paused", "cancelled"}:
            if resumed.state == "cancelled":
                return _finalize_cancelled_codex(config=config, run=resumed, store=store)
            return store.public_status(resumed.run_id)
        _verify_and_archive(
            control_root=control_root,
            config=config,
            run=resumed,
            store=store,
            final_state="ready_for_next",
        )
        current = store.find_by_run_id(run.run_id)
        assert current is not None
        store.append_event(
            run_id=run.run_id,
            event_key=f"resume:{run.run_id}:next-stage",
            event_type="next_stage_started",
            payload={"from_step": "codex_example", "to_step": "codex_second"},
        )
        return _advance_second_stage(
            control_root=control_root,
            config=config,
            run=current,
            brief_digest=brief_digest,
            store=store,
        )
    if run.current_step == "codex_planning":
        resumed = _execute_codex_planning(
            control_root=control_root, config=config, brief=brief, brief_digest=brief_digest,
            run=run, store=store, thread_id=thread_id,
        )
        if resumed.state in {"needs_input", "paused", "cancelled"}:
            return store.public_status(resumed.run_id)
        if resumed.state != "planned":
            raise RunnerError("planning_resume_not_ready", "resumed planning did not produce a planned state")
        ticketed = _execute_codex_tickets(
            control_root=control_root, config=config, brief_digest=brief_digest, run=resumed,
            store=store, spec_plan=load_json(_safe_artifact_directory(control_root, config, run.run_id) / "spec-plan.json"),
            thread_id=None,
        )
        if ticketed.state == "tickets_ready" and config.workflow_mode == "production":
            ticket_files = sorted(_safe_artifact_directory(control_root, config, run.run_id).glob("ticket-plan-*.json"))
            return _execute_codex_implementation(
                control_root=control_root, config=config, brief_digest=brief_digest, run=ticketed,
                store=store, ticket_plan=load_json(ticket_files[0]),
            )
        return {"created": False, **store.public_status(ticketed.run_id)}
    if run.current_step == "codex_ticket_planning":
        plans = sorted(_safe_artifact_directory(control_root, config, run.run_id).glob("spec-plan.json"))
        if not plans:
            raise RunnerError("spec_plan_missing", "ticket planning resume has no persisted SpecPlan")
        resumed = _execute_codex_tickets(
            control_root=control_root, config=config, brief_digest=brief_digest, run=run, store=store,
            spec_plan=load_json(plans[0]), thread_id=thread_id,
        )
        if resumed.state == "tickets_ready" and config.workflow_mode == "production":
            ticket_files = sorted(_safe_artifact_directory(control_root, config, run.run_id).glob("ticket-plan-*.json"))
            return _execute_codex_implementation(
                control_root=control_root, config=config, brief_digest=brief_digest, run=resumed,
                store=store, ticket_plan=load_json(ticket_files[0]),
            )
        return store.public_status(resumed.run_id)
    if run.current_step == "codex_second":
        resumed = _execute_second_codex(
            control_root=control_root,
            config=config,
            run=run,
            brief_digest=brief_digest,
            store=store,
            thread_id=thread_id,
        )
        if resumed.state in {"paused", "cancelled"}:
            if resumed.state == "cancelled":
                return _finalize_cancelled_codex(config=config, run=resumed, store=store)
            return store.public_status(resumed.run_id)
        return _verify_and_archive(control_root=control_root, config=config, run=resumed, store=store)
    raise RunnerError("resume_stage_unknown", f"paused Codex run has unsupported step: {run.current_step}")


def _recover_after_process_exit(*, control_root: Path, config: RunnerConfig, run: RunRecord, brief: str, brief_digest: str, store: Store) -> dict[str, object] | None:
    """Reconcile only evidence that can be proven locally; never replay an unknown SDK call."""
    if config.delivery_plan is not None:
        store.append_event(
            run_id=run.run_id,
            event_key=f"recovery:{run.run_id}:delivery:detected",
            event_type="recovery_detected",
            payload={"step": "delivery_plan", "state": run.state},
        )
        return {"created": False, **_run_delivery_plan(control_root=control_root, config=config, run=run, store=store)}
    if config.execution_backend != "deterministic_test":
        if run.state in {"starting", "failed", "cleanup_pending"}:
            raise RunnerError("recovery_blocked", "the SDK operation has no uniquely recoverable external result; inspect the persisted thread/turn before retry")
        return None
    store.append_event(
        run_id=run.run_id,
        event_key=f"recovery:{run.run_id}:{run.current_step}:detected",
        event_type="recovery_detected",
        payload={"step": run.current_step, "state": run.state, "backend_kind": run.backend_kind},
    )
    directory = _safe_artifact_directory(control_root, config, run.run_id)
    if run.current_step == "deterministic_example" and (directory / "handoff.json").is_file():
        store.append_event(
            run_id=run.run_id,
            event_key=f"recovery:{run.run_id}:deterministic_example:resumed",
            event_type="step_resumed",
            payload={"step": "deterministic_example", "evidence": "handoff.json"},
        )
        recovered = store.complete_deterministic_stage(run.run_id, f"start:{run.run_id}")
        _verify_and_archive(control_root=control_root, config=config, run=recovered, store=store, final_state="ready_for_next")
        current = store.find_by_run_id(run.run_id)
        assert current is not None
        store.append_event(
            run_id=run.run_id,
            event_key=f"recovery:{run.run_id}:next-stage",
            event_type="next_stage_started",
            payload={"from_step": "deterministic_example", "to_step": "deterministic_second"},
        )
        return _advance_second_stage(control_root=control_root, config=config, run=current, brief_digest=brief_digest, store=store)
    if run.current_step == "deterministic_example":
        store.append_event(
            run_id=run.run_id,
            event_key=f"recovery:{run.run_id}:deterministic_example:reexecute",
            event_type="step_resumed",
            payload={"step": "deterministic_example", "evidence": "no_completed_artifact"},
        )
        resumed = _execute_deterministic_example(
            control_root=control_root,
            config=config,
            brief=brief,
            brief_digest=brief_digest,
            run=run,
            store=store,
        )
        _verify_and_archive(control_root=control_root, config=config, run=resumed, store=store, final_state="ready_for_next")
        current = store.find_by_run_id(run.run_id)
        assert current is not None
        store.append_event(
            run_id=run.run_id,
            event_key=f"recovery:{run.run_id}:next-stage",
            event_type="next_stage_started",
            payload={"from_step": "deterministic_example", "to_step": "deterministic_second"},
        )
        return _advance_second_stage(control_root=control_root, config=config, run=current, brief_digest=brief_digest, store=store)
    if run.current_step == "deterministic_second" and (directory / "final.json").is_file():
        store.append_event(
            run_id=run.run_id,
            event_key=f"recovery:{run.run_id}:deterministic_second:resumed",
            event_type="step_resumed",
            payload={"step": "deterministic_second", "evidence": "final.json"},
        )
        recovered = store.complete_mechanical_stage(run.run_id, f"second:{run.run_id}", step_name="deterministic_second")
        return _verify_and_archive(control_root=control_root, config=config, run=recovered, store=store)
    return None


def start(*, brief_file: Path, config_file: Path, control_root: Path, launch_key: str, run_id: str | None = None) -> dict[str, object]:
    launch_key = _validate_launch_key(launch_key)
    control_root = control_root.expanduser().resolve()
    brief, brief_digest = read_brief(brief_file)
    config = RunnerConfig.from_file(config_file, control_root)
    requested_run_id = run_id or str(uuid.uuid4())
    try:
        uuid.UUID(requested_run_id)
    except ValueError as exc:
        raise RunnerError("invalid_run_id", "run_id must be a UUID") from exc

    store = Store.open(control_root, create=True)
    owner_token = f"{requested_run_id}:{os.getpid()}:{uuid.uuid4().hex}"
    lease_scope = f"{os.path.normcase(os.fspath(config.repository_path))}@{config.target_ref}"
    stale_after_seconds = 5.0
    if config.execution_backend == "deterministic_test" and os.environ.get("SPEC_RUNNER_TEST_LEASE_STALE_AFTER_SECONDS"):
        try:
            stale_after_seconds = max(0.0, float(os.environ["SPEC_RUNNER_TEST_LEASE_STALE_AFTER_SECONDS"]))
        except ValueError as exc:
            raise RunnerError("invalid_test_fault_config", "test lease stale timeout must be numeric") from exc
    heartbeat_stop: threading.Event | None = None
    heartbeat_thread: threading.Thread | None = None
    global_lease: ScopeLock | None = None
    try:
        existing = store.find_by_launch_key(launch_key)
        if existing:
            if existing.input_digest != brief_digest or existing.config_digest != config.digest:
                raise RunnerError(
                    "launch_key_input_conflict",
                    "launch_key already belongs to different normalized input",
                    details={"run_id": existing.run_id},
                )
            if existing.state in {"completed", "cancelled", "blocked_writer_busy"}:
                return {"created": False, **store.public_status(existing.run_id)}
            global_lease = _acquire_global_lease(scope=lease_scope, owner_token=owner_token, stale_after_seconds=stale_after_seconds)
            store.acquire_lease(scope=lease_scope, run_id=existing.run_id, owner_token=owner_token, stale_after_seconds=stale_after_seconds)
            heartbeat_stop, heartbeat_thread = _start_lease_heartbeat(control_root=control_root, scope=lease_scope, owner_token=owner_token, global_path=global_lease)
            if existing.state == "ready_for_next":
                final_status = _advance_second_stage(
                    control_root=control_root, config=config, run=existing, brief_digest=brief_digest, store=store
                )
                return {"created": False, **final_status}
            if existing.state == "planned":
                plan_path = _safe_artifact_directory(control_root, config, existing.run_id) / "spec-plan.json"
                if not plan_path.is_file():
                    raise RunnerError("spec_plan_missing", "planned run has no persisted SpecPlan")
                ticketed = _execute_codex_tickets(
                    control_root=control_root, config=config, brief_digest=brief_digest, run=existing,
                    store=store, spec_plan=load_json(plan_path),
                )
                return {"created": False, **store.public_status(ticketed.run_id)}
            if existing.state == "tickets_ready" and config.workflow_mode == "production":
                ticket_files = sorted(_safe_artifact_directory(control_root, config, existing.run_id).glob("ticket-plan-*.json"))
                if not ticket_files:
                    raise RunnerError("ticket_plan_missing", "tickets_ready run has no persisted TicketPlan")
                delivered = _execute_codex_implementation(
                    control_root=control_root, config=config, brief_digest=brief_digest, run=existing,
                    store=store, ticket_plan=load_json(ticket_files[0]),
                )
                return {"created": False, **delivered}
            if existing.state == "needs_input" and not store.control_for_run(existing.run_id):
                worker_result = _safe_artifact_directory(control_root, config, existing.run_id) / "worker-result.json"
                questions: list[dict[str, object]] = []
                if worker_result.is_file():
                    try:
                        document = json.loads(worker_result.read_text(encoding="utf-8"))
                        questions = [item for item in document.get("questions", []) if isinstance(item, dict) and isinstance(item.get("id"), str)]
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                        questions = []
                answer_ids = {str(item["question_id"]) for item in store.answers_for_run(existing.run_id)}
                if questions and {str(item["id"]) for item in questions}.issubset(answer_ids) and config.execution_backend == "codex_sdk":
                    resumed = _resume_codex_stage(control_root=control_root, config=config, run=existing, brief=brief, brief_digest=brief_digest, store=store)
                    return {"created": False, **resumed}
                return {"created": False, **store.public_status(existing.run_id)}
            if existing.state == "paused" and not store.control_for_run(existing.run_id):
                if config.execution_backend == "codex_sdk":
                    resumed = _resume_codex_stage(
                        control_root=control_root,
                        config=config,
                        run=existing,
                        brief=brief,
                        brief_digest=brief_digest,
                        store=store,
                    )
                    return {"created": False, **resumed}
                resumed = _advance_second_stage(
                    control_root=control_root, config=config, run=existing, brief_digest=brief_digest, store=store
                )
                return {"created": False, **resumed}
            try:
                recovered_status = _recover_after_process_exit(
                    control_root=control_root, config=config, run=existing, brief=brief, brief_digest=brief_digest, store=store
                )
            except RunnerError as exc:
                store.set_run_state(existing.run_id, "blocked")
                store.append_event(
                    run_id=existing.run_id,
                    event_key=f"recovery:{existing.run_id}:blocked",
                    event_type="recovery_blocked",
                    payload={"code": exc.code, "message": exc.message},
                )
                raise
            if recovered_status is not None:
                return {"created": False, **recovered_status}
            return {"created": False, **store.public_status(existing.run_id)}
        if store.find_by_run_id(requested_run_id):
            raise RunnerError("run_id_conflict", "run_id already exists; choose another UUID")

        timestamp = now()
        stage_name = "delivery_plan" if config.delivery_plan is not None else ("deterministic_example" if config.execution_backend == "deterministic_test" else ("codex_planning" if config.workflow_mode == "production" else "codex_example"))
        record = RunRecord(
            run_id=requested_run_id,
            launch_key=launch_key,
            input_digest=brief_digest,
            config_digest=config.digest,
            repository_path=os.fspath(config.repository_path),
            target_ref=config.target_ref,
            artifact_root=config.artifact_root.as_posix(),
            backend_kind=config.execution_backend,
            state="starting",
            current_step=stage_name,
            log_path=(Path("logs") / f"{requested_run_id}.jsonl").as_posix(),
            created_at=timestamp,
            updated_at=timestamp,
        )
        operation_id = f"start:{requested_run_id}"
        store.create_run(record, operation_id)
        store.register_runtime(
            requested_run_id,
            pid=os.getpid(),
            owner_token=owner_token,
            log_path=os.fspath(control_root / record.log_path),
        )
        try:
            store.acquire_lease(scope=lease_scope, run_id=requested_run_id, owner_token=owner_token, stale_after_seconds=stale_after_seconds)
        except RunnerError:
            # Keep the attempted run as an explicit blocker instead of leaving
            # a second starting writer that a later process might adopt.
            store.fail_run(requested_run_id, operation_id, state="blocked_writer_busy")
            raise
        global_lease = _acquire_global_lease(scope=lease_scope, owner_token=owner_token, stale_after_seconds=stale_after_seconds)
        heartbeat_stop, heartbeat_thread = _start_lease_heartbeat(control_root=control_root, scope=lease_scope, owner_token=owner_token, global_path=global_lease)
        store.write_log(control_root, requested_run_id, {"event": "run_started", "backend_kind": config.execution_backend})
        _test_fault_pause(control_root=control_root, run_id=requested_run_id, point="after_first_intent")
        try:
            if config.delivery_plan is not None:
                return {"created": True, **_run_delivery_plan(control_root=control_root, config=config, run=record, store=store)}
            if config.execution_backend == "deterministic_test":
                finished = _execute_deterministic_example(
                    control_root=control_root, config=config, brief=brief, brief_digest=brief_digest, run=record, store=store
                )
            elif config.workflow_mode == "production":
                finished = _execute_codex_planning(
                    control_root=control_root, config=config, brief=brief, brief_digest=brief_digest, run=record, store=store
                )
            else:
                finished = _execute_codex_example(
                    control_root=control_root, config=config, brief=brief, brief_digest=brief_digest, run=record, store=store
                )
        except RunnerError:
            store.fail_run(record.run_id, operation_id)
            raise
        if finished.state in {"paused", "cancelled"}:
            if finished.state == "cancelled":
                return {"created": True, **_finalize_cancelled_codex(config=config, run=finished, store=store)}
            return {"created": True, **store.public_status(finished.run_id)}
        if finished.state == "needs_input":
            return {"created": True, **store.public_status(finished.run_id)}
        if finished.state == "planned":
            plan_path = _safe_artifact_directory(control_root, config, finished.run_id) / "spec-plan.json"
            ticketed = _execute_codex_tickets(
                control_root=control_root, config=config, brief_digest=brief_digest, run=finished,
                store=store, spec_plan=load_json(plan_path),
            )
            if ticketed.state == "tickets_ready" and config.workflow_mode == "production":
                ticket_files = sorted(_safe_artifact_directory(control_root, config, ticketed.run_id).glob("ticket-plan-*.json"))
                delivered = _execute_codex_implementation(
                    control_root=control_root, config=config, brief_digest=brief_digest, run=ticketed,
                    store=store, ticket_plan=load_json(ticket_files[0]),
                )
                return {"created": True, **delivered}
            return {"created": True, **store.public_status(ticketed.run_id)}
        _verify_and_archive(
            control_root=control_root,
            config=config,
            run=finished,
            store=store,
            final_state="ready_for_next",
        )
        store.append_event(
            run_id=finished.run_id,
            event_key=f"normal:{finished.run_id}:next-stage",
            event_type="next_stage_started",
            payload={"from_step": finished.current_step, "to_step": "deterministic_second" if config.execution_backend == "deterministic_test" else "codex_second"},
        )
        final_status = _advance_second_stage(
            control_root=control_root,
            config=config,
            run=store.find_by_run_id(finished.run_id) or finished,
            brief_digest=brief_digest,
            store=store,
        )
        return {"created": True, **final_status}
    finally:
        if heartbeat_stop is not None:
            heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1.0)
        try:
            store.release_lease(scope=lease_scope, owner_token=owner_token)
        except RunnerError:
            pass
        _release_global_lease(global_lease, owner_token)
        store.close()


def control(*, control_root: Path, run_id: str, requested_state: str) -> dict[str, object]:
    store = Store.open(control_root.expanduser().resolve(), create=False)
    try:
        record = store.find_by_run_id(run_id)
        if record is None:
            raise RunnerError("unknown_run", f"run does not exist: {run_id}")
        if record.state == "completed" and requested_state != "resume_requested":
            return {"accepted": False, "reason": "run_already_completed", **store.public_status(run_id)}
        request = store.request_control(run_id, requested_state)
        return {"accepted": True, "control_request": request, **store.public_status(run_id)}
    finally:
        store.close()


def resume(*, brief_file: Path, config_file: Path, control_root: Path, launch_key: str) -> dict[str, object]:
    control_root = control_root.expanduser().resolve()
    store = Store.open(control_root, create=False)
    try:
        existing = store.find_by_launch_key(launch_key)
        if existing is None:
            raise RunnerError("unknown_run", "resume requires an existing launch_key")
        if existing.state == "cancelled":
            raise RunnerError("cancelled_run", "cancelled runs require explicit creation of a new launch identity")
        store.clear_control(existing.run_id)
    finally:
        store.close()
    return start(brief_file=brief_file, config_file=config_file, control_root=control_root, launch_key=launch_key)


def launch(*, brief_file: Path, config_file: Path, control_root: Path, launch_key: str) -> dict[str, object]:
    """Start a detached Runner and return only after its durable handshake."""
    launch_key = _validate_launch_key(launch_key)
    control_root = control_root.expanduser().resolve()
    # Validate all user inputs before creating the child or control files.
    read_brief(brief_file)
    RunnerConfig.from_file(config_file, control_root)
    run_id = str(uuid.uuid4())
    log_root = control_root / "launcher-logs"
    log_root.mkdir(parents=True, exist_ok=True)
    stdout_path = log_root / f"{run_id}.stdout.log"
    stderr_path = log_root / f"{run_id}.stderr.log"
    command = [
        sys.executable,
        "-m",
        "spec_runner.cli",
        "start",
        "--brief",
        os.fspath(brief_file),
        "--config",
        os.fspath(config_file),
        "--control-root",
        os.fspath(control_root),
        "--launch-key",
        launch_key,
        "--run-id",
        run_id,
    ]
    creation_flags = 0
    if os.name == "nt":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        child = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
            creationflags=creation_flags,
            cwd=os.fspath(control_root),
        )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RunnerError(
                "launch_handshake_failed",
                "detached Runner exited before claiming the run",
                details={"exit_code": child.returncode, "stderr_log": os.fspath(stderr_path)},
            )
        try:
            store = Store.open(control_root, create=False)
        except RunnerError:
            time.sleep(0.05)
            continue
        try:
            record = store.find_by_run_id(run_id)
            runtime = store.runtime_for_run(run_id) if record else None
            if record and runtime and int(runtime["pid"]) == child.pid:
                return {
                    "started": True,
                    "pid": child.pid,
                    "run_id": run_id,
                    "log_path": os.fspath(stdout_path),
                    "run": store.public_status(run_id),
                }
        finally:
            store.close()
        time.sleep(0.05)
    raise RunnerError(
        "launch_handshake_timeout",
        "detached Runner did not claim the run before the handshake deadline",
        details={"pid": child.pid, "stdout_log": os.fspath(stdout_path), "stderr_log": os.fspath(stderr_path)},
    )


def status(*, control_root: Path, run_id: str | None) -> dict[str, object]:
    store = Store.open(control_root.expanduser().resolve(), create=False)
    try:
        if run_id:
            return store.public_status(run_id)
        return {"runs": store.list_status()}
    finally:
        store.close()


def doctor(*, config_file: Path | None, control_root: Path) -> dict[str, object]:
    report: dict[str, object] = {
        "read_only": True,
        "control_database_exists": (control_root.expanduser().resolve() / "spec-runner.sqlite3").is_file(),
        "supported_backends": ["deterministic_test", "codex_sdk"],
    }
    if config_file:
        config = RunnerConfig.from_file(config_file, control_root.expanduser().resolve())
        report["config"] = {
            "valid": True,
            "repository_path": os.fspath(config.repository_path),
            "execution_backend": config.execution_backend,
            "requested_model": config.model_name,
            "requested_effort": config.effort,
        }
    return report
