from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from .config import RunnerConfig, read_brief
from .codex_adapter import CodexAdapter, CodexWorkerResult
from .errors import RunnerError
from .store import RunRecord, Store, now


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


def _execute_codex_example(
    *, control_root: Path, config: RunnerConfig, brief: str, brief_digest: str, run: RunRecord, store: Store
) -> RunRecord:
    prompt = (
        "You are a bounded Spec Runner worker. Read the supplied brief and produce a concise structured response. "
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
        json.dumps(
            {"schema_version": "spec-runner-worker-result/v1", "stage": "example", **result.public()},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
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
    try:
        existing = store.find_by_launch_key(launch_key)
        if existing:
            if existing.input_digest != brief_digest or existing.config_digest != config.digest:
                raise RunnerError(
                    "launch_key_input_conflict",
                    "launch_key already belongs to different normalized input",
                    details={"run_id": existing.run_id},
                )
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
        return {"created": True, **store.public_status(finished.run_id)}
    finally:
        store.close()


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
