from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .config import RunnerConfig, read_brief
from .codex_adapter import CodexAdapter, CodexWorkerResult
from .errors import RunnerError
from .store import RunRecord, Store, now
from .verification import verify_run
from .plans import load_json
from .multi_spec import run_local_delivery


def _validate_launch_key(value: str) -> str:
    if not value or len(value) > 200 or any(character.isspace() for character in value):
        raise RunnerError("invalid_launch_key", "launch_key must be non-empty, at most 200 characters, and contain no whitespace")
    return value


def _start_lease_heartbeat(*, control_root: Path, scope: str, owner_token: str) -> tuple[threading.Event, threading.Thread]:
    """Keep a long-running SDK call from looking stale to a recovery process."""
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(5.0):
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
    return {
        "schema_version": "spec-runner-worker-result/v1",
        "stage": stage,
        "input_digest": brief_digest,
        "outcome": declared.get("outcome", "completed" if result.status == "completed" else result.status),
        "artifacts": declared.get("artifacts", []),
        "blockers": declared.get("blockers", [result.error] if result.error else []),
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
    result: CodexWorkerResult = CodexAdapter().run(
        prompt=prompt,
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
    completed = store.complete_codex_stage(
        run.run_id,
        f"start:{run.run_id}",
        thread_id=result.thread_id,
        turn_id=result.turn_id,
        state=state,
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
    result = CodexAdapter().run(
        prompt=prompt,
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
            store.acquire_lease(scope=lease_scope, run_id=existing.run_id, owner_token=owner_token, stale_after_seconds=stale_after_seconds)
            heartbeat_stop, heartbeat_thread = _start_lease_heartbeat(control_root=control_root, scope=lease_scope, owner_token=owner_token)
            if existing.state == "ready_for_next":
                final_status = _advance_second_stage(
                    control_root=control_root, config=config, run=existing, brief_digest=brief_digest, store=store
                )
                return {"created": False, **final_status}
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
        stage_name = "delivery_plan" if config.delivery_plan is not None else ("deterministic_example" if config.execution_backend == "deterministic_test" else "codex_example")
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
        heartbeat_stop, heartbeat_thread = _start_lease_heartbeat(control_root=control_root, scope=lease_scope, owner_token=owner_token)
        store.write_log(control_root, requested_run_id, {"event": "run_started", "backend_kind": config.execution_backend})
        _test_fault_pause(control_root=control_root, run_id=requested_run_id, point="after_first_intent")
        try:
            if config.delivery_plan is not None:
                return {"created": True, **_run_delivery_plan(control_root=control_root, config=config, run=record, store=store)}
            if config.execution_backend == "deterministic_test":
                finished = _execute_deterministic_example(
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
