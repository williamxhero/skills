from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .config import RunnerConfig, read_brief
from .codex_adapter import CodexAdapter, CodexWorkerResult
from .errors import RunnerError
from .store import RunRecord, Store, now
from .verification import verify_run


def _validate_launch_key(value: str) -> str:
    if not value or len(value) > 200 or any(character.isspace() for character in value):
        raise RunnerError("invalid_launch_key", "launch_key must be non-empty, at most 200 characters, and contain no whitespace")
    return value


def _safe_artifact_directory(control_root: Path, config: RunnerConfig, run_id: str) -> Path:
    root = (control_root / config.artifact_root).resolve()
    directory = (root / run_id).resolve()
    if root not in (directory, *directory.parents):
        raise RunnerError("artifact_path_escape", "run artifact directory escaped artifact_root")
    return directory


def _execute_deterministic_example(
    *, control_root: Path, config: RunnerConfig, brief: str, brief_digest: str, run: RunRecord, store: Store
) -> RunRecord:
    if "example" not in config.allowed_stages:
        raise RunnerError("stage_not_allowed", "deterministic_test requires the example stage to be allowed")
    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    artifact_directory.mkdir(parents=True, exist_ok=False)
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


def _execute_codex_example(
    *, control_root: Path, config: RunnerConfig, brief: str, brief_digest: str, run: RunRecord, store: Store
) -> RunRecord:
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
    )
    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    artifact_directory.mkdir(parents=True, exist_ok=False)
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
    return store.complete_codex_stage(
        run.run_id,
        f"start:{run.run_id}",
        thread_id=result.thread_id,
        turn_id=result.turn_id,
        state="turn_completed",
    )


def _execute_second_codex(
    *, control_root: Path, config: RunnerConfig, run: RunRecord, brief_digest: str, store: Store
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
    return store.complete_codex_stage(
        run.run_id,
        operation_id,
        thread_id=result.thread_id,
        turn_id=result.turn_id,
        state="turn_completed",
        step_name=step_name,
        worker_id=f"codex_sdk:{run.run_id}:{step_name}",
    )


def _verify_and_archive(
    *, control_root: Path, config: RunnerConfig, run: RunRecord, store: Store, final_state: str = "completed"
) -> dict[str, object]:
    worker = store.workers_for_run(run.run_id)[-1]
    try:
        receipt = verify_run(control_root=control_root, run=run, worker=worker)
        store.record_verification(run.run_id, receipt)
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
    store.mark_archived(run.run_id, state=final_state)
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
    return store.complete_mechanical_stage(run.run_id, operation_id, step_name=step_name)


def _advance_second_stage(*, control_root: Path, config: RunnerConfig, run: RunRecord, brief_digest: str, store: Store) -> dict[str, object]:
    control = store.control_for_run(run.run_id)
    if control and control["requested_state"] in {"pause_requested", "cancel_requested"}:
        stop_state = "paused" if control["requested_state"] == "pause_requested" else "cancelled"
        store.set_run_state(run.run_id, stop_state)
        return store.public_status(run.run_id)
    if config.execution_backend == "deterministic_test":
        second = _execute_second_deterministic(control_root=control_root, config=config, run=run, store=store)
    else:
        second = _execute_second_codex(
            control_root=control_root, config=config, run=run, brief_digest=brief_digest, store=store
        )
    return _verify_and_archive(control_root=control_root, config=config, run=second, store=store)


def _recover_after_process_exit(*, control_root: Path, config: RunnerConfig, run: RunRecord, brief_digest: str, store: Store) -> dict[str, object] | None:
    """Reconcile only evidence that can be proven locally; never replay an unknown SDK call."""
    if config.execution_backend != "deterministic_test":
        if run.state in {"starting", "failed", "cleanup_pending"}:
            raise RunnerError("recovery_blocked", "the SDK operation has no uniquely recoverable external result; inspect the persisted thread/turn before retry")
        return None
    directory = _safe_artifact_directory(control_root, config, run.run_id)
    if run.current_step == "deterministic_example" and (directory / "handoff.json").is_file():
        recovered = store.complete_deterministic_stage(run.run_id, f"start:{run.run_id}")
        _verify_and_archive(control_root=control_root, config=config, run=recovered, store=store, final_state="ready_for_next")
        current = store.find_by_run_id(run.run_id)
        assert current is not None
        return _advance_second_stage(control_root=control_root, config=config, run=current, brief_digest=brief_digest, store=store)
    if run.current_step == "deterministic_second" and (directory / "final.json").is_file():
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
    lease_scope = f"{config.repository_path.as_posix()}@{config.target_ref}"
    try:
        existing = store.find_by_launch_key(launch_key)
        if existing:
            if existing.input_digest != brief_digest or existing.config_digest != config.digest:
                raise RunnerError(
                    "launch_key_input_conflict",
                    "launch_key already belongs to different normalized input",
                    details={"run_id": existing.run_id},
                )
            if existing.state == "ready_for_next":
                store.acquire_lease(scope=lease_scope, run_id=existing.run_id, owner_token=owner_token)
                final_status = _advance_second_stage(
                    control_root=control_root, config=config, run=existing, brief_digest=brief_digest, store=store
                )
                return {"created": False, **final_status}
            recovered_status = _recover_after_process_exit(
                control_root=control_root, config=config, run=existing, brief_digest=brief_digest, store=store
            )
            if recovered_status is not None:
                return {"created": False, **recovered_status}
            return {"created": False, **store.public_status(existing.run_id)}
        if store.find_by_run_id(requested_run_id):
            raise RunnerError("run_id_conflict", "run_id already exists; choose another UUID")

        timestamp = now()
        stage_name = "deterministic_example" if config.execution_backend == "deterministic_test" else "codex_example"
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
        store.acquire_lease(scope=lease_scope, run_id=requested_run_id, owner_token=owner_token)
        store.write_log(control_root, requested_run_id, {"event": "run_started", "backend_kind": config.execution_backend})
        try:
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
        _verify_and_archive(
            control_root=control_root,
            config=config,
            run=finished,
            store=store,
            final_state="ready_for_next",
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
