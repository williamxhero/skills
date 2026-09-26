from __future__ import annotations

import json
import hashlib
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .config import DEFAULT_GIT_TIMEOUT_SECONDS, RunnerConfig, read_brief
from .codex_adapter import CodexAdapter, CodexWorkerResult
from .errors import RunnerError
from .store import RunRecord, Store, now
from .verification import verify_run
from .plans import digest, load_json, validate_spec_plan, validate_ticket_plan
from .multi_spec import run_local_delivery
from .delivery import cleanup_managed_workspace, git_sha, merge_local, prepare_workspace, validate_candidate_write_scope, validate_review, verify_candidate
from .tracker import read_local, publish_local
from .github_tracker import GitHubTracker
from .scope_lock import ScopeLock
from .production_gates import IMPLEMENTATION_SCHEMA, implementation_artifacts, independent_review
from .github_delivery import GitHubDelivery
from .store import _process_alive
from .continuation import ContinuationBundle, read_bundle, write_bundle_atomic
from .recovery import (
    FaultFamily,
    RecoveryAction,
    RecoveryDecision,
    RecoverySnapshot,
    decide_recovery,
    observation_from_error,
)


def _implementation_write_root(*, workspace: Path, config: RunnerConfig, create: bool) -> Path:
    if len(config.acceptance_paths) != 1:
        raise RunnerError("write_scope_missing", "production implementation requires exactly one trusted write-scope root")
    root = workspace.resolve()
    relative = PurePosixPath(config.acceptance_paths[0])
    target = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RunnerError("write_scope_invalid", "trusted write-scope path crosses a symlink")
    resolved = target.resolve(strict=False)
    if root not in resolved.parents:
        raise RunnerError("write_scope_invalid", "trusted write-scope path escapes the implementation workspace")
    if create:
        resolved.mkdir(parents=True, exist_ok=True)
    elif not resolved.is_dir():
        raise RunnerError("write_scope_missing", "persisted implementation write-scope directory is missing")
    return resolved


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
            schema=schema or {"type": "object", "properties": {"outcome": {"type": "string"}, "artifacts": {"type": "array", "items": {"type": "string"}}, "blockers": {"type": "array", "items": {"type": "string"}}, "questions": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}}, "required": ["id", "question", "options"], "additionalProperties": False}}}, "required": ["outcome", "artifacts", "blockers", "questions"], "additionalProperties": False},
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
    # An older Runner may still hold the timestamp lease. Reclaim only when its
    # recorded PID is proven dead; age alone is never ownership evidence.
    if path.exists():
        try:
            legacy = json.loads(path.read_text(encoding="utf-8"))
            pid = int(legacy.get("pid", 0))
        except (OSError, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RunnerError("legacy_scope_lease_present", "old repository lease cannot be safely inspected", details={"path": str(path)}) from exc
        if _process_alive(pid):
            raise RunnerError("legacy_scope_lease_present", "old repository lease belongs to an active process", details={"path": str(path), "owner_pid": pid})
        try:
            path.unlink()
        except OSError as exc:
            raise RunnerError("legacy_scope_lease_present", "dead legacy repository lease could not be reclaimed", details={"path": str(path)}) from exc
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


def _continuation_workspace_identity(*, workspace: Path, workspace_info: dict[str, object],
                                     git_timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS) -> dict[str, object]:
    """Capture bounded Git references without copying source or sensitive paths."""
    status = _git_binary(workspace, "status", "--porcelain=v1", "--untracked-files=all", timeout_seconds=git_timeout_seconds)
    staged = _git_binary(workspace, "diff", "--cached", "--binary", timeout_seconds=git_timeout_seconds)
    unstaged = _git_binary(workspace, "diff", "--binary", timeout_seconds=git_timeout_seconds)
    return {
        "path": os.fspath(workspace.resolve()),
        "repository": workspace_info.get("repository"),
        "branch": workspace_info.get("branch"),
        "base_sha": workspace_info.get("base_sha"),
        "head": git_sha(workspace, timeout_seconds=git_timeout_seconds),
        "working_tree": {
            "dirty": bool(status),
            "status_digest": hashlib.sha256(status).hexdigest(),
            "status_entry_count": len([line for line in status.splitlines() if line]),
            "index_diff_digest": hashlib.sha256(staged).hexdigest(),
            "worktree_diff_digest": hashlib.sha256(unstaged).hexdigest(),
            "binary_state_preserved": True,
            "source": "git-status-and-binary-diff-digests",
        },
    }


def _reconcile_continuation_bundles(*, control_root: Path, config: RunnerConfig,
                                    run: RunRecord, store: Store) -> list[dict[str, object]]:
    """Adopt only this run's atomically written bundles into the Store."""
    directory = _safe_artifact_directory(control_root, config, run.run_id)
    if not directory.exists():
        return []
    receipts: list[dict[str, object]] = []
    for path in sorted(directory.glob("continuation-*.json")):
        if path.is_symlink() or not path.is_file():
            raise RunnerError("continuation_path_invalid", "continuation bundle must be a regular file")
        bundle = read_bundle(path).public()
        expected_spec = path.name[len("continuation-"):-len(".json")]
        if bundle["run_id"] != run.run_id or bundle["spec_key"] != expected_spec:
            raise RunnerError("continuation_receipt_identity_invalid", "continuation bundle path and identity do not match this run")
        receipts.append(store.record_continuation_bundle(run_id=run.run_id, bundle_path=path, bundle=bundle))
    return receipts


def _write_json_atomic(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def _takeover_context(*, control_root: Path, config: RunnerConfig, run: RunRecord,
                      expected_takeover_key: str | None = None) -> dict[str, object] | None:
    """Load the immutable takeover continuation context for one Runner run."""
    path = _safe_artifact_directory(control_root, config, run.run_id) / "takeover-context.json"
    if path.is_symlink():
        raise RunnerError("takeover_context_invalid", "takeover continuation context cannot be a symlink")
    if not path.is_file():
        return None
    context = load_json(path)
    if context.get("schema_version") != "spec-runner-takeover-context/v1":
        raise RunnerError("takeover_context_invalid", "takeover continuation context has an unexpected schema")
    if context.get("run_id") != run.run_id:
        raise RunnerError("takeover_context_invalid", "takeover continuation context belongs to another run")
    if not isinstance(context.get("takeover_key"), str) or not context["takeover_key"]:
        raise RunnerError("takeover_context_invalid", "takeover continuation context has no takeover identity")
    if expected_takeover_key is not None and context["takeover_key"] != expected_takeover_key:
        raise RunnerError("takeover_context_invalid", "takeover continuation context belongs to another takeover")
    record = context.get("record")
    if not isinstance(record, dict) or not isinstance(record.get("frontier"), dict) or not isinstance(record.get("report"), dict):
        raise RunnerError("takeover_context_invalid", "takeover continuation context has incomplete evidence")
    if record.get("takeover_key") != context["takeover_key"]:
        raise RunnerError("takeover_context_invalid", "takeover continuation context identity does not match its record")
    return context


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


def _recovery_episode_identity(*, run_id: str, operation_kind: str, stage: str, generation: int = 0) -> str:
    identity = f"{run_id}:{operation_kind}:{stage}:{generation}"
    return "episode-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _record_recovery_failure(*, run: RunRecord, store: Store, operation_id: str,
                             error: RunnerError) -> RecoveryDecision:
    """Persist the observed fault and execute its deterministic state transition."""
    operation_kind = "codex_turn" if run.backend_kind == "codex_sdk" else run.backend_kind
    episode_id = _recovery_episode_identity(
        run_id=run.run_id, operation_kind=operation_kind, stage=run.current_step,
    )
    existing = store.recovery_episode(episode_id) or {}
    if not existing:
        existing = store.upsert_recovery_episode(
            episode_id=episode_id, run_id=run.run_id, operation_kind=operation_kind,
            stage=run.current_step, generation=0,
        )
    workers = store.workers_for_run(run.run_id)
    worker = workers[-1] if workers else {}
    fault = error.details.get("fault_observation") if isinstance(error.details, dict) else None
    observation = observation_from_error(
        operation_kind=operation_kind,
        error=fault if isinstance(fault, dict) else error,
        run_id=run.run_id,
        stage=run.current_step,
        attempt=int(existing.get("same_thread_attempts") or 0) + int(existing.get("capacity_attempts") or 0) + 1,
        worker_id=(str(fault.get("worker_id")) if isinstance(fault, dict) and fault.get("worker_id") else (str(worker.get("worker_id")) if worker.get("worker_id") else None)),
        thread_id=(str(fault.get("thread_id")) if isinstance(fault, dict) and fault.get("thread_id") else (str(error.details.get("thread_id")) if error.details.get("thread_id") else None)),
        turn_id=(str(fault.get("turn_id")) if isinstance(fault, dict) and fault.get("turn_id") else (str(error.details.get("turn_id")) if error.details.get("turn_id") else None)),
        last_verified_progress=None,
    )
    counters = {
        key: int(existing.get(key) or 0)
        for key in (
            "same_thread_attempts", "capacity_attempts", "route_probe_attempts",
            "clean_probe_attempts", "migration_attempts", "no_progress_attempts",
        )
    }
    prior_episodes = store.recovery_for_run(run.run_id).get("episodes", [])
    prior = next((item for item in prior_episodes if item.get("episode_id") == episode_id), {})
    decisions = prior.get("decisions", []) if isinstance(prior, dict) else []
    prior_action = decisions[-1].get("decision", {}).get("action") if decisions else None
    counter_name: str | None = None
    if observation.family == FaultFamily.CAPACITY.value:
        counter_name = "capacity_attempts"
    elif observation.family in {
        FaultFamily.FAST_NOT_CONFIGURED.value,
        FaultFamily.STREAM_DISCONNECTED.value,
        FaultFamily.UNKNOWN.value,
    }:
        counter_name = "same_thread_attempts"
    elif observation.family == FaultFamily.ROUTE_NOT_FOUND.value and prior_action == RecoveryAction.USE_APPROVED_ROUTE.value:
        counter_name = "route_probe_attempts"
    elif observation.family == FaultFamily.ENCRYPTED_ITEM_MISMATCH.value:
        if prior_action == RecoveryAction.PROBE_CLEAN_CONTEXT.value:
            counter_name = "clean_probe_attempts"
        elif prior_action == RecoveryAction.REQUEST_CLEAN_MIGRATION.value:
            counter_name = "migration_attempts"
    attempt_identity = observation.request_id or observation.turn_id
    if counter_name and attempt_identity:
        reserved = store.reserve_recovery_budget(
            reservation_id=f"{episode_id}:attempt:{attempt_identity}",
            episode_id=episode_id, counter_name=counter_name,
        )
        counters = {
            key: int(reserved.get(key) or 0)
            for key in counters
        }
    snapshot = RecoverySnapshot(
        run_id=run.run_id,
        operation_kind=operation_kind,
        stage=run.current_step,
        active_execution=worker.get("state") == "running" and observation.execution_outcome == "unknown",
        request_admission=observation.request_admission,
        execution_outcome=observation.execution_outcome,
        same_thread_attempts=counters["same_thread_attempts"],
        capacity_attempts=counters["capacity_attempts"],
        route_probe_attempts=counters["route_probe_attempts"],
        clean_probe_attempts=counters["clean_probe_attempts"],
        migration_attempts=counters["migration_attempts"],
        no_progress_attempts=counters["no_progress_attempts"],
        thread_id=observation.thread_id or (str(worker.get("external_thread_id")) if worker.get("external_thread_id") else None),
        turn_id=observation.turn_id or (str(worker.get("external_turn_id")) if worker.get("external_turn_id") else None),
    )
    decision = decide_recovery(snapshot, [observation], now=datetime.now(timezone.utc))
    state = decision.action.value
    store.upsert_recovery_episode(
        episode_id=episode_id, run_id=run.run_id, operation_kind=operation_kind,
        stage=run.current_step, generation=0, state=state, counters=counters,
        retry_deadline=decision.next_check_at if decision.action == RecoveryAction.WAIT_RETRY else None,
        wait_deadline=decision.next_check_at if decision.action == RecoveryAction.SERVICE_WAIT else None,
        last_verified_progress=observation.last_verified_progress,
    )
    observation_id = f"{episode_id}:observation:{observation.fingerprint}:{observation.turn_id or 'no-turn'}"
    store.record_recovery_observation(
        observation_id=observation_id, episode_id=episode_id,
        observation=observation.public(),
    )
    store.append_event(
        run_id=run.run_id,
        event_key=f"recovery:{operation_id}:observation:{observation_id}",
        event_type="fault_observed",
        payload={"operation_id": operation_id, "episode_id": episode_id,
                 "observation": observation.public()},
    )
    decision_id = f"{episode_id}:decision:{decision.action.value}:{observation.fingerprint}:{counters['same_thread_attempts']}:{counters['capacity_attempts']}"
    store.record_recovery_decision(
        decision_id=decision_id, episode_id=episode_id, decision=decision.public(),
    )
    store.append_event(
        run_id=run.run_id,
        event_key=f"recovery:{operation_id}:{decision_id}",
        event_type="recovery_decision_recorded",
        payload={"operation_id": operation_id, "episode_id": episode_id, "decision": decision.public()},
    )
    action_events = {
        RecoveryAction.OBSERVE: "reconcile_started",
        RecoveryAction.WAIT_RETRY: "retry_scheduled",
        RecoveryAction.SERVICE_WAIT: "service_wait",
        RecoveryAction.RESUME_SAME_THREAD: "retry_started",
        RecoveryAction.USE_APPROVED_ROUTE: "route_changed",
        RecoveryAction.PROBE_CLEAN_CONTEXT: "probe_result",
        RecoveryAction.REQUEST_CLEAN_MIGRATION: "migration_requested",
        RecoveryAction.ADOPT_RESULT: "progress_verified",
        RecoveryAction.BLOCKED: "recovery_blocked",
    }
    event_type = action_events.get(decision.action)
    if event_type:
        store.append_event(
            run_id=run.run_id,
            event_key=f"recovery:{operation_id}:{decision_id}:{event_type}",
            event_type=event_type,
            payload={"operation_id": operation_id, "episode_id": episode_id,
                     "action": decision.action.value, "next_check_at": decision.next_check_at,
                     "reason": decision.reason},
        )
    return decision

def _recovery_waits(*, run: RunRecord, store: Store, config: RunnerConfig) -> bool:
    """Prevent a relaunch from bypassing a persisted recovery wait or block."""
    recovery = store.recovery_for_run(run.run_id).get("episodes", [])
    if not recovery:
        return False
    episode = recovery[-1]
    decisions = episode.get("decisions") if isinstance(episode, dict) else None
    decision = decisions[-1].get("decision") if isinstance(decisions, list) and decisions else None
    if not isinstance(decision, dict):
        return False
    action = str(decision.get("action") or "")
    if run.state not in {action, "paused"} or action == RecoveryAction.OBSERVE.value:
        return False
    if action in {
        RecoveryAction.WAIT_RETRY.value,
        RecoveryAction.SERVICE_WAIT.value,
        RecoveryAction.WAIT_FOR_CONFIG.value,
        RecoveryAction.BLOCKED.value,
        RecoveryAction.NEEDS_INPUT.value,
        RecoveryAction.PROBE_CLEAN_CONTEXT.value,
        RecoveryAction.REQUEST_CLEAN_MIGRATION.value,
        RecoveryAction.USE_APPROVED_ROUTE.value,
        RecoveryAction.RECONNECT_RUNTIME.value,
    }:
        if run.state == "paused":
            control = store.control_for_run(run.run_id)
            if control and control.get("requested_state") in {"pause_requested", "cancel_requested"}:
                return True
            store.set_run_state(run.run_id, action)
        deadline = episode.get("wait_deadline") if action == RecoveryAction.SERVICE_WAIT.value else episode.get("retry_deadline")
        if deadline:
            try:
                parsed_deadline = datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
                if parsed_deadline.tzinfo is None:
                    parsed_deadline = parsed_deadline.replace(tzinfo=timezone.utc)
                if parsed_deadline > datetime.now(timezone.utc):
                    return True
            except (TypeError, ValueError):
                return True
            store.fail_run(run.run_id, f"start:{run.run_id}", state="failed")
            store.append_event(
                run_id=run.run_id,
                event_key=f"recovery:{run.run_id}:wait-expired:{action}",
                event_type="recovery_wait_expired",
                payload={"action": action, "deadline": deadline},
            )
            return False
        return True
    return False


def _recovery_wait_record(*, store: Store, run_id: str) -> tuple[str, str] | None:
    recovery = store.recovery_for_run(run_id).get("episodes", [])
    if not isinstance(recovery, list) or not recovery:
        return None
    episode = recovery[-1]
    decisions = episode.get("decisions") if isinstance(episode, dict) else None
    decision = decisions[-1].get("decision") if isinstance(decisions, list) and decisions else None
    if not isinstance(decision, dict):
        return None
    action = str(decision.get("action") or "")
    if action not in {RecoveryAction.WAIT_RETRY.value, RecoveryAction.SERVICE_WAIT.value}:
        return None
    key = "wait_deadline" if action == RecoveryAction.SERVICE_WAIT.value else "retry_deadline"
    deadline = episode.get(key)
    if not isinstance(deadline, str) or not deadline:
        raise RunnerError("recovery_deadline_missing", "persisted recovery wait has no deadline", details={"run_id": run_id, "action": action})
    try:
        parsed = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RunnerError("recovery_deadline_invalid", "persisted recovery wait deadline is invalid", details={"run_id": run_id, "action": action}) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return action, parsed.astimezone(timezone.utc).isoformat()


def drive(*, brief_file: Path, config_file: Path, control_root: Path, launch_key: str,
          run_id: str | None = None, launch_token: str | None = None) -> dict[str, object]:
    """Run the workflow, waiting for durable retry deadlines and controls."""
    launch_key = _validate_launch_key(launch_key)
    control_root = control_root.expanduser().resolve()
    active_run_id = run_id
    woken_deadlines: set[tuple[str, str]] = set()
    while True:
        result = start(
            brief_file=brief_file, config_file=config_file, control_root=control_root,
            launch_key=launch_key, run_id=active_run_id, launch_token=launch_token,
        )
        run_payload = result.get("run")
        if not isinstance(run_payload, dict) or not isinstance(run_payload.get("run_id"), str):
            raise RunnerError("run_status_missing", "Runner start returned no durable run identity")
        active_run_id = str(run_payload["run_id"])

        while True:
            store = Store.open(control_root, create=False)
            try:
                current = store.find_by_run_id(active_run_id)
                if current is None:
                    raise RunnerError("unknown_run", "recovery wait run disappeared from the control database")
                wait_record = _recovery_wait_record(store=store, run_id=active_run_id)
                if wait_record is None:
                    if current.state in {RecoveryAction.WAIT_RETRY.value, RecoveryAction.SERVICE_WAIT.value}:
                        raise RunnerError("recovery_record_missing", "waiting run has no persisted recovery decision")
                    return {**result, **store.public_status(active_run_id)}
                action, deadline = wait_record
                control = store.control_for_run(active_run_id)
                if control and control.get("requested_state") in {"pause_requested", "cancel_requested"}:
                    requested = str(control["requested_state"])
                    stopped_state = "paused" if requested == "pause_requested" else "cancelled"
                    store.set_run_state(active_run_id, stopped_state)
                    store.append_event(
                        run_id=active_run_id,
                        event_key=f"control:{active_run_id}:{control['generation']}:applied",
                        event_type="control_applied",
                        payload={"requested_state": requested, "generation": control["generation"], "during": "recovery_wait"},
                    )
                    return {**result, **store.public_status(active_run_id)}
                wake_key = (action, deadline)
                remaining = (datetime.fromisoformat(deadline) - datetime.now(timezone.utc)).total_seconds()
                if remaining <= 0:
                    if wake_key in woken_deadlines:
                        return {**result, **store.public_status(active_run_id)}
                    woken_deadlines.add(wake_key)
                    store.append_event(
                        run_id=active_run_id,
                        event_key=f"recovery:{active_run_id}:timer-woke:{action}:{deadline}",
                        event_type="recovery_timer_woke",
                        payload={"action": action, "deadline": deadline},
                    )
                    break
                wait_seconds = min(remaining, 0.25)
            finally:
                store.close()
            time.sleep(wait_seconds)


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
                       worker_id: str, brief_digest: str, persist_result: bool = True,
                       allow_ticket_confirmation: bool = False) -> dict[str, object] | RunRecord:
    directory = _safe_artifact_directory(control_root, config, run.run_id)
    directory.mkdir(parents=True, exist_ok=True)
    # Retain the actual transport result even if semantic validation fails.
    turn_key = hashlib.sha256(result.turn_id.encode("utf-8")).hexdigest()
    if persist_result:
        (directory / f"{step_name}-{turn_key}.json").write_text(
            json.dumps(result.public(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if result.status != "completed" or result.error:
        raise RunnerError(
            "planning_worker_failed",
            "planning requires a successful terminal SDK turn",
            details={"fault_observation": result.fault_observation, "thread_id": result.thread_id, "turn_id": result.turn_id},
        )
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
    if declared["questions"]:
        raise RunnerError("planning_not_ready", "only planned without unanswered questions may advance",
                          details={"outcome": document.get("outcome")})
    if document.get("outcome") != "planned":
        # The to-tickets Skill may use a human-readable confirmation sentence
        # in the outcome field while still returning the complete structured
        # TicketPlan. Normalize that equivalent success form at the Runner
        # boundary; missing tickets remain a hard failure below.
        tickets = document.get("tickets")
        if allow_ticket_confirmation and isinstance(tickets, list) and tickets:
            document["outcome"] = "planned"
        else:
            raise RunnerError("planning_not_ready", "only planned without unanswered questions may advance",
                              details={"outcome": document.get("outcome")})
    return document


def _planning_turn_requires_retry(*, control_root: Path, config: RunnerConfig,
                                  run: RunRecord, thread_id: str, turn_id: str,
                                  brief_digest: str) -> bool:
    """Identify a completed planning turn whose semantic receipt was rejected.

    A process can exit after the SDK turn is durable but before the Runner
    persists its validated SpecPlan. Retry only when the persisted turn is
    provably not a valid planned response; a valid-looking response must not be
    replayed or silently promoted without its normal stage completion path.
    """
    artifact = _safe_artifact_directory(control_root, config, run.run_id) / (
        f"codex_planning-{hashlib.sha256(turn_id.encode('utf-8')).hexdigest()}.json"
    )
    try:
        receipt = load_json(artifact)
        if (receipt.get("status") != "completed"
                or receipt.get("error") is not None
                or receipt.get("thread_id") != thread_id
                or receipt.get("turn_id") != turn_id):
            return False
        document = json.loads(str(receipt.get("final_response") or "null"))
    except (RunnerError, TypeError, json.JSONDecodeError):
        return False
    if not isinstance(document, dict) or document.get("outcome") != "planned":
        return True
    candidate = dict(document)
    candidate["schema_version"] = "spec-runner-spec-plan/v1"
    candidate["requirement_digest"] = brief_digest
    try:
        validate_spec_plan(candidate)
    except RunnerError:
        return True
    return False


def _blocked_planning_retry_thread(*, control_root: Path, config: RunnerConfig,
                                   run: RunRecord, store: Store,
                                   brief_digest: str) -> str | None:
    worker_id = f"codex_sdk:{run.run_id}:codex_planning"
    workers = [
        worker for worker in store.workers_for_run(run.run_id)
        if worker.get("worker_id") == worker_id
        and worker.get("state") in {"running", "failed", "interrupted", "rejected"}
    ]
    if not workers:
        return None
    worker = workers[-1]
    thread_id = str(worker.get("external_thread_id") or "")
    turn_id = str(worker.get("external_turn_id") or "")
    if not thread_id or not turn_id:
        return None
    if not _planning_turn_requires_retry(
        control_root=control_root, config=config, run=run,
        thread_id=thread_id, turn_id=turn_id, brief_digest=brief_digest,
    ):
        return None
    return thread_id


def _persist_implementation_input_gate(
    *, control_root: Path, config: RunnerConfig, run: RunRecord, store: Store,
    result: CodexWorkerResult, brief_digest: str, operation_id: str,
    step_name: str, worker_id: str, spec_key: str,
) -> dict[str, object] | None:
    """Persist an implementation worker's real question as a resumable gate."""
    declared = _declared_model_result(result=result, brief_digest=brief_digest, stage=step_name)
    if declared.get("outcome") != "needs_input":
        return None
    questions = declared.get("questions")
    if not isinstance(questions, list) or not questions:
        raise RunnerError("invalid_worker_questions", "implementation needs_input requires at least one question")
    directory = _safe_artifact_directory(control_root, config, run.run_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "worker-result.json").write_text(
        json.dumps(declared, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    store.complete_codex_stage(
        run.run_id, operation_id, thread_id=result.thread_id, turn_id=result.turn_id,
        state="needs_input", step_name=step_name, worker_id=worker_id,
    )
    return {"state": "needs_input", "spec_key": spec_key, "questions": questions}


def _persist_ticket_plan(*, control_root: Path, config: RunnerConfig,
                         run: RunRecord, store: Store, document: dict[str, object],
                         spec: dict[str, object], base_sha: str, operation_id: str,
                         step_name: str, worker_id: str, result: CodexWorkerResult) -> RunRecord:
    spec_key = str(spec.get("key", ""))
    document.update({
        "schema_version": "spec-runner-ticket-plan/v1", "spec_key": spec_key, "base_sha": base_sha,
        "spec_title": str(spec.get("title") or spec_key), "spec_body": str(spec.get("body") or ""),
    })
    validated = validate_ticket_plan(document, expected_spec_key=spec_key, expected_base_sha=base_sha)
    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    artifact_directory.mkdir(parents=True, exist_ok=True)
    (artifact_directory / f"ticket-plan-{spec_key}.json").write_text(
        json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n"
    )
    source = _ticket_plan_source(artifact_directory=artifact_directory, plan=validated, run_id=run.run_id)
    published = publish_local(read_local(source), control_root / "tracker", operation_id=operation_id)
    tracker_receipts: dict[str, object] = {"local": published}
    external_receipt: tuple[str, dict[str, object]] | None = None
    if config.github_repository is not None:
        tracker_receipts["github"] = _publish_ticket_plan(
            config=config, control_root=control_root, plan=validated,
            operation_id=operation_id, run_id=run.run_id, store=store
        )
        github_receipt = tracker_receipts["github"].get("receipt")
        assert isinstance(github_receipt, dict)
        external_receipt = (operation_id, github_receipt)
    store.write_log(control_root, run.run_id, {
        "event": "ticket_plan_published", "spec_key": spec_key,
        "ticket_count": len(validated["tickets"]), "tracker": tracker_receipts,
    })
    return store.complete_codex_stage(
        run.run_id, operation_id, thread_id=result.thread_id, turn_id=result.turn_id,
        state="tickets_ready", step_name=step_name, worker_id=worker_id,
        external_operation=external_receipt,
    )


def _execute_codex_grill(*, control_root: Path, config: RunnerConfig, brief: str,
                         brief_digest: str, run: RunRecord, store: Store,
                         thread_id: str | None = None) -> RunRecord:
    """Clarify a brief through the current read-only Skill, not a scheduler LLM."""
    step = "codex_grill"
    operation = f"grill:{run.run_id}"
    worker = f"codex_sdk:{run.run_id}:{step}"
    store.begin_stage(run.run_id, step_name=step, operation_id=operation, backend_kind="codex_sdk", worker_id=worker)
    schema = {"type": "object", "properties": {
        "outcome": {"type": "string", "enum": ["planned", "needs_input", "change_request", "failed"]},
        "scope": {"type": "string"},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "acceptance": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}},
        }, "required": ["id", "question", "options"], "additionalProperties": False}},
    }, "required": ["outcome", "scope", "constraints", "acceptance", "questions"], "additionalProperties": False}
    grill_prompt = (
        "Clarify the supplied requirement into scope, constraints and observable acceptance. "
        "Do not invent missing business facts or permissions. Return needs_input with stable question IDs "
        "if a necessary fact is missing; otherwise return planned. Do not publish or change files.\n\n" + brief
    )
    takeover = _takeover_context(control_root=control_root, config=config, run=run)
    if takeover is not None:
        grill_prompt += (
            "\n\nTakeover continuation context is evidence only. Preserve its authorized scope and remaining frontier; "
            "do not treat historical claims as completed work:\n" +
            json.dumps(takeover, ensure_ascii=False, sort_keys=True)
        )
    result = _run_worker(adapter=CodexAdapter(), phase="grill", config=config,
        prompt=grill_prompt,
        trusted={"stage": step, "brief_digest": brief_digest, "answers": store.answers_for_run(run.run_id)},
        repository_path=config.repository_path, model=config.model_name, effort=config.effort,
        thread_id=thread_id, schema=schema,
        control_state=lambda: _read_control_state(control_root=control_root, run_id=run.run_id),
        on_turn_started=lambda thread_id, turn_id: _record_codex_turn_started(store, run_id=run.run_id,
            operation_id=operation, step_name=step, worker_id=worker, thread_id=thread_id, turn_id=turn_id))
    document = _planning_response(result=result, control_root=control_root, config=config, run=run,
        store=store, operation_id=operation, step_name=step, worker_id=worker, brief_digest=brief_digest)
    if isinstance(document, RunRecord):
        return document
    if not isinstance(document.get("scope"), str) or not document["scope"].strip():
        raise RunnerError("invalid_grill_handoff", "Grill must produce a non-empty scope")
    for field in ("constraints", "acceptance"):
        values = document.get(field)
        if not isinstance(values, list) or (field == "acceptance" and not values) or any(not isinstance(value, str) or not value.strip() for value in values):
            raise RunnerError("invalid_grill_handoff", f"Grill {field} must contain valid text")
    document.update(schema_version="spec-runner-grill-handoff/v1", requirement_digest=brief_digest,
                    thread_id=result.thread_id, turn_id=result.turn_id)
    directory = _safe_artifact_directory(control_root, config, run.run_id)
    (directory / "grill-handoff.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    archive = CodexAdapter().archive_and_readback(thread_id=result.thread_id, repository_path=config.repository_path)
    store.append_event(run_id=run.run_id, event_key=f"grill:{run.run_id}:archived", event_type="cleanup_readback", payload=archive)
    return store.complete_codex_stage(run.run_id, operation, thread_id=result.thread_id,
        turn_id=result.turn_id, state="clarified", step_name=step, worker_id=worker)


def _execute_codex_planning(
    *, control_root: Path, config: RunnerConfig, brief: str, brief_digest: str, run: RunRecord, store: Store,
    thread_id: str | None = None,
) -> RunRecord:
    """Run the production planning boundary and persist a validated SpecPlan."""
    grill_path = _safe_artifact_directory(control_root, config, run.run_id) / "grill-handoff.json"
    handoff = None
    if config.workflow_mode == "production":
        if not grill_path.exists():
            clarified = _execute_codex_grill(control_root=control_root, config=config, brief=brief,
                brief_digest=brief_digest, run=run, store=store)
            if clarified.state != "clarified":
                return clarified
        handoff = load_json(grill_path)
        if handoff.get("requirement_digest") != brief_digest:
            raise RunnerError("stale_grill_handoff", "Grill handoff belongs to another requirement revision")
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
        "questions": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}}, "required": ["id", "question", "options"], "additionalProperties": False}},
    }, "required": ["outcome", "requirements", "specs", "questions"], "additionalProperties": False}
    answers = store.answers_for_run(run.run_id)
    prompt = (
        "Produce a SpecPlan for this requirement. Do not publish issues, create branches, or modify files. "
        "If a user decision is required, return outcome needs_input and questions; otherwise return outcome "
        "planned with complete requirements and dependency-ordered specs. The requirements field is the exact "
        "canonical list of requirement strings, and every spec's covers list must contain only exact strings "
        "copied from that requirements list; do not put acceptance prose or paraphrases in covers. Every "
        "requirement must appear in at least one covers list, including global structural or scope "
        "requirements; do not treat those requirements as implicitly covered by the shape of the plan. "
        "Before returning, compare every covers entry character-for-character with the requirements list; "
        "a covers entry that describes tests or acceptance behavior is invalid unless that exact sentence is "
        "also present in requirements.\n\n" + brief
    )
    if handoff:
        prompt += "\n\nValidated Grill handoff:\n" + json.dumps(handoff, ensure_ascii=False, sort_keys=True)
    if thread_id is not None:
        prompt += (
            "\n\nThis is a retry after the previous planning receipt was rejected. "
            "Rebuild the covers arrays from exact requirement strings and do not copy acceptance prose into them."
        )
    if answers:
        prompt += "\n\nRunner-recorded business answers (use as facts, do not ask again):\n" + json.dumps(answers, ensure_ascii=False, sort_keys=True)
    takeover = _takeover_context(control_root=control_root, config=config, run=run)
    if takeover is not None:
        prompt += (
            "\n\nTakeover continuation context is evidence only; do not promote historical claims to completed work. "
            "Use its frontier to select the minimum safe next stage, preserve adopted artifacts, and reverify "
            "claimed completion against current receipts:\n" +
            json.dumps(takeover, ensure_ascii=False, sort_keys=True)
        )
    result = _run_worker(
        adapter=CodexAdapter(), phase="to-spec", config=config, prompt=prompt,
        trusted={"brief_digest": brief_digest, "stage": step_name, "repository_scope": os.fspath(config.repository_path)},
        repository_path=config.repository_path, model=config.model_name, effort=config.effort, thread_id=thread_id,
        control_state=lambda: _read_control_state(control_root=control_root, run_id=run.run_id),
        schema=schema,
        on_turn_started=lambda thread_id, turn_id: _record_codex_turn_started(store, run_id=run.run_id, operation_id=operation_id, step_name=step_name, worker_id=worker_id, thread_id=thread_id, turn_id=turn_id),
    )
    document = _planning_response(result=result, control_root=control_root, config=config, run=run,
        store=store, operation_id=operation_id, step_name=step_name, worker_id=worker_id,
        brief_digest=brief_digest)
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


def _ticket_plan_github_draft(*, plan: dict[str, object], run_id: str) -> dict[str, object]:
    source = plan
    spec_key = str(source["spec_key"])
    spec_title = str(source.get("spec_title") or spec_key)
    spec_body = str(source.get("spec_body") or "")
    specs = [{"key": spec_key, "title": spec_title,
              "body": f"spec-runner-run:{run_id}\n\n{spec_body}"}]
    for ticket in source["tickets"]:  # type: ignore[index]
        ticket_key = str(ticket["key"])
        blocked = list(ticket.get("blocked_by", []))
        relation = f"Parent SPEC: {spec_key}"
        if blocked:
            relation += "\nBlocked by: " + ", ".join(str(item) for item in blocked)
        specs.append({"key": ticket_key, "title": str(ticket.get("title") or ticket_key),
                      "parent": spec_key, "blocked_by": blocked,
                      "body": f"spec-runner-run:{run_id}\n{relation}\n\n{ticket['body']}"})
    # The first item is the umbrella SPEC; remaining items are its tickets.
    return {"umbrella": specs[0], "specs": specs[1:]}


def _publish_ticket_plan(*, config: RunnerConfig, control_root: Path, plan: dict[str, object],
                         operation_id: str, run_id: str, store: Store) -> dict[str, object]:
    """Publish the validated plan through the configured tracker boundary.

    GitHub publication uses per-object durable operations. Native relations are
    written only after each issue has been independently read back, then their
    own operation receipts are committed after relation readback.
    """
    draft = _ticket_plan_github_draft(plan=plan, run_id=run_id)
    if not config.github_repository or not config.github_receipt_root:
        raise RunnerError("github_config_incomplete", "GitHub tracker publication requires repository and receipt root")
    draft_digest = hashlib.sha256(json.dumps(draft, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    store.prepare_external_operation(operation_id=operation_id, run_id=run_id,
        operation_kind="github_issue_publication", repository=config.github_repository,
        input_digest=draft_digest)
    def prepare_issue_operation(*, operation_id: str, operation_kind: str, repository: str,
                                input_digest: str) -> dict[str, object]:
        return store.prepare_external_operation(operation_id=operation_id, run_id=run_id,
            operation_kind=operation_kind, repository=repository, input_digest=input_digest)

    def complete_issue_operation(*, operation_id: str, receipt: dict[str, object]) -> None:
        store.complete_external_operation(operation_id=operation_id, receipt=receipt)

    result = GitHubTracker(timeout_seconds=config.github_timeout_seconds).publish_draft(repository=config.github_repository, draft=draft,
        operation_id=operation_id, receipt_root=config.github_receipt_root,
        relation_mode="native", operation_intent=prepare_issue_operation,
        operation_completed=complete_issue_operation)
    receipt = result.get("receipt")
    if not isinstance(receipt, dict) or receipt.get("complete") is not True:
        raise RunnerError("github_publish_unconfirmed", "GitHub publication did not return a complete operation receipt")
    return result


def _close_published_ticket_plan(*, config: RunnerConfig, plan: dict[str, object],
                                 run_id: str, store: Store) -> dict[str, object] | None:
    if config.github_repository is None:
        return None
    spec_key = str(plan.get("spec_key") or "")
    if not spec_key or not config.github_receipt_root:
        raise RunnerError("github_close_config_incomplete", "closing published tickets requires repository, SPEC identity and receipt root")
    publication_operation = f"tickets:{run_id}:{spec_key}"
    publication = store.external_operation(publication_operation)
    if (not isinstance(publication, dict) or publication.get("state") != "completed"
            or publication.get("operation_kind") != "github_issue_publication"
            or publication.get("repository") != config.github_repository
            or not isinstance(publication.get("receipt"), dict)
            or publication["receipt"].get("complete") is not True
            or publication["receipt"].get("repository") != config.github_repository):
        raise RunnerError("github_close_evidence_missing", "SPEC has no completed GitHub publication receipt")

    def prepare(*, operation_id: str, operation_kind: str, repository: str,
                input_digest: str) -> dict[str, object]:
        return store.prepare_external_operation(operation_id=operation_id, run_id=run_id,
            operation_kind=operation_kind, repository=repository, input_digest=input_digest)

    def complete(*, operation_id: str, receipt: dict[str, object]) -> None:
        store.complete_external_operation(operation_id=operation_id, receipt=receipt)

    return GitHubTracker(timeout_seconds=config.github_timeout_seconds).close_published(repository=config.github_repository,
        draft=_ticket_plan_github_draft(plan=plan, run_id=run_id),
        operation_id=publication_operation, publication_receipt=publication["receipt"],
        relation_mode="native", operation_intent=prepare, operation_completed=complete)


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
    # SPEC-level dependencies are consumed by the production queue before this
    # stage starts. TicketPlan.blocked_by is a separate namespace for
    # dependencies between tickets within the current SPEC.
    ticket_spec = dict(spec)
    ticket_spec["blocked_by"] = []
    base_sha = git_sha(config.repository_path, config.target_ref, timeout_seconds=config.git_timeout_seconds)
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
            "questions": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}}, "required": ["id", "question", "options"], "additionalProperties": False}},
        },
        "required": ["outcome", "tickets", "questions"], "additionalProperties": False,
    }
    prompt = (
        "Produce a TicketPlan for exactly this SPEC. Do not publish issues, create branches, or modify files. "
        "Return needs_input/questions when a real requirement fact is missing; otherwise return planned with concrete "
        "tickets, dependency keys, and acceptance IDs. Ticket keys must be unique and must not equal the parent "
        "spec_key; use a child key such as <spec_key>.1. The SPEC is already dependency-ready; its blocked_by "
        "metadata belongs to the production queue and must not be copied into ticket blocked_by. Only reference "
        "ticket keys from this TicketPlan in ticket blocked_by.\n\n" + json.dumps(ticket_spec, ensure_ascii=False, sort_keys=True)
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
        store=store, operation_id=operation_id, step_name=step_name, worker_id=worker_id,
        brief_digest=brief_digest, allow_ticket_confirmation=True)
    if isinstance(document, RunRecord):
        return document
    return _persist_ticket_plan(
        control_root=control_root, config=config,
        run=run, store=store, document=document, spec=spec, base_sha=base_sha,
        operation_id=operation_id, step_name=step_name, worker_id=worker_id, result=result,
    )


_GIT_COMMAND_TIMEOUT_SECONDS = 120


def _git_checked(repository: Path, *args: str, timeout_seconds: float = _GIT_COMMAND_TIMEOUT_SECONDS) -> str:
    try:
        result = subprocess.run(
            ["git", "-c", "core.longpaths=true", "-C", os.fspath(repository), *args],
            check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunnerError(
            "implementation_git_timeout",
            "Runner Git reconciliation exceeded its bounded timeout; reconcile before retry",
            details={"args": list(args), "timeout_seconds": timeout_seconds},
        ) from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RunnerError("implementation_git_failed", "Runner could not reconcile the implementation workspace", details={"args": list(args)}) from exc
    return result.stdout.strip()


def _git_binary(repository: Path, *args: str, timeout_seconds: float = _GIT_COMMAND_TIMEOUT_SECONDS) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-c", "core.longpaths=true", "-C", os.fspath(repository), *args],
            check=True, capture_output=True, timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunnerError(
            "implementation_git_timeout",
            "Runner Git reconciliation exceeded its bounded timeout; reconcile before retry",
            details={"args": list(args), "timeout_seconds": timeout_seconds},
        ) from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RunnerError("implementation_git_failed", "Runner could not reconcile the implementation workspace", details={"args": list(args)}) from exc
    return result.stdout


def _reconcile_github_base(*, repository: Path, target_ref: str, base: str,
                           git_timeout_seconds: float = _GIT_COMMAND_TIMEOUT_SECONDS) -> dict[str, object]:
    """Advance the local delivery base to the exact remote merge result.

    GitHub delivery changes the provider first. The next SPEC must prepare its
    worktree from that provider state, while a concurrent local change must
    fail closed instead of silently basing work on an older ref.
    """
    target_branch = target_ref.removeprefix("refs/heads/")
    remote_ref = f"refs/remotes/origin/{base}"
    _git_checked(
        repository, "fetch", "origin", f"refs/heads/{base}:{remote_ref}",
        timeout_seconds=git_timeout_seconds,
    )
    remote_sha = git_sha(repository, remote_ref, timeout_seconds=git_timeout_seconds)
    local_sha = git_sha(repository, target_ref, timeout_seconds=git_timeout_seconds)
    if local_sha == remote_sha:
        return {"target_ref": target_ref, "previous_sha": local_sha, "synced_sha": remote_sha, "outcome": "already_current"}
    if _git_checked(repository, "status", "--porcelain",
                    timeout_seconds=git_timeout_seconds):
        raise RunnerError("github_base_sync_dirty", "cannot advance the local base with uncommitted changes")
    current_branch = _git_checked(
        repository, "branch", "--show-current",
        timeout_seconds=git_timeout_seconds,
    )
    if current_branch == target_branch:
        _git_checked(repository, "merge", "--ff-only", remote_ref,
                     timeout_seconds=git_timeout_seconds)
    else:
        _git_checked(
            repository, "update-ref", target_ref, remote_sha, local_sha,
            timeout_seconds=git_timeout_seconds,
        )
    if git_sha(repository, target_ref, timeout_seconds=git_timeout_seconds) != remote_sha:
        raise RunnerError("github_base_sync_unconfirmed", "local base did not reach the provider merge revision")
    return {"target_ref": target_ref, "previous_sha": local_sha, "synced_sha": remote_sha, "outcome": "fast_forwarded"}


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
    try:
        validated = independent_review(review_result, implementation_thread=implementation_thread, candidate_sha=candidate_sha, acceptance_version=str(ticket_plan["digest"]))
    except RunnerError as exc:
        # A completed SDK turn with an invalid receipt is terminal external
        # work, but it cannot advance delivery. Persist that distinction so a
        # later drive can archive/read back the reviewer and retry review.
        store.reject_codex_stage(
            run.run_id,
            review_operation,
            thread_id=review_result.thread_id,
            turn_id=review_result.turn_id,
            step_name=review_step,
            worker_id=review_worker,
            code=exc.code,
        )
        raise
    store.complete_codex_stage(run.run_id, review_operation, thread_id=review_result.thread_id, turn_id=review_result.turn_id, state="reviewed", step_name=review_step, worker_id=review_worker)
    (artifact_directory / f"review-{spec_key}-{candidate_sha[:12]}.json").write_text(json.dumps({**validated, "worker": review_result.public()}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    CodexAdapter().archive_and_readback(thread_id=review_result.thread_id, repository_path=workspace)
    return validated, review_result


def _repair_candidate(*, control_root: Path, config: RunnerConfig, brief_digest: str, run: RunRecord,
                      store: Store, ticket_plan: dict[str, object], workspace: Path,
                      findings: list[object], implementation_thread: str, artifact_directory: Path,
                      start_new_thread: bool = False) -> tuple[str, dict[str, object]]:
    spec_key = str(ticket_plan["spec_key"])
    operation = f"repair:{run.run_id}:{spec_key}:{len(store.operations_for_run(run.run_id))}"
    step = "codex_repair"
    worker = f"codex_sdk:{run.run_id}:{step}:{spec_key}"
    write_root = _implementation_write_root(workspace=workspace, config=config, create=False)
    store.begin_stage(run.run_id, step_name=step, operation_id=operation, backend_kind="codex_sdk", worker_id=worker)
    adapter = CodexAdapter()
    if not start_new_thread:
        # A prior candidate/review cycle may have archived the implementation
        # thread. Repair must resume that same formal owner, so explicitly
        # restore it before issuing the next SDK turn.  Lightweight adapter
        # doubles used by the deterministic contract tests may not implement
        # the optional archive probe; they still exercise the real receipt and
        # candidate gates below.
        unarchive = getattr(adapter, "unarchive_and_readback", None)
        if callable(unarchive):
            unarchive(thread_id=implementation_thread, repository_path=workspace)
    result = _run_worker(
        adapter=adapter, phase="implement", config=config,
        prompt=("Fix only these independent review findings in the existing assigned write-scope directory. Preserve all acceptance "
                "requirements and do not publish, merge, or edit outside that directory. Do not run repository-wide test discovery, "
                "invoke pytest from a parent project, start another Runner, or operate on any external repository. Runner will execute "
                "the exact trusted acceptance checks after this turn. "
                "The structured artifacts array must contain only existing write-scope-relative file paths; "
                "never include test summaries, prose, or other non-path text in artifacts.\n\n"
                + json.dumps(findings, ensure_ascii=False, sort_keys=True)),
        model=config.model_name, effort=config.effort,
        thread_id=None if start_new_thread else implementation_thread,
        repository_path=write_root,
        trusted={"brief_digest": brief_digest, "stage": step, "spec_key": spec_key,
                 "workspace": os.fspath(workspace), "write_scope": os.fspath(write_root),
                 "review_findings": findings},
        on_turn_started=lambda thread_id, turn_id: _record_codex_turn_started(store, run_id=run.run_id, operation_id=operation, step_name=step, worker_id=worker, thread_id=thread_id, turn_id=turn_id),
        control_state=lambda: _read_control_state(control_root=control_root, run_id=run.run_id),
        schema=IMPLEMENTATION_SCHEMA,
    )
    turn_key = hashlib.sha256(result.turn_id.encode("utf-8")).hexdigest()
    (artifact_directory / f"repair-worker-{spec_key}-{turn_key}.json").write_text(
        json.dumps(result.public(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not start_new_thread and result.thread_id != implementation_thread:
        raise RunnerError("repair_owner_changed", "repair must resume the original implementation thread")
    return _finish_repair_candidate_result(
        control_root=control_root, config=config, run=run, store=store,
        ticket_plan=ticket_plan, workspace=workspace, result=result,
        operation=operation, worker=worker, artifact_directory=artifact_directory,
        # A repair worker may report an environmental or unrelated test
        # blocker after making real workspace changes. Let the trusted
        # candidate acceptance gate decide whether that candidate is
        # deliverable; do not discard the repair before verification.
        allow_blocked=True,
    )


def _finish_repair_candidate_result(*, control_root: Path, config: RunnerConfig, run: RunRecord,
                                    store: Store, ticket_plan: dict[str, object], workspace: Path,
                                    result: CodexWorkerResult, operation: str, worker: str,
                                    artifact_directory: Path,
                                    adopt_existing: bool = False,
                                    allow_blocked: bool = False) -> tuple[str, dict[str, object]]:
    """Commit and verify one already persisted repair result exactly once."""
    spec_key = str(ticket_plan["spec_key"])
    step = "codex_repair"
    write_root = _implementation_write_root(workspace=workspace, config=config, create=False)
    base_sha = str(ticket_plan.get("base_sha") or "")
    if not base_sha:
        base_sha = git_sha(config.repository_path, config.target_ref, timeout_seconds=config.git_timeout_seconds)
    try:
        # A recovered repair may already have committed its candidate before
        # the Runner process exited.  In that case the workspace is clean, so
        # validate the completed-with-blockers receipt directly when adopting
        # the existing commit.  Fresh repairs still pass through the guarded
        # path below, which requires observable workspace progress.
        implementation_artifacts(
            result,
            workspace,
            allow_blocked=allow_blocked and adopt_existing,
            artifact_root=write_root,
        )
        workspace_status = _git_checked(workspace, "status", "--porcelain", timeout_seconds=config.git_timeout_seconds)
    except RunnerError as exc:
        # A blocked worker is admissible only when it actually produced a
        # candidate that the trusted checks can evaluate.  Keep all other
        # invalid receipts on the early semantic gate so they cannot even
        # trigger a workspace inspection or write.
        try:
            declared = json.loads(result.final_response or "null")
        except json.JSONDecodeError:
            declared = None
        if not (allow_blocked and exc.code == "implementation_not_ready"
                and isinstance(declared, dict)
                and declared.get("outcome") in {"blocked", "completed"}
                and isinstance(declared.get("blockers"), list)
                and bool(declared.get("blockers"))):
            raise
        workspace_status = _git_checked(workspace, "status", "--porcelain", timeout_seconds=config.git_timeout_seconds)
        if not workspace_status:
            raise exc
        implementation_artifacts(result, workspace, allow_blocked=True, artifact_root=write_root)
    validate_candidate_write_scope(
        workspace=workspace, base_sha=base_sha, allowed_paths=config.acceptance_paths,
        git_timeout_seconds=config.git_timeout_seconds,
    )
    if workspace_status:
        _git_checked(workspace, "add", "--all", timeout_seconds=config.git_timeout_seconds)
        _git_checked(workspace, "-c", "user.name=Spec Runner", "-c", "user.email=spec-runner@localhost", "commit", "-m", f"spec-runner: repair {spec_key}", timeout_seconds=config.git_timeout_seconds)
    elif not adopt_existing:
        raise RunnerError("repair_no_progress", "repair worker produced no candidate changes")
    else:
        candidate_sha = git_sha(workspace, timeout_seconds=config.git_timeout_seconds)
        if candidate_sha == str(ticket_plan.get("base_sha") or ""):
            raise RunnerError("repair_no_progress", "repair worker produced no candidate changes")
    candidate_sha = git_sha(workspace, timeout_seconds=config.git_timeout_seconds)
    candidate_receipt = verify_candidate(workspace=workspace, candidate_sha=candidate_sha,
        acceptance_version=str(ticket_plan["digest"]), checks=list(config.acceptance_checks),
        acceptance=list(config.acceptance_ids), base_sha=base_sha, allowed_paths=config.acceptance_paths,
        git_timeout_seconds=config.git_timeout_seconds)
    store.complete_codex_stage(run.run_id, operation, thread_id=result.thread_id, turn_id=result.turn_id, state="verified_candidate", step_name=step, worker_id=worker)
    # A repaired candidate replaces the previous candidate for the SPEC. Keep
    # the canonical receipt aligned with the verified workspace so GitHub
    # recovery cannot resume against a stale candidate.
    _write_json_atomic(artifact_directory / f"candidate-{spec_key}.json", candidate_receipt)
    (artifact_directory / f"repair-{spec_key}-{candidate_sha[:12]}.json").write_text(json.dumps({"candidate": candidate_receipt, "worker": result.public()}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return candidate_sha, candidate_receipt


def _candidate_verification_finding(exc: RunnerError) -> dict[str, object]:
    """Convert a failed trusted check into bounded repair input."""
    details = exc.details if isinstance(exc.details, dict) else {}
    checks = details.get("checks")
    failed_checks = [
        {
            "command": item.get("command"),
            "exit_code": item.get("exit_code"),
            "timed_out": item.get("timed_out"),
            "stdout_digest": item.get("stdout_digest"),
            "stderr_digest": item.get("stderr_digest"),
            "stdout_tail": item.get("stdout_tail"),
            "stderr_tail": item.get("stderr_tail"),
        }
        for item in checks
        if isinstance(item, dict) and not item.get("passed")
    ] if isinstance(checks, list) else []
    return {
        "severity": "high",
        "status": "open",
        "description": "Trusted candidate verification failed. Run the exact acceptance command(s), fix the product regression, and preserve the SPEC contract.",
        "verification_error": exc.code,
        "checks": failed_checks,
    }


def _retry_repair_after_candidate_failure(*, control_root: Path, config: RunnerConfig,
                                         brief_digest: str, run: RunRecord, store: Store,
                                         ticket_plan: dict[str, object], workspace: Path,
                                         findings: list[object], implementation_thread: str,
                                         artifact_directory: Path, failed_operation: str,
                                         failed_result: CodexWorkerResult,
                                         failure: RunnerError) -> tuple[str, dict[str, object]]:
    """Give one failed candidate check back to the repair worker.

    The failed candidate is already committed in the managed workspace. The
    next turn therefore edits that exact candidate and remains on the formal
    repair thread recorded by the Runner.
    """
    worker = f"codex_sdk:{run.run_id}:codex_repair:{ticket_plan['spec_key']}"
    store.complete_codex_stage(
        run.run_id, failed_operation, thread_id=failed_result.thread_id,
        turn_id=failed_result.turn_id, state="candidate_verification_failed",
        step_name="codex_repair", worker_id=worker,
    )
    worker_rows = [
        item for item in store.workers_for_run(run.run_id)
        if str(item.get("worker_id") or "") == worker
    ]
    repair_thread = str(worker_rows[-1].get("external_thread_id") or "") if worker_rows else ""
    if not repair_thread:
        raise RunnerError("recovery_blocked", "candidate verification failed but the repair thread identity was lost")
    repair_findings = [*findings, _candidate_verification_finding(failure)]
    candidate_sha, receipt = _repair_candidate(
        control_root=control_root, config=config, brief_digest=brief_digest,
        run=run, store=store, ticket_plan=ticket_plan, workspace=workspace,
        findings=repair_findings, implementation_thread=repair_thread,
        artifact_directory=artifact_directory,
    )
    store.append_event(
        run_id=run.run_id,
        event_key=f"recovery:{run.run_id}:candidate-verification-repaired:{candidate_sha}",
        event_type="candidate_verification_failure_repaired",
        payload={"spec_key": str(ticket_plan["spec_key"]), "failed_operation": failed_operation,
                 "repair_thread_id": repair_thread, "candidate_sha": candidate_sha},
    )
    return candidate_sha, receipt


def _resume_after_repair_candidate(*, control_root: Path, config: RunnerConfig,
                                   brief_digest: str, run: RunRecord, store: Store,
                                   spec_key: str) -> dict[str, object]:
    """Return to the completed implementation turn after a repair candidate."""
    implementation_prefix = f"codex_sdk:{run.run_id}:codex_implementation:{spec_key}"
    implementation_workers = [
        item for item in store.workers_for_run(run.run_id)
        if str(item.get("worker_id") or "") == implementation_prefix
    ]
    implementation_worker = implementation_workers[-1] if implementation_workers else None
    if implementation_worker is None:
        raise RunnerError("recovery_blocked", "repair completed but the implementation worker identity is missing")
    implementation_thread = str(implementation_worker.get("external_thread_id") or "")
    implementation_turn = str(implementation_worker.get("external_turn_id") or "")
    if not implementation_thread or not implementation_turn:
        raise RunnerError("recovery_blocked", "repair completed but the implementation turn identity is missing")
    return _reconcile_completed_implementation_turn(
        control_root=control_root, config=config, run=run,
        brief_digest=brief_digest, store=store, worker=implementation_worker,
        thread_id=implementation_thread, turn_id=implementation_turn,
    )


def _execute_github_delivery(*, control_root: Path, config: RunnerConfig, run: RunRecord,
                             spec_key: str, candidate_sha: str, branch: str,
                             candidate_receipt: dict[str, object], review: dict[str, object],
                             push: bool = True) -> dict[str, object]:
    """Publish one already verified candidate through the explicit GitHub gate."""
    if not config.github_repository or not config.github_required_checks or not config.github_receipt_root or not config.github_base:
        raise RunnerError("github_config_incomplete", "GitHub delivery requires repository, base, receipt root and required checks")
    base = config.github_base.removeprefix("refs/heads/")
    repository = config.github_repository
    # The branch push is a Runner side effect, after all local candidate gates.
    if push:
        _git_checked(
            config.repository_path, "push", "--set-upstream", "origin", branch,
            timeout_seconds=config.git_timeout_seconds,
        )
    body = json.dumps({"run_id": run.run_id, "spec_key": spec_key, "candidate": candidate_receipt,
                       "review": review}, ensure_ascii=False, sort_keys=True)
    operation = f"github:{run.run_id}:{spec_key}:{candidate_sha}"
    delivery = GitHubDelivery(timeout_seconds=config.github_timeout_seconds)
    pr_result = delivery.create_or_adopt_pr(repository=repository, head=branch, base=base,
        candidate_sha=candidate_sha, body=body, operation_id=operation,
        receipt_root=config.github_receipt_root)
    pr_receipt = pr_result.get("receipt")
    if not isinstance(pr_receipt, dict) or not isinstance(pr_receipt.get("number"), int):
        raise RunnerError("github_pr_unconfirmed", "GitHub PR receipt lacks a confirmed number")
    checks = delivery.checks(repository=repository, candidate_sha=candidate_sha,
        required=list(config.github_required_checks))
    if not checks["ready"]:
        return {"state": "waiting_ci", "spec_key": spec_key, "branch": branch, "pr": pr_receipt, "checks": checks,
                "candidate": candidate_receipt, "review": review}
    if not config.github_merge_authorized:
        raise RunnerError("github_merge_not_authorized", "GitHub checks passed but merge authorization is not configured")
    merged = delivery.merge(repository=repository, number=int(pr_receipt["number"]),
        expected_head=candidate_sha, expected_base=base,
        candidate_receipt=candidate_receipt, review=review, checks=checks, allow=True)
    merged["base_sync"] = _reconcile_github_base(repository=config.repository_path,
                                                  target_ref=config.target_ref, base=base)
    return {"state": "github_completed", "spec_key": spec_key, "branch": branch, "pr": pr_receipt, "checks": checks,
            "merge": merged, "candidate": candidate_receipt, "review": review}


def _definitive_failed_github_checks(*, checks: object, candidate_sha: str) -> bool:
    """Recognize a terminal failure for the exact candidate under review."""
    if not isinstance(checks, dict) or checks.get("candidate_sha") != candidate_sha:
        return False
    if checks.get("ready") is not False:
        return False
    required = checks.get("required")
    failed = checks.get("failed")
    states = checks.get("states")
    if (not isinstance(required, list) or not required
            or any(not isinstance(item, str) or not item for item in required)
            or not isinstance(failed, list) or not failed
            or any(not isinstance(item, str) or item not in required for item in failed)
            or not isinstance(states, dict)):
        return False
    for key in ("missing", "wrong_sha", "pending", "unknown"):
        values = checks.get(key)
        if not isinstance(values, list) or values:
            return False
    for name in failed:
        state = states.get(name)
        if not isinstance(state, dict) or state.get("sha") != candidate_sha:
            return False
        terminal_failure = (
            state.get("source") == "check_run"
            and state.get("status") == "completed"
            and state.get("conclusion") not in {None, "success", "neutral", "skipped"}
        ) or (
            state.get("source") == "status_context"
            and state.get("status") in {"error", "failure"}
        )
        if not terminal_failure:
            return False
    return True


def _recover_failed_github_candidate(*, control_root: Path, config: RunnerConfig, run: RunRecord,
                                     store: Store, spec_key: str, candidate: dict[str, object],
                                     review: dict[str, object], github: dict[str, object],
                                     failed_checks: dict[str, object], manifest_path: Path,
                                     manifest: dict[str, object]) -> dict[str, object]:
    """Rebase a failed GitHub candidate and restart the guarded delivery gate.

    The failed candidate and PR remain immutable evidence. A new managed
    workspace, branch, candidate receipt, review receipt, and GitHub operation
    are created for the current target ref.
    """
    old_candidate_sha = candidate.get("candidate_sha")
    old_branch = manifest.get("branch")
    old_base_sha = manifest.get("base_sha")
    if (not isinstance(old_candidate_sha, str) or len(old_candidate_sha) != 40
            or not isinstance(old_branch, str) or not old_branch
            or not isinstance(old_base_sha, str) or not old_base_sha):
        raise RunnerError("github_recovery_evidence_invalid", "failed GitHub candidate lacks a complete workspace identity")
    if not _definitive_failed_github_checks(checks=failed_checks, candidate_sha=old_candidate_sha):
        raise RunnerError("github_recovery_evidence_invalid", "failed GitHub checks are not definitive for the candidate")
    github_candidate = github.get("candidate")
    if (candidate.get("outcome") != "verified"
            or not isinstance(github_candidate, dict)
            or candidate.get("acceptance_version") != github_candidate.get("acceptance_version")
            or review.get("approved") is not True
            or review.get("candidate_sha") != old_candidate_sha):
        raise RunnerError("github_recovery_evidence_invalid", "failed GitHub candidate or review receipt is inconsistent")
    artifact = _safe_artifact_directory(control_root, config, run.run_id)
    artifact.mkdir(parents=True, exist_ok=True)
    old_short = old_candidate_sha[:12]
    failed_path = artifact / f"github-failed-{spec_key}-{old_short}.json"
    historical_candidate_path = artifact / f"candidate-{spec_key}-{old_short}.json"
    if not historical_candidate_path.is_file():
        _write_json_atomic(historical_candidate_path, candidate)
    failed_record = {
        "schema_version": "spec-runner-github-failed-delivery/v1",
        "run_id": run.run_id,
        "spec_key": spec_key,
        "candidate": candidate,
        "review": review,
        "checks": failed_checks,
        "delivery": github,
        "pr": github.get("pr"),
        "reason": "required_check_failed",
    }
    if not failed_path.is_file():
        _write_json_atomic(failed_path, failed_record)
    store.append_event(
        run_id=run.run_id,
        event_key=f"github-recovery:{run.run_id}:{spec_key}:{old_candidate_sha}:intent",
        event_type="github_candidate_recovery_intent",
        payload={"spec_key": spec_key, "candidate_sha": old_candidate_sha,
                 "failed_receipt": os.fspath(failed_path)},
    )

    workspace_root = (control_root / "delivery-workspaces").resolve()
    old_workspace = Path(str(manifest.get("workspace", ""))).resolve()
    if (workspace_root not in old_workspace.parents or not old_workspace.is_dir()
            or Path(str(manifest.get("repository", ""))).resolve() != config.repository_path.resolve()
            or manifest_path.resolve().parent != workspace_root):
        raise RunnerError("github_recovery_evidence_invalid", "failed GitHub workspace manifest is outside the managed repository scope")
    if (git_sha(old_workspace, timeout_seconds=config.git_timeout_seconds) != old_candidate_sha
            or git_sha(config.repository_path, old_branch, timeout_seconds=config.git_timeout_seconds) != old_candidate_sha):
        raise RunnerError("github_recovery_evidence_invalid", "failed GitHub workspace no longer names its recorded candidate")
    if _git_checked(old_workspace, "status", "--porcelain", timeout_seconds=config.git_timeout_seconds):
        raise RunnerError("github_recovery_evidence_invalid", "failed GitHub workspace is dirty")

    base_sync = _reconcile_github_base(
        repository=config.repository_path, target_ref=config.target_ref,
        base=config.github_base.removeprefix("refs/heads/"),
        git_timeout_seconds=config.git_timeout_seconds,
    )
    recovery_suffix = f"-recovery-{old_short}"
    recovery_branch = f"{old_branch}{recovery_suffix}"
    recovery_workspace = prepare_workspace(
        repository=config.repository_path,
        workspace_root=workspace_root,
        run_id=run.run_id,
        spec_key=spec_key,
        base_ref=config.target_ref,
        branch=recovery_branch,
        workspace_suffix=recovery_suffix,
        git_timeout_seconds=config.git_timeout_seconds,
    )
    recovery_path = artifact / f"candidate-{spec_key}-recovery-{old_short}.json"
    recovery_workspace_path = Path(str(recovery_workspace["workspace"]))
    if recovery_path.is_file():
        recovery_candidate = load_json(recovery_path)
        recovery_sha = recovery_candidate.get("candidate_sha")
        if (recovery_candidate.get("outcome") != "verified"
                or not isinstance(recovery_sha, str) or len(recovery_sha) != 40
                or git_sha(recovery_workspace_path, timeout_seconds=config.git_timeout_seconds) != recovery_sha
                or _git_checked(recovery_workspace_path, "status", "--porcelain", timeout_seconds=config.git_timeout_seconds)):
            raise RunnerError("github_recovery_evidence_invalid", "persisted rebased candidate does not match its workspace")
    else:
        patch_path = artifact / f"github-recovery-{spec_key}-{old_short}.patch"
        if not patch_path.is_file():
            patch = _git_binary(config.repository_path, "diff", "--binary", f"{old_base_sha}..{old_candidate_sha}", timeout_seconds=config.git_timeout_seconds)
            if not patch:
                raise RunnerError("github_recovery_no_changes", "failed GitHub candidate has no changes to recover")
            patch_path.write_bytes(patch)
        recovery_base_sha = str(recovery_workspace["base_sha"])
        recovery_head = git_sha(recovery_workspace_path, timeout_seconds=config.git_timeout_seconds)
        recovery_status = _git_checked(recovery_workspace_path, "status", "--porcelain", timeout_seconds=config.git_timeout_seconds)
        if recovery_head == recovery_base_sha and not recovery_status:
            _git_checked(recovery_workspace_path, "apply", "--index", os.fspath(patch_path), timeout_seconds=config.git_timeout_seconds)
            recovery_status = _git_checked(recovery_workspace_path, "status", "--porcelain", timeout_seconds=config.git_timeout_seconds)
        elif recovery_head == recovery_base_sha and recovery_status:
            staged_patch = _git_binary(recovery_workspace_path, "diff", "--cached", "--binary", timeout_seconds=config.git_timeout_seconds)
            unstaged = _git_binary(recovery_workspace_path, "diff", "--binary", timeout_seconds=config.git_timeout_seconds)
            if staged_patch != patch_path.read_bytes() or unstaged:
                raise RunnerError("github_recovery_evidence_invalid", "recovery workspace has changes that do not match its persisted patch")
        elif recovery_status:
            raise RunnerError("github_recovery_evidence_invalid", "recovery workspace has unrecorded changes")
        if recovery_head == recovery_base_sha:
            if not recovery_status:
                raise RunnerError("github_recovery_no_changes", "rebased recovery workspace has no candidate changes")
            _git_checked(recovery_workspace_path, "-c", "user.name=Spec Runner", "-c", "user.email=spec-runner@localhost",
                         "commit", "-m", f"spec-runner: recover {spec_key} after failed CI", timeout_seconds=config.git_timeout_seconds)
        recovery_sha = git_sha(recovery_workspace_path, timeout_seconds=config.git_timeout_seconds)
        recovery_candidate = verify_candidate(
            workspace=recovery_workspace_path,
            candidate_sha=recovery_sha,
            acceptance_version=str(candidate.get("acceptance_version") or ""),
            checks=list(config.acceptance_checks),
            acceptance=list(config.acceptance_ids),
            base_sha=str(recovery_workspace["base_sha"]),
            allowed_paths=config.acceptance_paths,
            git_timeout_seconds=config.git_timeout_seconds,
        )
        _write_json_atomic(recovery_path, recovery_candidate)

    ticket_path = artifact / f"ticket-plan-{spec_key}.json"
    if not ticket_path.is_file():
        raise RunnerError("ticket_plan_missing", f"SPEC {spec_key} has no persisted TicketPlan")
    ticket_plan = validate_ticket_plan(load_json(ticket_path), expected_spec_key=spec_key)
    if candidate.get("acceptance_version") != ticket_plan.get("digest"):
        raise RunnerError("github_recovery_evidence_invalid", "failed candidate is bound to a different TicketPlan")
    implementation_prefix = f"codex_sdk:{run.run_id}:codex_implementation:{spec_key}"
    implementation_workers = [
        item for item in store.workers_for_run(run.run_id)
        if str(item.get("worker_id") or "") == implementation_prefix
        and item.get("external_thread_id")
    ]
    if not implementation_workers:
        raise RunnerError("github_recovery_owner_missing", "failed GitHub candidate has no implementation owner")
    implementation_thread = str(implementation_workers[-1]["external_thread_id"])
    recovery_sha = str(recovery_candidate["candidate_sha"])
    review_path = artifact / f"review-{spec_key}-{recovery_sha[:12]}.json"
    if review_path.is_file():
        recovery_review_document = load_json(review_path)
        review_worker_path = artifact / f"review-worker-{spec_key}-{recovery_sha[:12]}.json"
        review_worker_document = load_json(review_worker_path) if review_worker_path.is_file() else {}
        embedded_worker = recovery_review_document.get("worker")
        if (recovery_review_document.get("approved") is not True
                or recovery_review_document.get("candidate_sha") != recovery_sha
                or not isinstance(embedded_worker, dict)
                or embedded_worker != review_worker_document
                or embedded_worker.get("status") != "completed"
                or embedded_worker.get("error") is not None
                or not isinstance(embedded_worker.get("thread_id"), str)
                or embedded_worker.get("thread_id") == implementation_thread
                or not isinstance(embedded_worker.get("turn_id"), str)
                or not isinstance(embedded_worker.get("final_response"), str)):
            raise RunnerError("github_recovery_evidence_invalid", "persisted recovery review does not approve its candidate")
        review_result = CodexWorkerResult(
            thread_id=str(embedded_worker["thread_id"]), turn_id=str(embedded_worker["turn_id"]),
            status="completed", error=None, final_response=str(embedded_worker["final_response"]),
            item_count=int(embedded_worker.get("item_count", 0)),
            started_at=int(embedded_worker.get("started_at", 0)),
            completed_at=int(embedded_worker.get("completed_at", 0)),
            approval_mode=str(embedded_worker.get("approval_mode") or "deny_all"),
            skill_observation=embedded_worker.get("skill_observation") if isinstance(embedded_worker.get("skill_observation"), dict) else None,
        )
        try:
            expected_review = independent_review(
                review_result, implementation_thread=implementation_thread,
                candidate_sha=recovery_sha, acceptance_version=str(ticket_plan["digest"]),
            )
        except (RunnerError, ValueError, TypeError) as exc:
            raise RunnerError("github_recovery_evidence_invalid", "persisted recovery review failed revalidation") from exc
        if any(recovery_review_document.get(key) != value for key, value in expected_review.items()):
            raise RunnerError("github_recovery_evidence_invalid", "persisted recovery review changed")
        recovery_review = recovery_review_document
    else:
        recovery_review, _ = _execute_independent_review(
            control_root=control_root, config=config, brief_digest=run.input_digest,
            run=run, store=store, ticket_plan=ticket_plan,
            workspace=recovery_workspace_path, candidate_sha=recovery_sha,
            candidate_receipt=recovery_candidate, implementation_thread=implementation_thread,
            artifact_directory=artifact,
        )
        if recovery_review.get("approved") is not True:
            raise RunnerError("review_blocked", "fresh recovery review has blocking findings",
                              details={"findings": recovery_review.get("blocking")})
    review_projection = {
        key: recovery_review.get(key)
        for key in ("approved", "blocking", "candidate_sha", "findings", "review_digest")
    }
    recovered = _execute_github_delivery(
        control_root=control_root, config=config, run=run, spec_key=spec_key,
        candidate_sha=recovery_sha, branch=str(recovery_workspace["branch"]),
        candidate_receipt=recovery_candidate, review=review_projection,
    )
    recovery_metadata = {
        "candidate_sha": old_candidate_sha,
        "pr": github.get("pr"),
        "checks": failed_checks,
        "failed_receipt": os.fspath(failed_path),
        "workspace_manifest": os.fspath(manifest_path),
        "base_sync": base_sync,
    }
    recovered = {**recovered, "recovered_from": recovery_metadata}
    _write_json_atomic(artifact / f"candidate-{spec_key}.json", recovery_candidate)
    _write_json_atomic(artifact / f"github-{spec_key}.json", recovered)
    store.append_event(
        run_id=run.run_id,
        event_key=f"github-recovery:{run.run_id}:{spec_key}:{old_candidate_sha}:published:{recovery_sha}",
        event_type="github_candidate_recovery_published",
        payload={"spec_key": spec_key, "failed_candidate_sha": old_candidate_sha,
                 "candidate_sha": recovery_sha, "branch": recovery_workspace["branch"],
                 "state": recovered.get("state"), "base_sync": base_sync},
    )
    recovered["_workspace_manifest"] = os.fspath(recovery_workspace["manifest"])
    return recovered


def _resume_waiting_github(*, control_root: Path, config: RunnerConfig, run: RunRecord,
                           store: Store, finalize_run: bool = True) -> dict[str, object]:
    artifact = _safe_artifact_directory(control_root, config, run.run_id)
    github_files = sorted(artifact.glob("github-*.json"))
    manifests = []
    for path in (control_root / "delivery-workspaces").glob("*.manifest.json"):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if document.get("run_id") == run.run_id:
            manifests.append((path, document))
    waiting = []
    completed_specs = _production_completed_specs(
        control_root=control_root, config=config, run_id=run.run_id, store=store,
    )
    for path in github_files:
        document = load_json(path)
        if (document.get("state") == "waiting_ci"
                and document.get("spec_key") not in completed_specs):
            waiting.append(document)
    if len(waiting) != 1:
        raise RunnerError("github_waiting_evidence_missing", "waiting GitHub run needs one unambiguous SPEC delivery receipt")
    github = waiting[0]
    spec_key = github.get("spec_key")
    if not isinstance(spec_key, str) or not spec_key:
        raise RunnerError("github_waiting_evidence_missing", "waiting GitHub receipt has no SPEC identity")
    candidate_path = artifact / f"candidate-{spec_key}.json"
    if not candidate_path.is_file():
        raise RunnerError("github_waiting_evidence_missing", "waiting GitHub run has no candidate receipt for its SPEC")
    candidate = load_json(candidate_path)
    candidate_sha = candidate.get("candidate_sha")
    github_candidate = github.get("candidate")
    if (candidate.get("outcome") != "verified" or not isinstance(candidate_sha, str)
            or len(candidate_sha) != 40 or not isinstance(github_candidate, dict)
            or github_candidate.get("candidate_sha") != candidate_sha):
        raise RunnerError("github_waiting_evidence_invalid", "waiting GitHub receipt does not match its verified candidate")
    review_path = artifact / f"review-{spec_key}-{candidate_sha[:12]}.json"
    if not review_path.is_file():
        raise RunnerError("github_waiting_evidence_missing", "waiting GitHub run has no validated review for its candidate")
    review = load_json(review_path)
    github_review = github.get("review")
    review_projection = {
        key: review.get(key)
        for key in ("approved", "blocking", "candidate_sha", "findings", "review_digest")
    }
    legacy_review_receipt = isinstance(github_review, dict) and github_review == review
    if (review.get("approved") is not True or review.get("candidate_sha") != candidate_sha
            or not isinstance(review.get("review_digest"), str) or not review["review_digest"].strip()
            or not isinstance(github_review, dict)
            or (github_review != review_projection and not legacy_review_receipt)):
        raise RunnerError("github_waiting_evidence_invalid", "waiting GitHub receipt does not match its validated independent review")
    github_branch = github.get("branch")
    matching_manifests = [
        item for item in manifests
        if item[1].get("spec_key") == spec_key
        and (github_branch is None or item[1].get("branch") == github_branch)
    ]
    if len(matching_manifests) != 1:
        raise RunnerError("github_waiting_evidence_missing", "waiting GitHub run needs one workspace manifest for its SPEC")
    manifest_path, manifest = matching_manifests[0]
    result = _execute_github_delivery(control_root=control_root, config=config, run=run,
        spec_key=spec_key, candidate_sha=candidate_sha, branch=str(manifest["branch"]),
        candidate_receipt=candidate, review=review_projection, push=False)
    if _definitive_failed_github_checks(checks=result.get("checks"), candidate_sha=candidate_sha):
        result = _recover_failed_github_candidate(
            control_root=control_root, config=config, run=run, store=store,
            spec_key=spec_key, candidate=candidate, review=review_projection,
            github={**github, **result}, failed_checks=result["checks"],
            manifest_path=manifest_path, manifest=manifest,
        )
        recovery_manifest = result.pop("_workspace_manifest", None)
        if not isinstance(recovery_manifest, str) or not recovery_manifest:
            raise RunnerError("github_recovery_evidence_invalid", "recovered delivery has no cleanup manifest")
        manifest_path = Path(recovery_manifest)
        manifest = load_json(manifest_path)
        manifests.append((manifest_path, manifest))
    if result["state"] != "github_completed":
        if result.get("state") == "waiting_ci":
            _write_json_atomic(artifact / f"github-{spec_key}.json", result)
            store.set_run_state(run.run_id, "waiting_ci")
        return result
    merge = result.get("merge")
    if not isinstance(merge, dict) or merge.get("merged") is not True:
        raise RunnerError("github_merge_unconfirmed", "GitHub delivery cannot complete without a confirmed merge receipt")
    _persist_delivery_evidence(control_root=control_root, config=config, run_id=run.run_id,
                               spec_key=str(result["spec_key"]), delivery=result)
    spec_manifests = [
        (path, document) for path, document in manifests
        if document.get("spec_key") == spec_key
    ]
    cleanups = [
        cleanup_managed_workspace(repository=config.repository_path,
            workspace_root=control_root / "delivery-workspaces",
            workspace=Path(str(document["workspace"])), manifest=path, preserve_manifest=True)
        for path, document in spec_manifests
    ]
    cleanup = cleanups[0] if len(cleanups) == 1 else {
        "outcome": "cleaned" if cleanups and all(item.get("outcome") == "cleaned" for item in cleanups) else "pending",
        "workspaces": cleanups,
    }
    result["cleanup"] = cleanup
    if cleanup["outcome"] != "cleaned":
        store.mark_cleanup_pending(run.run_id)
        result["state"] = "cleanup_pending"
    else:
        ticket_path = artifact / f"ticket-plan-{spec_key}.json"
        if not ticket_path.is_file():
            raise RunnerError("ticket_plan_missing", f"SPEC {spec_key} has no persisted TicketPlan")
        _persist_delivery_evidence(control_root=control_root, config=config, run_id=run.run_id,
                                   spec_key=spec_key, delivery=result)
        try:
            result["issue_closure"] = _close_published_ticket_plan(
                config=config, plan=load_json(ticket_path), run_id=run.run_id, store=store,
            )
        except RunnerError as exc:
            result["issue_closure"] = {"state": "pending", "error_code": exc.code}
            _persist_delivery_evidence(control_root=control_root, config=config, run_id=run.run_id,
                                       spec_key=spec_key, delivery=result)
            store.mark_cleanup_pending(run.run_id)
            result["state"] = "cleanup_pending"
            return result
        _persist_delivery_evidence(control_root=control_root, config=config, run_id=run.run_id,
                                   spec_key=spec_key, delivery=result)
        finalized_cleanups = [
            cleanup_managed_workspace(repository=config.repository_path,
                workspace_root=control_root / "delivery-workspaces",
                workspace=Path(str(document["workspace"])), manifest=path)
            for path, document in spec_manifests
        ]
        if any(item.get("outcome") != "cleaned" for item in finalized_cleanups):
            store.mark_cleanup_pending(run.run_id)
            result["manifest_cleanup"] = finalized_cleanups
            result["state"] = "cleanup_pending"
            return result
        if finalize_run:
            store.mark_archived(run.run_id, state="completed")
        else:
            plan = load_json(artifact / "spec-plan.json")
            _record_production_spec(control_root=control_root, config=config, run_id=run.run_id, store=store,
                spec_key=str(result["spec_key"]), plan_digest=str(plan.get("digest", "")))
            store.set_run_state(run.run_id, "spec_completed")
    if not finalize_run and result.get("state") == "github_completed":
        result["state"] = "spec_completed"
    return result


def _finish_codex_implementation(
    *, control_root: Path, config: RunnerConfig, brief_digest: str, run: RunRecord, store: Store,
    ticket_plan: dict[str, object], workspace_info: dict[str, object], result: CodexWorkerResult,
    finalize_run: bool, validated_review: dict[str, object] | None = None,
) -> dict[str, object]:
    """Finish an implementation whose SDK turn has already completed.

    Keeping this boundary separate lets process-exit recovery consume a
    persisted completed turn without starting a second model turn.
    """
    spec_key = str(ticket_plan["spec_key"])
    workspace = Path(str(workspace_info["workspace"]))
    implementation_operation = f"implementation:{run.run_id}:{spec_key}"
    implementation_step = "codex_implementation"
    implementation_worker = f"codex_sdk:{run.run_id}:{implementation_step}:{spec_key}"
    write_root = _implementation_write_root(workspace=workspace, config=config, create=False)
    # A worker may complete the edit while reporting that its own sandbox
    # could not run a local check.  That report is not delivery evidence, so
    # let the Runner's trusted candidate verification decide whether the
    # candidate is usable.  Blocked, failed, or input-gated outcomes still
    # fail at the semantic boundary.
    implementation_artifacts(result, workspace, allow_completed_blockers=True, artifact_root=write_root)
    validate_candidate_write_scope(
        workspace=workspace, base_sha=str(workspace_info["base_sha"]),
        allowed_paths=config.acceptance_paths,
        git_timeout_seconds=config.git_timeout_seconds,
    )
    workspace_status = _git_checked(workspace, "status", "--porcelain", timeout_seconds=config.git_timeout_seconds)
    if workspace_status:
        _git_checked(workspace, "add", "--all", timeout_seconds=config.git_timeout_seconds)
        _git_checked(workspace, "-c", "user.name=Spec Runner", "-c", "user.email=spec-runner@localhost", "commit", "-m", f"spec-runner: implement {spec_key}", timeout_seconds=config.git_timeout_seconds)
    else:
        # A process can exit after the implementation commit but before the
        # candidate/review phase.  Recovery must adopt that durable commit,
        # not mistake a clean workspace for an implementation that did
        # nothing or start a second worker turn.
        existing_sha = git_sha(workspace, timeout_seconds=config.git_timeout_seconds)
        if existing_sha == str(workspace_info["base_sha"]):
            raise RunnerError("implementation_no_changes", "implementation worker produced no workspace changes")
    candidate_sha = git_sha(workspace, timeout_seconds=config.git_timeout_seconds)
    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    artifact_directory.mkdir(parents=True, exist_ok=True)
    try:
        candidate_receipt = verify_candidate(
            workspace=workspace, candidate_sha=candidate_sha,
            acceptance_version=str(ticket_plan["digest"]), checks=list(config.acceptance_checks),
            acceptance=list(config.acceptance_ids), base_sha=str(workspace_info["base_sha"]),
            allowed_paths=config.acceptance_paths,
            git_timeout_seconds=config.git_timeout_seconds,
        )
    except RunnerError as exc:
        if exc.code != "candidate_verification_failed":
            raise
        # A first candidate can fail the trusted acceptance gate before any
        # review receipt exists. Route that durable workspace back through the
        # bounded repair path so a failed check cannot strand the run at an
        # implementation worker that is already terminal in the SDK.
        store.complete_codex_stage(
            run.run_id, implementation_operation,
            thread_id=result.thread_id, turn_id=result.turn_id,
            state="candidate_verification_failed",
            step_name=implementation_step, worker_id=implementation_worker,
        )
        repaired_sha, _ = _repair_candidate(
            control_root=control_root, config=config, brief_digest=brief_digest,
            run=run, store=store, ticket_plan=ticket_plan, workspace=workspace,
            findings=[_candidate_verification_finding(exc)],
            implementation_thread=result.thread_id, artifact_directory=artifact_directory,
        )
        store.append_event(
            run_id=run.run_id,
            event_key=f"recovery:{run.run_id}:initial-candidate-verification-repaired:{repaired_sha}",
            event_type="candidate_verification_failure_repaired",
            payload={"spec_key": spec_key, "failed_candidate_sha": candidate_sha,
                     "repair_thread_id": result.thread_id, "candidate_sha": repaired_sha},
        )
        return _resume_after_repair_candidate(
            control_root=control_root, config=config, brief_digest=brief_digest,
            run=run, store=store, spec_key=spec_key,
        )
    if validated_review is not None and (
        validated_review.get("approved") is not True
        or validated_review.get("candidate_sha") != candidate_sha
    ):
        raise RunnerError("review_candidate_mismatch", "persisted review does not approve the current verified candidate")
    store.complete_codex_stage(run.run_id, implementation_operation, thread_id=result.thread_id, turn_id=result.turn_id, state="verified_candidate", step_name=implementation_step, worker_id=implementation_worker)
    (artifact_directory / f"candidate-{spec_key}.json").write_text(json.dumps(candidate_receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    if validated_review is None:
        validated_review, _ = _execute_independent_review(control_root=control_root, config=config, brief_digest=brief_digest, run=run, store=store, ticket_plan=ticket_plan, workspace=workspace, candidate_sha=candidate_sha, candidate_receipt=candidate_receipt, implementation_thread=result.thread_id, artifact_directory=artifact_directory)
    repair_round = 0
    while not validated_review["approved"] and repair_round < 2:
        repair_round += 1
        candidate_sha, candidate_receipt = _repair_candidate(control_root=control_root, config=config, brief_digest=brief_digest, run=run, store=store, ticket_plan=ticket_plan, workspace=workspace, findings=list(validated_review["blocking"]), implementation_thread=result.thread_id, artifact_directory=artifact_directory)
        validated_review, _ = _execute_independent_review(control_root=control_root, config=config, brief_digest=brief_digest, run=run, store=store, ticket_plan=ticket_plan, workspace=workspace, candidate_sha=candidate_sha, candidate_receipt=candidate_receipt, implementation_thread=result.thread_id, artifact_directory=artifact_directory)
    if not validated_review["approved"]:
        raise RunnerError("review_blocked", "independent review remained blocked after bounded repair rounds", details={"findings": validated_review["blocking"], "repair_rounds": repair_round})
    # Keep the implementation thread available throughout bounded repair;
    # archive only after its final reviewed turn, never before resuming it.
    archive_event_key = f"cleanup:{run.run_id}:{spec_key}:implementation:{candidate_sha}"
    archive_event = next(
        (event for event in store.events_for_run(run.run_id) if event.get("event_key") == archive_event_key),
        None,
    )
    if archive_event is None:
        archive_event = next(
            (
                event for event in store.events_for_run(run.run_id)
                if event.get("event_type") == "cleanup_readback"
                and isinstance(event.get("payload"), dict)
                and event["payload"].get("thread_id") == result.thread_id
                and event["payload"].get("archived") is True
            ),
            None,
        )
    if archive_event is not None:
        archive = archive_event.get("payload")
        if not isinstance(archive, dict) or archive.get("thread_id") != result.thread_id or archive.get("archived") is not True:
            raise RunnerError("recovery_blocked", "persisted implementation archive receipt changed identity")
    else:
        archive = CodexAdapter().archive_and_readback(thread_id=result.thread_id, repository_path=workspace)
        store.append_event(run_id=run.run_id, event_key=archive_event_key,
                           event_type="cleanup_readback", payload=archive)
    if config.github_repository is not None:
        review_projection = {
            key: validated_review.get(key)
            for key in ("approved", "blocking", "candidate_sha", "findings", "review_digest")
        }
        github_result = _execute_github_delivery(control_root=control_root, config=config, run=run,
            spec_key=spec_key, candidate_sha=candidate_sha, branch=str(workspace_info["branch"]),
            candidate_receipt=candidate_receipt, review=review_projection)
        artifact_directory.joinpath(f"github-{spec_key}.json").write_text(
            json.dumps(github_result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if github_result["state"] == "waiting_ci":
            store.set_run_state(run.run_id, "waiting_ci")
            store.append_event(run_id=run.run_id, event_key=f"github:{run.run_id}:{spec_key}:waiting",
                event_type="github_checks_pending", payload=github_result)
            return github_result
        _persist_delivery_evidence(control_root=control_root, config=config, run_id=run.run_id,
                                   spec_key=spec_key, delivery=github_result)
        cleanup = cleanup_managed_workspace(repository=config.repository_path,
            workspace_root=control_root / "delivery-workspaces", workspace=workspace,
            manifest=Path(str(workspace_info["manifest"])), preserve_manifest=True)
        if cleanup.get("outcome") != "cleaned":
            store.mark_cleanup_pending(run.run_id)
            github_result["cleanup"] = cleanup
            return {**github_result, "state": "cleanup_pending"}
        github_result["cleanup"] = cleanup
        _persist_delivery_evidence(control_root=control_root, config=config, run_id=run.run_id,
                                   spec_key=spec_key, delivery=github_result)
        try:
            github_result["issue_closure"] = _close_published_ticket_plan(
                config=config, plan=ticket_plan, run_id=run.run_id, store=store,
            )
        except RunnerError as exc:
            github_result["issue_closure"] = {"state": "pending", "error_code": exc.code}
            _persist_delivery_evidence(control_root=control_root, config=config, run_id=run.run_id,
                                       spec_key=spec_key, delivery=github_result)
            store.mark_cleanup_pending(run.run_id)
            return {**github_result, "state": "cleanup_pending"}
        _persist_delivery_evidence(control_root=control_root, config=config, run_id=run.run_id,
                                   spec_key=spec_key, delivery=github_result)
        final_cleanup = cleanup_managed_workspace(repository=config.repository_path,
            workspace_root=control_root / "delivery-workspaces", workspace=workspace,
            manifest=Path(str(workspace_info["manifest"])))
        if final_cleanup.get("outcome") != "cleaned":
            store.mark_cleanup_pending(run.run_id)
            github_result["manifest_cleanup"] = final_cleanup
            return {**github_result, "state": "cleanup_pending"}
        if finalize_run:
            store.mark_archived(run.run_id, state="completed")
        else:
            store.set_run_state(run.run_id, "spec_completed")
        return {**github_result, "state": "completed" if finalize_run else "spec_completed"}
    if (
        git_sha(workspace, timeout_seconds=config.git_timeout_seconds) != candidate_sha
        or _git_checked(workspace, "status", "--porcelain",
                        timeout_seconds=config.git_timeout_seconds)
    ):
        raise RunnerError("candidate_changed_after_review", "candidate changed after the verified check/review pair")
    if git_sha(
        config.repository_path, str(workspace_info["branch"]),
        timeout_seconds=config.git_timeout_seconds,
    ) != candidate_sha:
        raise RunnerError("candidate_branch_changed", "candidate branch moved after verification")
    expected_target_sha = str(workspace_info["base_sha"])
    merged = merge_local(
        repository=config.repository_path,
        candidate_branch=str(workspace_info["branch"]),
        target_ref=config.target_ref,
        expected_target_sha=expected_target_sha,
        workspace_root=control_root / "delivery-workspaces",
        run_id=run.run_id,
        git_timeout_seconds=config.git_timeout_seconds,
    )
    _persist_delivery_evidence(control_root=control_root, config=config, run_id=run.run_id,
        spec_key=spec_key, delivery={"candidate": candidate_receipt, "review": validated_review, "merge": merged})
    cleanup = cleanup_managed_workspace(repository=config.repository_path, workspace_root=control_root / "delivery-workspaces", workspace=workspace, manifest=Path(str(workspace_info["manifest"])))
    if cleanup.get("outcome") != "cleaned":
        store.mark_cleanup_pending(run.run_id)
        return {"state": "cleanup_pending", "candidate": candidate_receipt, "review": validated_review, "merge": merged, "cleanup": cleanup}
    if finalize_run:
        store.mark_archived(run.run_id, state="completed")
    else:
        store.set_run_state(run.run_id, "spec_completed")
    return {"state": "completed" if finalize_run else "spec_completed", "spec_key": spec_key,
            "candidate": candidate_receipt, "review": validated_review, "merge": merged, "cleanup": cleanup}


def _persist_implementation_continuation(*, control_root: Path, config: RunnerConfig,
                                         run: RunRecord, ticket_plan: dict[str, object],
                                         workspace_info: dict[str, object], stage: str, store: Store,
                                         generation: int = 0) -> dict[str, object]:
    """Save the minimum business context before a worker can become unusable."""
    workspace = Path(str(workspace_info["workspace"]))
    bundle = ContinuationBundle(
        run_id=run.run_id,
        spec_key=str(ticket_plan.get("spec_key", "")),
        stage=stage,
        generation=generation,
        input_revision=str(ticket_plan.get("digest", run.input_digest)),
        requirements=list(ticket_plan.get("tickets", [])) if isinstance(ticket_plan.get("tickets"), list) else [],
        confirmed_decisions=[],
        tickets=list(ticket_plan.get("tickets", [])) if isinstance(ticket_plan.get("tickets"), list) else [],
        dependencies=[{"spec_key": item} for item in ticket_plan.get("blocked_by", [])] if isinstance(ticket_plan.get("blocked_by"), list) else [],
        workspace=_continuation_workspace_identity(
            workspace=workspace, workspace_info=workspace_info,
            git_timeout_seconds=config.git_timeout_seconds,
        ),
        verified_items=[],
        remaining_items=[{"kind": "implementation"}, {"kind": "candidate_verification"}, {"kind": "independent_review"}],
        tests=list(config.acceptance_checks),
        review=[],
        unconfirmed_operations=[],
        authorization={"repository": os.fspath(config.repository_path), "write_scope": list(config.acceptance_paths)},
        last_verified_progress=None,
        source_refs=[{"kind": "ticket_plan", "digest": ticket_plan.get("digest")}],
    )
    path = _safe_artifact_directory(control_root, config, run.run_id) / f"continuation-{bundle.spec_key}.json"
    document = write_bundle_atomic(path, bundle)
    store.record_continuation_bundle(run_id=run.run_id, bundle_path=path, bundle=document)
    return document


def _execute_codex_implementation(
    *, control_root: Path, config: RunnerConfig, brief_digest: str, run: RunRecord, store: Store,
    ticket_plan: dict[str, object], finalize_run: bool = True, thread_id: str | None = None,
) -> dict[str, object]:
    """Run one real implementation and independent review for the active SPEC.

    The worker may write only its managed worktree.  Commit, candidate
    verification, review binding, merge and cleanup remain Runner operations.
    """
    if not config.acceptance_ids or not config.acceptance_checks:
        raise RunnerError("acceptance_config_missing", "production implementation requires workflow.acceptance ids and checks")
    if len(config.acceptance_paths) != 1:
        raise RunnerError("write_scope_missing", "production implementation requires exactly one trusted write-scope root")
    spec_key = str(ticket_plan["spec_key"])
    workspace_info = prepare_workspace(
        repository=config.repository_path, workspace_root=control_root / "delivery-workspaces",
        run_id=run.run_id, spec_key=spec_key, base_ref=config.target_ref,
        git_timeout_seconds=config.git_timeout_seconds,
    )
    workspace = Path(str(workspace_info["workspace"]))
    write_root = _implementation_write_root(workspace=workspace, config=config, create=True)
    implementation_operation = f"implementation:{run.run_id}:{spec_key}"
    implementation_step = "codex_implementation"
    implementation_worker = f"codex_sdk:{run.run_id}:{implementation_step}:{spec_key}"
    store.begin_stage(run.run_id, step_name=implementation_step, operation_id=implementation_operation, backend_kind="codex_sdk", worker_id=implementation_worker)
    _persist_implementation_continuation(
        control_root=control_root, config=config, run=run, ticket_plan=ticket_plan,
        workspace_info=workspace_info, stage=implementation_step, store=store,
    )
    schema = IMPLEMENTATION_SCHEMA
    implementation_prompt = (
        "Implement this SPEC only in the assigned write-scope directory. Work on the real code and tests there; do not publish, merge, "
        "or modify files outside that directory. Do not run repository-wide test discovery, invoke pytest from a parent project, "
        "start another Runner, or operate on any external repository. Runner will execute the exact trusted acceptance checks after "
        "this turn. Return JSON only after the implementation is complete. "
        "The artifacts array must contain one or more paths relative to the assigned write-scope directory to real files you changed "
        "(for example, src/module.py); do not put descriptions, summaries, or links in artifacts.\n\n"
        + json.dumps(ticket_plan, ensure_ascii=False, sort_keys=True)
    )
    answers = store.answers_for_run(run.run_id)
    if answers:
        implementation_prompt += "\n\nRunner-recorded implementation answers (use these as decisions; do not ask them again):\n" + json.dumps(answers, ensure_ascii=False, sort_keys=True)
    result = _run_worker(
        adapter=CodexAdapter(), phase="implement", config=config,
        prompt=implementation_prompt,
        model=config.model_name, effort=config.effort, thread_id=thread_id, repository_path=write_root,
        trusted={"brief_digest": brief_digest, "stage": implementation_step, "spec_key": spec_key,
                 "workspace": os.fspath(workspace), "write_scope": os.fspath(write_root),
                 "ticket_plan_digest": ticket_plan["digest"], "answers": answers},
        on_turn_started=lambda thread_id, turn_id: _record_codex_turn_started(store, run_id=run.run_id, operation_id=implementation_operation, step_name=implementation_step, worker_id=implementation_worker, thread_id=thread_id, turn_id=turn_id),
        control_state=lambda: _read_control_state(control_root=control_root, run_id=run.run_id), schema=schema,
    )
    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    artifact_directory.mkdir(parents=True, exist_ok=True)
    (artifact_directory / f"implementation-{spec_key}.json").write_text(json.dumps(result.public(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    input_gate = _persist_implementation_input_gate(
        control_root=control_root, config=config, run=run, store=store, result=result,
        brief_digest=brief_digest, operation_id=implementation_operation, step_name=implementation_step,
        worker_id=implementation_worker, spec_key=spec_key,
    )
    if input_gate is not None:
        return input_gate
    return _finish_codex_implementation(
        control_root=control_root, config=config, brief_digest=brief_digest, run=run, store=store,
        ticket_plan=ticket_plan, workspace_info=workspace_info, result=result, finalize_run=finalize_run,
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
    if result.status == "failed" or result.error:
        raise RunnerError(
            "codex_worker_failed",
            "Codex worker returned a failed terminal result",
            details={"fault_observation": result.fault_observation, "thread_id": result.thread_id, "turn_id": result.turn_id},
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
    if result.status == "failed" or result.error:
        raise RunnerError(
            "codex_worker_failed",
            "Codex worker returned a failed terminal result",
            details={"fault_observation": result.fault_observation, "thread_id": result.thread_id, "turn_id": result.turn_id},
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
    *, control_root: Path, config: RunnerConfig, run: RunRecord, brief: str, brief_digest: str, store: Store,
    thread_id: str | None = None, spec_key: str | None = None,
) -> dict[str, object]:
    """Continue the current SDK stage on its persisted thread.

    A paused run is a stage boundary, not permission to start a competing
    thread. The formal thread identity persisted by ``Thread.turn()`` is the
    only identity accepted for this continuation or a reconciled retry.
    """
    workers = store.workers_for_run(run.run_id)
    worker_prefix = f"codex_sdk:{run.run_id}"
    if run.current_step == "codex_example":
        worker_matches = [worker for worker in workers if worker.get("worker_id") == worker_prefix]
    elif run.current_step == "codex_ticket_planning":
        ticket_prefix = f"{worker_prefix}:codex_ticket_planning:"
        worker_matches = [
            worker for worker in workers
            if str(worker.get("worker_id") or "").startswith(ticket_prefix)
            and (spec_key is None or str(worker.get("worker_id"))[len(ticket_prefix):] == spec_key)
        ]
    elif run.current_step == "codex_implementation":
        implementation_prefix = f"{worker_prefix}:codex_implementation:"
        worker_matches = [
            worker for worker in workers
            if str(worker.get("worker_id") or "").startswith(implementation_prefix)
            and (spec_key is None or str(worker.get("worker_id"))[len(implementation_prefix):] == spec_key)
        ]
    else:
        expected_worker = f"{worker_prefix}:{run.current_step}"
        worker_matches = [worker for worker in workers if worker.get("worker_id") == expected_worker]
    if not worker_matches:
        raise RunnerError("resume_worker_missing", "the current SDK stage has no uniquely identified worker")
    worker = worker_matches[-1]
    thread_id = thread_id or str(worker.get("external_thread_id") or "")
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
    if run.current_step in {"codex_grill", "codex_planning"}:
        if run.current_step == "codex_grill":
            clarified = _execute_codex_grill(control_root=control_root, config=config, brief=brief,
                brief_digest=brief_digest, run=run, store=store, thread_id=thread_id)
            if clarified.state != "clarified":
                return store.public_status(run.run_id)
            thread_id = None
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
        if spec_key is None:
            prefix = f"codex_sdk:{run.run_id}:codex_ticket_planning:"
            matching_workers = [
                candidate for candidate in reversed(workers)
                if str(candidate.get("worker_id") or "").startswith(prefix)
            ]
            if matching_workers:
                spec_key = str(matching_workers[0]["worker_id"])[len(prefix):]
        if not spec_key:
            raise RunnerError("ticket_spec_missing", "ticket planning resume has no uniquely identified SPEC")
        full_plan = load_json(plans[0])
        specs = full_plan.get("specs")
        if not isinstance(specs, list):
            raise RunnerError("invalid_spec_plan", "ticket planning resume requires a SPEC list")
        selected = [item for item in specs if isinstance(item, dict) and item.get("key") == spec_key]
        if len(selected) != 1:
            raise RunnerError("ticket_spec_missing", "ticket planning resume could not match its persisted SPEC")
        scoped_plan = {**full_plan, "specs": selected}
        resumed = _execute_codex_tickets(
            control_root=control_root, config=config, brief_digest=brief_digest, run=run, store=store,
            spec_plan=scoped_plan, thread_id=thread_id,
        )
        if resumed.state == "tickets_ready" and config.workflow_mode == "production":
            ticket_path = _safe_artifact_directory(control_root, config, run.run_id) / f"ticket-plan-{spec_key}.json"
            if not ticket_path.is_file():
                raise RunnerError("ticket_plan_missing", f"SPEC {spec_key} has no persisted TicketPlan")
            return _execute_codex_implementation(
                control_root=control_root, config=config, brief_digest=brief_digest, run=resumed,
                store=store, ticket_plan=load_json(ticket_path),
            )
        return store.public_status(resumed.run_id)
    if run.current_step == "codex_implementation":
        implementation_prefix = f"{worker_prefix}:codex_implementation:"
        if spec_key is None:
            spec_key = str(worker.get("worker_id") or "")[len(implementation_prefix):]
        if not spec_key:
            raise RunnerError("implementation_spec_missing", "implementation resume has no uniquely identified SPEC")
        ticket_path = _safe_artifact_directory(control_root, config, run.run_id) / f"ticket-plan-{spec_key}.json"
        if not ticket_path.is_file():
            raise RunnerError("ticket_plan_missing", f"SPEC {spec_key} has no persisted TicketPlan")
        return _execute_codex_implementation(
            control_root=control_root, config=config, brief_digest=brief_digest, run=run, store=store,
            ticket_plan=load_json(ticket_path), thread_id=thread_id,
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


def _reconcile_blocked_review(*, control_root: Path, config: RunnerConfig,
                              run: RunRecord, store: Store, worker: dict[str, object],
                              brief_digest: str) -> dict[str, object]:
    """Resume repair after a valid review was persisted with blocking findings.

    ``_execute_independent_review`` records a structurally valid review as
    ``reviewed`` before the implementation loop decides that its findings
    require repair.  If the process exits at that boundary, the durable worker
    is therefore ``reviewed`` even though the run is blocked.  Treat that
    state as a repair frontier and reuse the persisted review evidence.
    """
    prefix = f"codex_sdk:{run.run_id}:codex_review:"
    worker_id = str(worker.get("worker_id") or "")
    if not worker_id.startswith(prefix):
        raise RunnerError("recovery_blocked", "blocking review worker is not scoped to a candidate")
    identity = worker_id[len(prefix):].rsplit(":", 1)
    if len(identity) != 2 or not identity[0] or len(identity[1]) < 12:
        raise RunnerError("recovery_blocked", "blocking review worker has no SPEC/candidate identity")
    spec_key, candidate_short = identity
    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    review_path = artifact_directory / f"review-{spec_key}-{candidate_short}.json"
    if not review_path.is_file():
        raise RunnerError("recovery_blocked", "blocking review has no persisted validated receipt")
    validated_review = load_json(review_path)
    if validated_review.get("approved") is not False or not isinstance(validated_review.get("blocking"), list) or not validated_review["blocking"]:
        raise RunnerError("recovery_blocked", "persisted review is not a blocking repair frontier")
    ticket_path = artifact_directory / f"ticket-plan-{spec_key}.json"
    if not ticket_path.is_file():
        raise RunnerError("recovery_blocked", "blocking review has no matching TicketPlan")
    ticket_plan = load_json(ticket_path)
    candidate_receipt = None
    candidate_files = [artifact_directory / f"candidate-{spec_key}.json", *sorted(artifact_directory.glob(f"repair-{spec_key}-*.json"))]
    for path in candidate_files:
        if not path.is_file():
            continue
        document = load_json(path)
        candidate = document.get("candidate") if path.name.startswith("repair-") else document
        if isinstance(candidate, dict) and str(candidate.get("candidate_sha") or "").startswith(candidate_short):
            candidate_receipt = candidate
            break
    if candidate_receipt is None:
        raise RunnerError("recovery_blocked", "blocking review has no matching candidate receipt")
    implementation_prefix = f"codex_sdk:{run.run_id}:codex_implementation:{spec_key}"
    implementation_workers = [
        item for item in store.workers_for_run(run.run_id)
        if str(item.get("worker_id") or "") == implementation_prefix and item.get("external_thread_id")
    ]
    if not implementation_workers:
        raise RunnerError("recovery_blocked", "blocking review has no implementation owner")
    implementation_thread = str(implementation_workers[-1]["external_thread_id"])
    workspace = _implementation_workspace_path(control_root=control_root, config=config, run=run, spec_key=spec_key)
    repair_round = 0
    while not validated_review["approved"] and repair_round < 2:
        repair_round += 1
        candidate_sha, candidate_receipt = _repair_candidate(
            control_root=control_root, config=config, brief_digest=brief_digest,
            run=run, store=store, ticket_plan=ticket_plan, workspace=workspace,
            findings=list(validated_review["blocking"]), implementation_thread=implementation_thread,
            artifact_directory=artifact_directory,
        )
        validated_review, _ = _execute_independent_review(
            control_root=control_root, config=config, brief_digest=brief_digest,
            run=run, store=store, ticket_plan=ticket_plan, workspace=workspace,
            candidate_sha=candidate_sha, candidate_receipt=candidate_receipt,
            implementation_thread=implementation_thread, artifact_directory=artifact_directory,
        )
    if not validated_review["approved"]:
        raise RunnerError("review_blocked", "independent review remained blocked after bounded repair rounds", details={"findings": validated_review["blocking"], "repair_rounds": repair_round})
    implementation_path = artifact_directory / f"implementation-{spec_key}.json"
    if not implementation_path.is_file():
        raise RunnerError("recovery_blocked", "blocking review has no persisted implementation result")
    implementation_document = load_json(implementation_path)
    result = CodexWorkerResult(
        thread_id=implementation_thread,
        turn_id=str(implementation_document["turn_id"]),
        status="completed", error=None,
        final_response=str(implementation_document["final_response"]),
        item_count=int(implementation_document["item_count"]),
        started_at=int(implementation_document["started_at"]),
        completed_at=int(implementation_document["completed_at"]),
    )
    workspace_info = prepare_workspace(
        repository=config.repository_path, workspace_root=control_root / "delivery-workspaces",
        run_id=run.run_id, spec_key=spec_key, base_ref=config.target_ref,
        git_timeout_seconds=config.git_timeout_seconds,
    )
    return _finish_codex_implementation(
        control_root=control_root, config=config, brief_digest=brief_digest,
        run=run, store=store, ticket_plan=ticket_plan, workspace_info=workspace_info,
        result=result, finalize_run=False, validated_review=validated_review,
    )


def _reconcile_approved_review(*, control_root: Path, config: RunnerConfig,
                               run: RunRecord, store: Store, worker: dict[str, object],
                               brief_digest: str) -> dict[str, object]:
    """Finish a persisted approved review without replaying either worker.

    A run can be blocked after review when the target ref moves before merge.
    That is a merge frontier, not a repair frontier.  Reconcile all durable
    evidence before handing the candidate back to the normal finish path.
    """
    prefix = f"codex_sdk:{run.run_id}:codex_review:"
    worker_id = str(worker.get("worker_id") or "")
    if not worker_id.startswith(prefix):
        raise RunnerError("recovery_blocked", "approved review worker is not scoped to a candidate")
    if worker.get("state") != "reviewed":
        raise RunnerError("recovery_blocked", "approved review worker is not durably terminal")
    identity = worker_id[len(prefix):].rsplit(":", 1)
    if len(identity) != 2 or not identity[0] or len(identity[1]) < 12:
        raise RunnerError("recovery_blocked", "approved review worker has no SPEC/candidate identity")
    spec_key, candidate_short = identity
    thread_id = str(worker.get("external_thread_id") or "")
    turn_id = str(worker.get("external_turn_id") or "")
    if not thread_id or not turn_id:
        raise RunnerError("recovery_blocked", "approved review lacks durable thread/turn identity")
    workspace = _implementation_workspace_path(
        control_root=control_root, config=config, run=run, spec_key=spec_key,
    )
    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    review_path = artifact_directory / f"review-{spec_key}-{candidate_short}.json"
    review_worker_path = artifact_directory / f"review-worker-{spec_key}-{candidate_short}.json"
    ticket_path = artifact_directory / f"ticket-plan-{spec_key}.json"
    for path, message in (
        (review_path, "approved review has no persisted validated receipt"),
        (review_worker_path, "approved review has no persisted worker receipt"),
        (ticket_path, "approved review has no matching TicketPlan"),
    ):
        if not path.is_file():
            raise RunnerError("recovery_blocked", message)
    validated_review = load_json(review_path)
    if validated_review.get("approved") is not True or validated_review.get("candidate_sha") is None:
        raise RunnerError("recovery_blocked", "persisted review is not an approved candidate frontier")
    candidate_receipt = None
    canonical_candidate_path = artifact_directory / f"candidate-{spec_key}.json"
    if canonical_candidate_path.is_file():
        canonical_candidate = load_json(canonical_candidate_path)
        if str(canonical_candidate.get("candidate_sha") or "").startswith(candidate_short):
            candidate_receipt = canonical_candidate
    if candidate_receipt is None:
        for candidate_path in sorted(artifact_directory.glob("candidate-*.json")):
            document = load_json(candidate_path)
            if str(document.get("candidate_sha") or "").startswith(candidate_short):
                if candidate_receipt is not None:
                    raise RunnerError("recovery_blocked", "approved review matches multiple candidate receipts")
                candidate_receipt = document
    if candidate_receipt is None:
        raise RunnerError("recovery_blocked", "approved review has no matching candidate receipt")
    candidate_sha = str(candidate_receipt.get("candidate_sha") or "")
    if (
        not candidate_sha
        or not candidate_sha.startswith(candidate_short)
        or validated_review.get("candidate_sha") != candidate_sha
        or candidate_receipt.get("outcome") != "verified"
    ):
        raise RunnerError("recovery_blocked", "approved review does not match the persisted candidate")
    review_document = load_json(review_worker_path)
    if (
        review_document.get("thread_id") != thread_id
        or review_document.get("turn_id") != turn_id
        or review_document.get("status") != "completed"
        or review_document.get("error") is not None
    ):
        raise RunnerError("recovery_blocked", "approved review worker receipt is not terminal or does not match its stage identity")
    final_response = review_document.get("final_response")
    if not isinstance(final_response, str) or not final_response.strip():
        raise RunnerError("recovery_blocked", "approved review has no structured worker output")
    review_result = CodexWorkerResult(
        thread_id=thread_id,
        turn_id=turn_id,
        status="completed",
        error=review_document.get("error"),
        final_response=final_response,
        item_count=int(review_document.get("item_count", 0)),
        started_at=int(review_document.get("started_at", 0)),
        completed_at=int(review_document.get("completed_at", 0)),
        approval_mode=str(review_document.get("approval_mode") or "deny_all"),
        skill_observation=review_document.get("skill_observation") if isinstance(review_document.get("skill_observation"), dict) else None,
    )
    workspace_info = _candidate_workspace_info(
        control_root=control_root, config=config, run=run, spec_key=spec_key,
        candidate_sha=candidate_sha,
    )
    ticket_plan = validate_ticket_plan(
        load_json(ticket_path), expected_spec_key=spec_key,
    )
    if candidate_receipt.get("acceptance_version") != ticket_plan["digest"]:
        raise RunnerError("recovery_blocked", "approved candidate receipt is bound to a different TicketPlan")
    implementation_prefix = f"codex_sdk:{run.run_id}:codex_implementation:{spec_key}"
    implementation_workers = [
        item for item in store.workers_for_run(run.run_id)
        if str(item.get("worker_id") or "") == implementation_prefix
        and item.get("external_thread_id")
    ]
    if not implementation_workers:
        raise RunnerError("recovery_blocked", "approved review has no implementation owner")
    implementation_worker = implementation_workers[-1]
    implementation_thread = str(implementation_worker["external_thread_id"])
    try:
        expected_review = independent_review(
            review_result,
            implementation_thread=implementation_thread,
            candidate_sha=candidate_sha,
            acceptance_version=str(ticket_plan["digest"]),
        )
    except RunnerError as exc:
        raise RunnerError("recovery_blocked", "persisted approved review failed revalidation", details={"code": exc.code}) from exc
    if any(validated_review.get(key) != value for key, value in expected_review.items()):
        raise RunnerError("recovery_blocked", "persisted approved review receipt changed")
    if validated_review.get("worker") != review_result.public():
        raise RunnerError("recovery_blocked", "approved review worker receipt changed")
    implementation_path = artifact_directory / f"implementation-{spec_key}.json"
    if not implementation_path.is_file():
        raise RunnerError("recovery_blocked", "approved review has no persisted implementation result")
    implementation_document = load_json(implementation_path)
    if (
        implementation_document.get("status") != "completed"
        or implementation_document.get("error") is not None
        or implementation_document.get("thread_id") != implementation_thread
        or implementation_document.get("turn_id") != implementation_worker.get("external_turn_id")
    ):
        raise RunnerError("recovery_blocked", "approved candidate has no matching completed implementation receipt")
    result = CodexWorkerResult(
        thread_id=implementation_thread,
        turn_id=str(implementation_document["turn_id"]),
        status="completed",
        error=None,
        final_response=str(implementation_document["final_response"]),
        item_count=int(implementation_document["item_count"]),
        started_at=int(implementation_document["started_at"]),
        completed_at=int(implementation_document["completed_at"]),
    )
    return _finish_codex_implementation(
        control_root=control_root, config=config, brief_digest=brief_digest,
        run=run, store=store, ticket_plan=ticket_plan, workspace_info=workspace_info,
        result=result, finalize_run=False, validated_review=validated_review,
    )


def _reconcile_rejected_review(*, control_root: Path, config: RunnerConfig,
                               run: RunRecord, store: Store, worker: dict[str, object],
                               brief_digest: str) -> dict[str, object]:
    """Retry review after a terminal reviewer turn produced an invalid receipt."""
    prefix = f"codex_sdk:{run.run_id}:codex_review:"
    worker_id = str(worker.get("worker_id") or "")
    if not worker_id.startswith(prefix):
        raise RunnerError("recovery_blocked", "rejected review worker is not scoped to a candidate")
    review_identity = worker_id[len(prefix):].rsplit(":", 1)
    if len(review_identity) != 2:
        raise RunnerError("recovery_blocked", "rejected review worker has no SPEC/candidate identity")
    spec_key, candidate_sha = review_identity
    if len(candidate_sha) < 12:
        raise RunnerError("recovery_blocked", "rejected review worker has no candidate identity")
    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    candidate_files = sorted(artifact_directory.glob("candidate-*.json"))
    matching_candidate = None
    for path in candidate_files:
        try:
            document = load_json(path)
        except RunnerError:
            continue
        if str(document.get("candidate_sha") or "").startswith(candidate_sha):
            matching_candidate = document
            break
    if matching_candidate is None:
        for path in sorted(artifact_directory.glob(f"repair-{spec_key}-*.json")):
            try:
                document = load_json(path)
            except RunnerError:
                continue
            candidate = document.get("candidate")
            if isinstance(candidate, dict) and str(candidate.get("candidate_sha") or "").startswith(candidate_sha):
                matching_candidate = candidate
                break
    if matching_candidate is None:
        raise RunnerError("recovery_blocked", "rejected review has no matching candidate receipt")
    ticket_path = artifact_directory / f"ticket-plan-{spec_key}.json"
    if not ticket_path.is_file():
        raise RunnerError("recovery_blocked", "rejected review has no matching TicketPlan")
    ticket_plan = load_json(ticket_path)
    workspace_info = _candidate_workspace_info(
        control_root=control_root, config=config, run=run, spec_key=spec_key,
        candidate_sha=str(matching_candidate["candidate_sha"]),
    )
    workspace = Path(str(workspace_info["workspace"]))
    old_thread = str(worker.get("external_thread_id") or "")
    if not old_thread or not str(worker.get("external_turn_id") or ""):
        raise RunnerError("recovery_blocked", "rejected review lacks formal thread/turn identity")
    archive = CodexAdapter().archive_and_readback(thread_id=old_thread, repository_path=workspace)
    if archive.get("thread_id") != old_thread or archive.get("archived") is not True:
        raise RunnerError("recovery_blocked", "rejected review owner was not archived and read back")
    store.append_event(
        run_id=run.run_id,
        event_key=f"recovery:{run.run_id}:review-rejected:{old_thread}:{worker.get('external_turn_id')}",
        event_type="rejected_review_reconciled",
        payload={"spec_key": spec_key, "candidate_sha": candidate_sha, "archive": archive},
    )
    implementation_thread = ""
    implementation_prefix = f"codex_sdk:{run.run_id}:codex_implementation:{spec_key}"
    for candidate in reversed(store.workers_for_run(run.run_id)):
        if str(candidate.get("worker_id") or "") == implementation_prefix and candidate.get("external_thread_id"):
            implementation_thread = str(candidate["external_thread_id"])
            break
    if not implementation_thread:
        raise RunnerError("recovery_blocked", "rejected review has no implementation owner for review context")
    refreshed = _execute_independent_review(
        control_root=control_root,
        config=config,
        brief_digest=brief_digest,
        run=run,
        store=store,
        ticket_plan=ticket_plan,
        workspace=workspace,
        candidate_sha=str(matching_candidate["candidate_sha"]),
        candidate_receipt=matching_candidate,
        implementation_thread=implementation_thread,
        artifact_directory=artifact_directory,
    )
    validated, _ = refreshed
    if not validated["approved"]:
        raise RunnerError("review_blocked", "recovered review still has blocking findings", details={"findings": validated["blocking"]})
    implementation_result_path = artifact_directory / f"implementation-{spec_key}.json"
    implementation_document = load_json(implementation_result_path)
    result = CodexWorkerResult(
        thread_id=implementation_thread,
        turn_id=str(implementation_document["turn_id"]),
        status="completed",
        error=None,
        final_response=str(implementation_document["final_response"]),
        item_count=int(implementation_document["item_count"]),
        started_at=int(implementation_document["started_at"]),
        completed_at=int(implementation_document["completed_at"]),
    )
    return _finish_codex_implementation(
        control_root=control_root,
        config=config,
        brief_digest=brief_digest,
        run=run,
        store=store,
        ticket_plan=ticket_plan,
        workspace_info=workspace_info,
        result=result,
        finalize_run=False,
        validated_review=validated,
    )


def _reconcile_completed_ticket_turn(*, control_root: Path, config: RunnerConfig, run: RunRecord,
                                     brief_digest: str, store: Store, worker: dict[str, object],
                                     thread_id: str, turn_id: str) -> RunRecord:
    """Consume a completed ticket turn that outlived the Runner process.

    The SDK turn output is already durable in the run artifact. Reconstructing
    the worker result from that receipt lets recovery finish the stage without
    replaying a completed external turn.
    """
    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    candidates: list[dict[str, object]] = []
    for path in sorted(artifact_directory.glob("codex_ticket_planning-*.json")):
        try:
            document = load_json(path)
        except RunnerError:
            continue
        if document.get("thread_id") == thread_id and document.get("turn_id") == turn_id:
            candidates.append(document)
    if len(candidates) != 1:
        raise RunnerError("recovery_blocked", "completed ticket turn lacks a unique persisted worker result")
    persisted = candidates[0]
    if persisted.get("status") != "completed" or persisted.get("error") is not None:
        raise RunnerError("recovery_blocked", "completed ticket turn has no successful persisted result")
    final_response = persisted.get("final_response")
    if not isinstance(final_response, str) or not final_response.strip():
        raise RunnerError("recovery_blocked", "completed ticket turn lacks useful persisted output")
    worker_id = str(worker.get("worker_id") or "")
    prefix = f"codex_sdk:{run.run_id}:codex_ticket_planning:"
    if not worker_id.startswith(prefix):
        raise RunnerError("recovery_blocked", "completed ticket worker identity is not scoped to a SPEC")
    spec_key = worker_id[len(prefix):]
    if not spec_key:
        raise RunnerError("recovery_blocked", "completed ticket worker has no SPEC identity")
    plan_path = artifact_directory / "spec-plan.json"
    if not plan_path.is_file():
        raise RunnerError("spec_plan_missing", "ticket planning recovery has no persisted SpecPlan")
    full_plan = load_json(plan_path)
    specs = full_plan.get("specs")
    if not isinstance(specs, list):
        raise RunnerError("invalid_spec_plan", "ticket planning recovery requires a SPEC list")
    selected = [item for item in specs if isinstance(item, dict) and item.get("key") == spec_key]
    if len(selected) != 1:
        raise RunnerError("ticket_spec_missing", "ticket planning recovery could not match its persisted SPEC")
    item_count = persisted.get("item_count", 0)
    if isinstance(item_count, bool) or not isinstance(item_count, int) or item_count < 0:
        raise RunnerError("recovery_blocked", "completed ticket turn has an invalid persisted item count")
    started_at = persisted.get("started_at")
    completed_at = persisted.get("completed_at")
    if started_at is not None and (isinstance(started_at, bool) or not isinstance(started_at, int)):
        raise RunnerError("recovery_blocked", "completed ticket turn has an invalid persisted start time")
    if completed_at is not None and (isinstance(completed_at, bool) or not isinstance(completed_at, int)):
        raise RunnerError("recovery_blocked", "completed ticket turn has an invalid persisted completion time")
    result = CodexWorkerResult(
        thread_id=thread_id,
        turn_id=turn_id,
        status=str(persisted.get("status") or "unknown"),
        error=(str(persisted["error"]) if persisted.get("error") is not None else None),
        final_response=final_response,
        item_count=item_count,
        started_at=started_at,
        completed_at=completed_at,
        approval_mode=str(persisted.get("approval_mode") or "deny_all"),
        skill_observation=persisted.get("skill_observation") if isinstance(persisted.get("skill_observation"), dict) else None,
    )
    document = _planning_response(
        result=result, control_root=control_root, config=config, run=run, store=store,
        operation_id=f"tickets:{run.run_id}:{spec_key}", step_name="codex_ticket_planning",
        worker_id=worker_id, brief_digest=brief_digest, persist_result=False,
        allow_ticket_confirmation=True,
    )
    if isinstance(document, RunRecord):
        store.append_event(
            run_id=run.run_id,
            event_key=f"recovery:{run.run_id}:completed-ticket-turn:{turn_id}",
            event_type="completed_sdk_turn_reconciled",
            payload={"step": "codex_ticket_planning", "spec_key": spec_key,
                     "thread_id": thread_id, "turn_id": turn_id},
        )
        return document
    base_sha = git_sha(config.repository_path, config.target_ref, timeout_seconds=config.git_timeout_seconds)
    recovered = _persist_ticket_plan(
        control_root=control_root, config=config, run=run, store=store,
        document=document, spec=selected[0], base_sha=base_sha,
        operation_id=f"tickets:{run.run_id}:{spec_key}", step_name="codex_ticket_planning",
        worker_id=worker_id, result=result,
    )
    store.append_event(
        run_id=run.run_id,
        event_key=f"recovery:{run.run_id}:completed-ticket-turn:{turn_id}",
        event_type="completed_sdk_turn_reconciled",
        payload={"step": "codex_ticket_planning", "spec_key": spec_key,
                 "thread_id": thread_id, "turn_id": turn_id},
    )
    return recovered


def _adopt_existing_ticket_plan(*, control_root: Path, config: RunnerConfig, run: RunRecord,
                                store: Store, worker: dict[str, object]) -> RunRecord | None:
    """Reuse a durable TicketPlan after a later duplicate planner attempt failed."""
    worker_id = str(worker.get("worker_id") or "")
    prefix = f"codex_sdk:{run.run_id}:codex_ticket_planning:"
    if not worker_id.startswith(prefix):
        return None
    spec_key = worker_id[len(prefix):]
    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    ticket_path = artifact_directory / f"ticket-plan-{spec_key}.json"
    if not ticket_path.is_file():
        return None
    ticket = load_json(ticket_path)
    base_sha = git_sha(config.repository_path, config.target_ref, timeout_seconds=config.git_timeout_seconds)
    validated_ticket = validate_ticket_plan(ticket, expected_spec_key=spec_key, expected_base_sha=base_sha)
    plan_path = artifact_directory / "spec-plan.json"
    if not plan_path.is_file():
        return None
    full_plan = load_json(plan_path)
    specs = full_plan.get("specs")
    if not isinstance(specs, list):
        return None
    selected = [item for item in specs if isinstance(item, dict) and item.get("key") == spec_key]
    if len(selected) != 1:
        return None
    if (validated_ticket.get("spec_title") != str(selected[0].get("title") or spec_key)
            or validated_ticket.get("spec_body") != str(selected[0].get("body") or "")):
        return None
    current_turn_id = str(worker.get("external_turn_id") or "")
    prior_results: list[dict[str, object]] = []
    for path in sorted(artifact_directory.glob("codex_ticket_planning-*.json")):
        try:
            result = load_json(path)
        except RunnerError:
            continue
        if (result.get("status") == "completed" and result.get("error") is None
                and isinstance(result.get("thread_id"), str)
                and isinstance(result.get("turn_id"), str)
                and result.get("turn_id") != current_turn_id
                and isinstance(result.get("final_response"), str)
                and result.get("final_response")):
            try:
                prior_document = json.loads(str(result["final_response"]))
            except json.JSONDecodeError:
                continue
            if not isinstance(prior_document, dict):
                continue
            try:
                questions = prior_document.get("questions")
                if not isinstance(questions, list) or questions:
                    continue
                if prior_document.get("outcome") != "planned":
                    if not isinstance(prior_document.get("tickets"), list) or not prior_document["tickets"]:
                        continue
                    prior_document["outcome"] = "planned"
                prior_document.update({
                    "schema_version": "spec-runner-ticket-plan/v1", "spec_key": spec_key,
                    "base_sha": base_sha,
                    "spec_title": str(validated_ticket.get("spec_title") or spec_key),
                    "spec_body": str(validated_ticket.get("spec_body") or ""),
                })
                prior_ticket = validate_ticket_plan(
                    prior_document, expected_spec_key=spec_key, expected_base_sha=base_sha
                )
            except RunnerError:
                continue
            if prior_ticket.get("digest") == validated_ticket.get("digest"):
                candidate = dict(result)
                candidate["_ticket_document"] = prior_document
                prior_results.append(candidate)
    identities = {(str(item["thread_id"]), str(item["turn_id"])) for item in prior_results}
    if len(identities) != 1:
        return None
    persisted = prior_results[0]
    item_count = persisted.get("item_count", 0)
    if isinstance(item_count, bool) or not isinstance(item_count, int) or item_count < 0:
        return None
    started_at = persisted.get("started_at")
    completed_at = persisted.get("completed_at")
    if (started_at is not None and (isinstance(started_at, bool) or not isinstance(started_at, int))) or (
            completed_at is not None and (isinstance(completed_at, bool) or not isinstance(completed_at, int))):
        return None
    result = CodexWorkerResult(
        thread_id=str(persisted["thread_id"]),
        turn_id=str(persisted["turn_id"]),
        status="completed",
        error=None,
        final_response=str(persisted["final_response"]),
        item_count=item_count,
        started_at=started_at,
        completed_at=completed_at,
        approval_mode=str(persisted.get("approval_mode") or "deny_all"),
        skill_observation=persisted.get("skill_observation") if isinstance(persisted.get("skill_observation"), dict) else None,
    )
    adopted = _persist_ticket_plan(
        control_root=control_root, config=config, run=run, store=store,
        document=persisted["_ticket_document"], spec=selected[0], base_sha=base_sha,
        operation_id=f"tickets:{run.run_id}:{spec_key}", step_name="codex_ticket_planning",
        worker_id=worker_id, result=result,
    )
    store.append_event(
        run_id=run.run_id,
        event_key=f"recovery:{run.run_id}:existing-ticket-plan:{spec_key}",
        event_type="existing_ticket_plan_reconciled",
        payload={"spec_key": spec_key, "thread_id": prior_results[0]["thread_id"],
                 "turn_id": prior_results[0]["turn_id"]},
    )
    return adopted


def _implementation_spec_key(*, run: RunRecord, worker: dict[str, object]) -> str:
    prefix = f"codex_sdk:{run.run_id}:codex_implementation:"
    worker_id = str(worker.get("worker_id") or "")
    if not worker_id.startswith(prefix):
        raise RunnerError("recovery_blocked", "implementation worker identity is not scoped to a SPEC")
    spec_key = worker_id[len(prefix):]
    if not spec_key:
        raise RunnerError("recovery_blocked", "implementation worker has no SPEC identity")
    return spec_key


def _implementation_workspace_path(*, control_root: Path, config: RunnerConfig,
                                   run: RunRecord, spec_key: str) -> Path:
    """Read the worker's exact managed cwd without creating a replacement."""
    workspace_root = (control_root / "delivery-workspaces").resolve()
    safe_key = "".join(char if char.isalnum() or char in "._-" else "-" for char in spec_key)
    manifest = workspace_root / f"{safe_key}-{run.run_id[:8]}.manifest.json"
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError("recovery_blocked", "implementation recovery has no readable workspace manifest") from exc
    if not isinstance(document, dict) or document.get("run_id") != run.run_id or document.get("spec_key") != spec_key:
        raise RunnerError("recovery_blocked", "implementation workspace manifest identity does not match the failed run")
    workspace = Path(str(document.get("workspace", ""))).resolve()
    if workspace_root not in workspace.parents or not workspace.is_dir():
        raise RunnerError("recovery_blocked", "implementation workspace is missing or outside the managed workspace root")
    if Path(str(document.get("repository", ""))).resolve() != config.repository_path.resolve():
        raise RunnerError("recovery_blocked", "implementation workspace manifest repository does not match the configured repository")
    return workspace


def _candidate_workspace_info(*, control_root: Path, config: RunnerConfig,
                              run: RunRecord, spec_key: str,
                              candidate_sha: str) -> dict[str, object]:
    """Read the unique clean managed manifest for an exact candidate SHA."""
    workspace_root = (control_root / "delivery-workspaces").resolve()
    matches: list[dict[str, object]] = []
    for manifest_path in sorted(workspace_root.glob("*.manifest.json")):
        try:
            document = load_json(manifest_path)
        except RunnerError:
            continue
        if (document.get("run_id") != run.run_id
                or document.get("spec_key") != spec_key
                or Path(str(document.get("repository", ""))).resolve() != config.repository_path.resolve()):
            continue
        workspace = Path(str(document.get("workspace", ""))).resolve()
        if workspace_root not in workspace.parents or not workspace.is_dir():
            continue
        try:
            if (git_sha(workspace, timeout_seconds=config.git_timeout_seconds) != candidate_sha
                    or _git_checked(workspace, "status", "--porcelain", timeout_seconds=config.git_timeout_seconds)):
                continue
        except RunnerError:
            continue
        matches.append({**document, "manifest": os.fspath(manifest_path), "workspace": os.fspath(workspace)})
    if len(matches) != 1:
        raise RunnerError("recovery_blocked", "candidate has no unique clean managed workspace manifest")
    return matches[0]


def _reconcile_completed_implementation_turn(*, control_root: Path, config: RunnerConfig,
                                             run: RunRecord, brief_digest: str, store: Store,
                                             worker: dict[str, object], thread_id: str,
                                             turn_id: str, finalize_run: bool = False) -> dict[str, object]:
    """Consume a completed implementation turn without replaying the worker."""
    spec_key = _implementation_spec_key(run=run, worker=worker)
    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    result_path = artifact_directory / f"implementation-{spec_key}.json"
    try:
        persisted = load_json(result_path)
    except RunnerError as exc:
        raise RunnerError("recovery_blocked", "completed implementation turn lacks a readable persisted result") from exc
    if persisted.get("thread_id") != thread_id or persisted.get("turn_id") != turn_id:
        raise RunnerError("recovery_blocked", "persisted implementation result identity does not match the completed SDK turn")
    if persisted.get("status") != "completed" or persisted.get("error") is not None:
        raise RunnerError("recovery_blocked", "completed implementation turn has no successful persisted result")
    final_response = persisted.get("final_response")
    if not isinstance(final_response, str) or not final_response.strip():
        raise RunnerError("recovery_blocked", "completed implementation turn lacks useful persisted output")
    item_count = persisted.get("item_count", 0)
    if isinstance(item_count, bool) or not isinstance(item_count, int) or item_count < 0:
        raise RunnerError("recovery_blocked", "completed implementation turn has an invalid persisted item count")
    started_at = persisted.get("started_at")
    completed_at = persisted.get("completed_at")
    if (started_at is not None and (isinstance(started_at, bool) or not isinstance(started_at, int))) or (
            completed_at is not None and (isinstance(completed_at, bool) or not isinstance(completed_at, int))):
        raise RunnerError("recovery_blocked", "completed implementation turn has invalid persisted timestamps")
    result = CodexWorkerResult(
        thread_id=thread_id,
        turn_id=turn_id,
        status="completed",
        error=None,
        final_response=final_response,
        item_count=item_count,
        started_at=started_at,
        completed_at=completed_at,
        approval_mode=str(persisted.get("approval_mode") or "deny_all"),
        skill_observation=persisted.get("skill_observation") if isinstance(persisted.get("skill_observation"), dict) else None,
    )
    ticket_path = artifact_directory / f"ticket-plan-{spec_key}.json"
    if not ticket_path.is_file():
        raise RunnerError("ticket_plan_missing", f"SPEC {spec_key} has no persisted TicketPlan")
    workspace = _implementation_workspace_path(control_root=control_root, config=config, run=run, spec_key=spec_key)
    workspace_info = prepare_workspace(
        repository=config.repository_path, workspace_root=control_root / "delivery-workspaces",
        run_id=run.run_id, spec_key=spec_key, base_ref=config.target_ref,
        git_timeout_seconds=config.git_timeout_seconds,
    )
    if Path(str(workspace_info["workspace"])).resolve() != workspace:
        raise RunnerError("recovery_blocked", "implementation workspace adoption changed the persisted workspace identity")
    ticket_plan = validate_ticket_plan(
        load_json(ticket_path), expected_spec_key=spec_key,
        expected_base_sha=str(workspace_info["base_sha"]),
    )
    input_gate = _persist_implementation_input_gate(
        control_root=control_root, config=config, run=run, store=store, result=result,
        brief_digest=brief_digest, operation_id=f"implementation:{run.run_id}:{spec_key}",
        step_name="codex_implementation",
        worker_id=str(worker["worker_id"]), spec_key=spec_key,
    )
    if input_gate is not None:
        store.append_event(
            run_id=run.run_id,
            event_key=f"recovery:{run.run_id}:completed-implementation-input:{turn_id}",
            event_type="completed_sdk_turn_reconciled",
            payload={"step": "codex_implementation", "spec_key": spec_key,
                     "thread_id": thread_id, "turn_id": turn_id, "state": "needs_input"},
        )
        return store.public_status(run.run_id)
    recovered = _finish_codex_implementation(
        control_root=control_root, config=config, brief_digest=brief_digest, run=run, store=store,
        ticket_plan=ticket_plan, workspace_info=workspace_info, result=result, finalize_run=finalize_run,
    )
    store.append_event(
        run_id=run.run_id,
        event_key=f"recovery:{run.run_id}:completed-implementation-turn:{turn_id}",
        event_type="completed_sdk_turn_reconciled",
        payload={"step": "codex_implementation", "spec_key": spec_key,
                 "thread_id": thread_id, "turn_id": turn_id, "state": recovered.get("state")},
    )
    return recovered


def _repair_spec_key(*, run: RunRecord, worker: dict[str, object]) -> str:
    prefix = f"codex_sdk:{run.run_id}:codex_repair:"
    worker_id = str(worker.get("worker_id") or "")
    if not worker_id.startswith(prefix) or not worker_id[len(prefix):]:
        raise RunnerError("recovery_blocked", "repair worker identity is not scoped to a SPEC")
    return worker_id[len(prefix):]


def _reconcile_repair_turn(*, control_root: Path, config: RunnerConfig,
                           run: RunRecord, brief_digest: str, store: Store,
                           worker: dict[str, object], turn_status: str) -> dict[str, object]:
    """Consume a completed repair result or safely retry a failed turn."""
    spec_key = _repair_spec_key(run=run, worker=worker)
    thread_id = str(worker.get("external_thread_id") or "")
    turn_id = str(worker.get("external_turn_id") or "")
    if not thread_id or not turn_id:
        raise RunnerError("recovery_blocked", "failed repair turn has no recoverable thread identity")
    workspace = _implementation_workspace_path(
        control_root=control_root, config=config, run=run, spec_key=spec_key,
    )
    try:
        inspection = CodexAdapter().read_thread(thread_id=thread_id, repository_path=workspace)
    except RunnerError as exc:
        raise RunnerError(
            "recovery_blocked",
            "the failed repair thread could not be reconciled; inspect it before retry",
            details={"inspection_error": exc.code},
        ) from exc
    turns = inspection.get("turns") if isinstance(inspection, dict) else None
    turn_count = inspection.get("turn_count") if isinstance(inspection, dict) else None
    failed_repair_turns = sum(
        1 for item in turns
        if isinstance(item, dict) and item.get("status") in {"failed", "interrupted"}
    ) if isinstance(turns, list) else 0
    terminal_statuses = {"failed", "interrupted"} if turn_status != "completed" else {"completed"}
    terminal = (
        isinstance(inspection, dict)
        and inspection.get("started_turn") is False
        and inspection.get("thread_id") == thread_id
        and inspection.get("thread_status") == "idle"
        and inspection.get("active_flags") == []
        and isinstance(turns, list)
        and isinstance(turn_count, int)
        and not isinstance(turn_count, bool)
        and turn_count == len(turns)
        and bool(turns)
        and isinstance(turns[-1], dict)
        and turns[-1].get("turn_id") == turn_id
        and turns[-1].get("status") in terminal_statuses
    )
    if not terminal:
        raise RunnerError("recovery_blocked", "the failed repair turn has no uniquely recoverable terminal result")

    artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
    review_files = sorted(artifact_directory.glob(f"review-{spec_key}-*.json"))
    if not review_files:
        raise RunnerError("recovery_blocked", "failed repair turn has no persisted review findings")
    review = load_json(review_files[-1])
    findings = review.get("blocking")
    if not isinstance(findings, list) or not findings:
        raise RunnerError("recovery_blocked", "failed repair turn has no persisted blocking findings")
    ticket_path = artifact_directory / f"ticket-plan-{spec_key}.json"
    if not ticket_path.is_file():
        raise RunnerError("ticket_plan_missing", f"SPEC {spec_key} has no persisted TicketPlan")
    ticket_plan = validate_ticket_plan(
        load_json(ticket_path), expected_spec_key=spec_key,
        expected_base_sha=git_sha(config.repository_path, config.target_ref, timeout_seconds=config.git_timeout_seconds),
    )
    repair_prefix = f"codex_sdk:{run.run_id}:codex_repair:{spec_key}"
    if turn_status != "completed":
        start_new_thread = failed_repair_turns >= 2
        candidate_sha, _ = _repair_candidate(
            control_root=control_root, config=config, brief_digest=brief_digest,
            run=run, store=store, ticket_plan=ticket_plan, workspace=workspace,
            findings=findings, implementation_thread=thread_id,
            artifact_directory=artifact_directory, start_new_thread=start_new_thread,
        )
        replacement_worker = store.workers_for_run(run.run_id)[-1]
        replacement_thread = str(replacement_worker.get("external_thread_id") or "")
        if not replacement_thread:
            raise RunnerError("recovery_blocked", "replacement repair has no durable thread identity")
        if start_new_thread:
            archive = CodexAdapter().archive_and_readback(thread_id=thread_id, repository_path=workspace)
            store.append_event(
                run_id=run.run_id,
                event_key=f"recovery:{run.run_id}:repair-thread-replaced:{replacement_thread}",
                event_type="repair_thread_replaced",
                payload={"spec_key": spec_key, "previous_thread_id": thread_id,
                         "replacement_thread_id": replacement_thread,
                         "failed_repair_turns": failed_repair_turns,
                         "reason": "repeated_terminal_repair_failure", "archive": archive},
            )
        store.append_event(
            run_id=run.run_id,
            event_key=f"recovery:{run.run_id}:failed-repair-turn:{turn_id}:{candidate_sha}",
            event_type="failed_sdk_turn_reconciled",
            payload={"step": "codex_repair", "spec_key": spec_key,
                     "thread_id": thread_id, "turn_id": turn_id, "turn_status": turn_status},
        )
        implementation_prefix = f"codex_sdk:{run.run_id}:codex_implementation:{spec_key}"
        implementation_workers = [
            item for item in store.workers_for_run(run.run_id)
            if str(item.get("worker_id") or "") == implementation_prefix
        ]
        implementation_worker = implementation_workers[-1] if implementation_workers else None
        if implementation_worker is None:
            raise RunnerError("recovery_blocked", "repair recovered but the implementation worker identity is missing")
        implementation_thread = str(implementation_worker.get("external_thread_id") or "")
        implementation_turn = str(implementation_worker.get("external_turn_id") or "")
        if not implementation_thread or not implementation_turn:
            raise RunnerError("recovery_blocked", "repair recovered but the implementation turn identity is missing")
        return _reconcile_completed_implementation_turn(
            control_root=control_root, config=config, run=run,
            brief_digest=brief_digest, store=store, worker=implementation_worker,
            thread_id=implementation_thread, turn_id=implementation_turn,
        )

    result_path = artifact_directory / f"repair-worker-{spec_key}-{hashlib.sha256(turn_id.encode('utf-8')).hexdigest()}.json"
    try:
        persisted = load_json(result_path)
    except RunnerError as exc:
        raise RunnerError("recovery_blocked", "repair turn has no persisted worker result") from exc
    if (persisted.get("thread_id") != thread_id or persisted.get("turn_id") != turn_id
            or persisted.get("status") != "completed" or persisted.get("error") is not None):
        raise RunnerError("recovery_blocked", "repair worker result identity or terminal status does not match")
    final_response = persisted.get("final_response")
    if not isinstance(final_response, str) or not final_response.strip():
        raise RunnerError("recovery_blocked", "repair turn has no useful structured result")
    result = CodexWorkerResult(
        thread_id=thread_id, turn_id=turn_id, status="completed", error=None,
        final_response=final_response,
        item_count=int(persisted.get("item_count") or 0),
        started_at=persisted.get("started_at"), completed_at=persisted.get("completed_at"),
        approval_mode=str(persisted.get("approval_mode") or "deny_all"),
        skill_observation=persisted.get("skill_observation") if isinstance(persisted.get("skill_observation"), dict) else None,
    )
    try:
        document = json.loads(final_response)
    except json.JSONDecodeError as exc:
        raise RunnerError("recovery_blocked", "repair result is not valid JSON") from exc
    blocked_candidate = (
        isinstance(document, dict)
        and document.get("outcome") in {"blocked", "completed"}
        and isinstance(document.get("blockers"), list)
        and bool(document.get("blockers"))
        and document.get("questions") == []
        and git_sha(workspace, timeout_seconds=config.git_timeout_seconds) != str(ticket_plan.get("base_sha") or "")
    )
    if blocked_candidate and turn_status == "completed":
        operation = str(store.operations_for_run(run.run_id)[-1]["operation_id"])
        try:
            candidate_sha, _ = _finish_repair_candidate_result(
                control_root=control_root, config=config, run=run, store=store,
                ticket_plan=ticket_plan, workspace=workspace, result=result,
                operation=operation, worker=repair_prefix,
                artifact_directory=artifact_directory, adopt_existing=True, allow_blocked=True,
            )
        except RunnerError as exc:
            if exc.code != "candidate_verification_failed":
                raise
            candidate_sha, _ = _retry_repair_after_candidate_failure(
                control_root=control_root, config=config, brief_digest=brief_digest,
                run=run, store=store, ticket_plan=ticket_plan, workspace=workspace,
                findings=findings, implementation_thread=thread_id,
                artifact_directory=artifact_directory, failed_operation=operation,
                failed_result=result, failure=exc,
            )
        store.append_event(
            run_id=run.run_id,
            event_key=f"recovery:{run.run_id}:blocked-worker-candidate:{candidate_sha}",
            event_type="blocked_worker_candidate_verified",
            payload={"spec_key": spec_key, "thread_id": thread_id, "turn_id": turn_id,
                     "candidate_sha": candidate_sha},
        )
        return _resume_after_repair_candidate(
            control_root=control_root, config=config, brief_digest=brief_digest,
            run=run, store=store, spec_key=spec_key,
        )
    if (
        not isinstance(document, dict)
        or document.get("outcome") != "completed"
        or document.get("blockers") != []
        or document.get("questions") != []
    ):
        if turn_status == "completed" and isinstance(document, dict):
            operation = str(store.operations_for_run(run.run_id)[-1]["operation_id"])
            store.complete_codex_stage(
                run.run_id, operation, thread_id=thread_id, turn_id=turn_id,
                state="worker_blocked", step_name="codex_repair", worker_id=repair_prefix,
            )
            blocked_findings = [
                *findings,
                {
                    "severity": "high",
                    "status": "open",
                    "description": "A prior trusted candidate verification failed. Run the exact acceptance command and fix every failing test; do not only report an environment blocker.",
                    "worker_blockers": document.get("blockers", []),
                    "worker_questions": document.get("questions", []),
                },
            ]
            try:
                verify_candidate(
                    workspace=workspace,
                    candidate_sha=git_sha(workspace, timeout_seconds=config.git_timeout_seconds),
                    acceptance_version=str(ticket_plan["digest"]),
                    checks=list(config.acceptance_checks),
                    acceptance=list(config.acceptance_ids),
                    git_timeout_seconds=config.git_timeout_seconds,
                )
            except RunnerError as verification_error:
                if verification_error.code == "candidate_verification_failed":
                    blocked_findings.append(_candidate_verification_finding(verification_error))
            _repair_candidate(
                control_root=control_root, config=config, brief_digest=brief_digest,
                run=run, store=store, ticket_plan=ticket_plan, workspace=workspace,
                findings=blocked_findings, implementation_thread=thread_id,
                artifact_directory=artifact_directory,
            )
            return _resume_after_repair_candidate(
                control_root=control_root, config=config, brief_digest=brief_digest,
                run=run, store=store, spec_key=spec_key,
            )
        raise RunnerError("implementation_not_ready", "implementation is incomplete or requires input")
    artifacts = document.get("artifacts") if isinstance(document, dict) else None
    missing_artifacts: list[str] = []
    if not isinstance(artifacts, list):
        missing_artifacts = ["<artifacts:not-a-list>"]
    else:
        for item in artifacts:
            if not isinstance(item, str) or not item.strip():
                missing_artifacts.append("<artifact:invalid>")
                continue
            relative = Path(item)
            path = workspace / relative
            if (relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts
                    or workspace.resolve() not in path.resolve().parents or not path.is_file()
                    or any(part.is_symlink() for part in [path, *path.parents])):
                missing_artifacts.append(item)
    if missing_artifacts:
        if turn_status == "completed":
            # Never replay a completed turn. Use the same active implementation
            # thread to ask for a contract-correct receipt over the existing files.
            receipt_operation = f"repair-receipt:{run.run_id}:{spec_key}:{turn_id}"
            store.begin_stage(
                run.run_id, step_name="codex_repair", operation_id=receipt_operation,
                backend_kind="codex_sdk", worker_id=repair_prefix,
            )
            receipt_result = _run_worker(
                adapter=CodexAdapter(), phase="implement", config=config,
                prompt=("The previous repair turn completed its workspace edits, but its structured artifacts field "
                        "included invalid entries. Do not change any files. Return outcome=completed and list only "
                        "real workspace-relative file paths in artifacts; include no prose or test summaries there. "
                        "Preserve blockers/questions as empty arrays.\n\nInvalid entries: "
                        + json.dumps(missing_artifacts, ensure_ascii=False)),
                model=config.model_name, effort=config.effort, thread_id=thread_id,
                repository_path=workspace,
                trusted={"brief_digest": brief_digest, "stage": "codex_repair_receipt", "spec_key": spec_key,
                         "prior_turn_id": turn_id},
                on_turn_started=lambda next_thread, next_turn: _record_codex_turn_started(
                    store, run_id=run.run_id, operation_id=receipt_operation,
                    step_name="codex_repair", worker_id=repair_prefix,
                    thread_id=next_thread, turn_id=next_turn),
                control_state=lambda: _read_control_state(control_root=control_root, run_id=run.run_id),
                schema=IMPLEMENTATION_SCHEMA,
            )
            receipt_key = hashlib.sha256(receipt_result.turn_id.encode("utf-8")).hexdigest()
            (artifact_directory / f"repair-worker-{spec_key}-{receipt_key}.json").write_text(
                json.dumps(receipt_result.public(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8", newline="\n",
            )
            if receipt_result.thread_id != thread_id:
                raise RunnerError("repair_owner_changed", "artifact receipt must resume the existing repair thread")
            try:
                _finish_repair_candidate_result(
                    control_root=control_root, config=config, run=run, store=store,
                    ticket_plan=ticket_plan, workspace=workspace, result=receipt_result,
                    operation=receipt_operation, worker=repair_prefix,
                    artifact_directory=artifact_directory, adopt_existing=True,
                )
            except RunnerError as exc:
                if exc.code != "candidate_verification_failed":
                    raise
                _retry_repair_after_candidate_failure(
                    control_root=control_root, config=config, brief_digest=brief_digest,
                    run=run, store=store, ticket_plan=ticket_plan, workspace=workspace,
                    findings=findings, implementation_thread=thread_id,
                    artifact_directory=artifact_directory, failed_operation=receipt_operation,
                    failed_result=receipt_result, failure=exc,
                )
            return {"created": False, **_resume_after_repair_candidate(
                control_root=control_root, config=config, brief_digest=brief_digest,
                run=run, store=store, spec_key=spec_key,
            )}

    if turn_status != "completed":
        raise RunnerError("recovery_blocked", "failed repair turn completed without its persisted result")
    operation = str(store.operations_for_run(run.run_id)[-1]["operation_id"])
    try:
        candidate_sha, _ = _finish_repair_candidate_result(
            control_root=control_root, config=config, run=run, store=store,
            ticket_plan=ticket_plan, workspace=workspace, result=result,
            operation=operation, worker=repair_prefix, artifact_directory=artifact_directory,
            adopt_existing=True,
        )
    except RunnerError as exc:
        if exc.code != "candidate_verification_failed":
            raise
        candidate_sha, _ = _retry_repair_after_candidate_failure(
            control_root=control_root, config=config, brief_digest=brief_digest,
            run=run, store=store, ticket_plan=ticket_plan, workspace=workspace,
            findings=findings, implementation_thread=thread_id,
            artifact_directory=artifact_directory, failed_operation=operation,
            failed_result=result, failure=exc,
        )
    store.append_event(
        run_id=run.run_id,
        event_key=f"recovery:{run.run_id}:failed-repair-turn:{turn_id}:{candidate_sha}",
        event_type="failed_sdk_turn_reconciled",
        payload={"step": "codex_repair", "spec_key": spec_key,
                 "thread_id": thread_id, "turn_id": turn_id,
                 "turn_status": "failed"},
    )
    return _resume_after_repair_candidate(
        control_root=control_root, config=config, brief_digest=brief_digest,
        run=run, store=store, spec_key=spec_key,
    )


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
        if run.state in {"running", "cleanup_pending", "failed"} and run.current_step == "codex_planning":
            workers = store.workers_for_run(run.run_id)
            worker_id = f"codex_sdk:{run.run_id}:codex_planning"
            stage_workers = [
                worker for worker in workers
                if worker.get("backend_kind") == "codex_sdk"
                and worker.get("state") in {"running", "failed", "interrupted", "rejected"}
                and worker.get("worker_id") == worker_id
            ]
            worker = stage_workers[-1] if stage_workers else None
            thread_id = str(worker.get("external_thread_id") or "") if worker else ""
            turn_id = str(worker.get("external_turn_id") or "") if worker else ""
            if worker is None or not thread_id or not turn_id:
                raise RunnerError("recovery_blocked", "the planning stage has no uniquely identified worker result")
            try:
                inspection = CodexAdapter().read_thread(
                    thread_id=thread_id,
                    repository_path=config.repository_path,
                )
            except RunnerError as exc:
                raise RunnerError(
                    "recovery_blocked",
                    "the persisted planning thread could not be reconciled; inspect it before retry",
                    details={"inspection_error": exc.code},
                ) from exc
            turns = inspection.get("turns") if isinstance(inspection, dict) else None
            turn_count = inspection.get("turn_count") if isinstance(inspection, dict) else None
            terminal_completed = (
                isinstance(inspection, dict)
                and inspection.get("started_turn") is False
                and inspection.get("thread_id") == thread_id
                and inspection.get("thread_status") == "idle"
                and inspection.get("active_flags") == []
                and isinstance(turns, list)
                and isinstance(turn_count, int)
                and not isinstance(turn_count, bool)
                and turn_count == len(turns)
                and bool(turns)
                and isinstance(turns[-1], dict)
                and turns[-1].get("turn_id") == turn_id
                and turns[-1].get("status") == "completed"
            )
            if not terminal_completed:
                if run.state != "failed":
                    raise RunnerError("recovery_blocked", "the planning turn has no uniquely recoverable terminal result")
            elif _planning_turn_requires_retry(
                control_root=control_root, config=config, run=run,
                thread_id=thread_id, turn_id=turn_id, brief_digest=brief_digest,
            ):
                store.append_event(
                    run_id=run.run_id,
                    event_key=f"recovery:{run.run_id}:planning-semantic-retry:{turn_id}:{worker['updated_at']}",
                    event_type="invalid_planning_turn_reconciled",
                    payload={"step": run.current_step, "thread_id": thread_id, "turn_id": turn_id},
                )
                resumed = _resume_codex_stage(
                    control_root=control_root, config=config, run=run,
                    brief=brief, brief_digest=brief_digest, store=store,
                    thread_id=thread_id,
                )
                return {"created": False, **resumed}
            elif run.state != "failed":
                raise RunnerError(
                    "recovery_blocked",
                    "completed planning turn has no persisted validated SpecPlan",
                )
        if run.state == "failed":
            resumable_steps = {
                "codex_example",
                "codex_grill",
                "codex_planning",
                "codex_ticket_planning",
                "codex_implementation",
                "codex_second",
            }
            if run.current_step not in resumable_steps:
                raise RunnerError("recovery_blocked", "the SDK operation has no uniquely recoverable external result; inspect the persisted thread/turn before retry")
            workers = store.workers_for_run(run.run_id)
            worker_prefix = f"codex_sdk:{run.run_id}"
            if run.current_step == "codex_example":
                expected_worker_id = worker_prefix
                matches_stage = lambda worker_id: worker_id == expected_worker_id
            elif run.current_step == "codex_ticket_planning":
                matches_stage = lambda worker_id: worker_id.startswith(f"{worker_prefix}:codex_ticket_planning:")
            elif run.current_step == "codex_implementation":
                matches_stage = lambda worker_id: worker_id.startswith(f"{worker_prefix}:codex_implementation:")
            else:
                expected_worker_id = f"{worker_prefix}:{run.current_step}"
                matches_stage = lambda worker_id: worker_id == expected_worker_id
            stage_workers = [
                worker for worker in workers
                if worker.get("backend_kind") == "codex_sdk"
                and worker.get("state") == "failed"
                and matches_stage(str(worker.get("worker_id", "")))
            ]
            worker = stage_workers[-1] if stage_workers else None
            thread_id = str(worker.get("external_thread_id") or "") if worker else ""
            turn_id = str(worker.get("external_turn_id") or "") if worker else ""
            if not thread_id or not turn_id:
                raise RunnerError("recovery_blocked", "the SDK operation has no uniquely recoverable external result; inspect the persisted thread/turn before retry")
            if run.current_step == "codex_ticket_planning" and worker is not None:
                adopted = _adopt_existing_ticket_plan(
                    control_root=control_root, config=config, run=run, store=store, worker=worker,
                )
                if adopted is not None:
                    return {"created": False, **store.public_status(adopted.run_id)}
            inspection_repository = config.repository_path
            if run.current_step == "codex_implementation" and worker is not None:
                spec_key = _implementation_spec_key(run=run, worker=worker)
                inspection_repository = _implementation_workspace_path(
                    control_root=control_root, config=config, run=run, spec_key=spec_key,
                )
            try:
                inspection = CodexAdapter().read_thread(
                    thread_id=thread_id,
                    repository_path=inspection_repository,
                )
            except RunnerError as exc:
                raise RunnerError(
                    "recovery_blocked",
                    "the persisted SDK thread could not be reconciled; inspect it before retry",
                    details={"inspection_error": exc.code},
                ) from exc
            if not isinstance(inspection, dict):
                raise RunnerError("recovery_blocked", "the SDK operation has no uniquely recoverable external result; inspect the persisted thread/turn before retry")
            turns = inspection.get("turns")
            turn_count = inspection.get("turn_count")
            turn_status = turns[-1].get("status") if isinstance(turns, list) and turns and isinstance(turns[-1], dict) else None
            if run.current_step == "codex_implementation" and turn_status == "completed":
                recovered = _reconcile_completed_implementation_turn(
                    control_root=control_root, config=config, run=run, brief_digest=brief_digest,
                    store=store, worker=worker, thread_id=thread_id, turn_id=turn_id,
                )
                return {"created": False, **recovered}
            reconciled = (
                inspection.get("started_turn") is False
                and inspection.get("thread_id") == thread_id
                and inspection.get("thread_status") == "idle"
                and inspection.get("active_flags") == []
                and isinstance(turns, list)
                and isinstance(turn_count, int)
                and not isinstance(turn_count, bool)
                and turn_count == len(turns)
                and bool(turns)
                and isinstance(turns[-1], dict)
                and turns[-1].get("turn_id") == turn_id
                and turn_status in ({"failed", "interrupted"}
                                    if run.current_step == "codex_implementation"
                                    else {"failed"})
            )
            if not reconciled:
                raise RunnerError("recovery_blocked", "the SDK operation has no uniquely recoverable external result; inspect the persisted thread/turn before retry")
            reconciled_status = str(turn_status)
            store.append_event(
                run_id=run.run_id,
                event_key=f"recovery:{run.run_id}:failed-turn-retry:{turn_id}:{worker['updated_at']}",
                event_type=("interrupted_sdk_turn_reconciled"
                            if reconciled_status == "interrupted"
                            else "failed_sdk_turn_reconciled"),
                payload={
                    "step": run.current_step,
                    **({"spec_key": _implementation_spec_key(run=run, worker=worker)}
                       if run.current_step == "codex_implementation" else {}),
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "thread_status": "idle",
                    "turn_status": reconciled_status,
                },
            )
            return {
                "created": False,
                **_resume_codex_stage(
                    control_root=control_root,
                    config=config,
                    run=run,
                    brief=brief,
                    brief_digest=brief_digest,
                    store=store,
                    thread_id=thread_id,
                    spec_key=(
                        _implementation_spec_key(run=run, worker=worker)
                        if run.current_step == "codex_implementation"
                        else (
                            str(worker["worker_id"])[len(f"{worker_prefix}:codex_ticket_planning:"):]
                            if run.current_step == "codex_ticket_planning"
                            and str(worker["worker_id"]).startswith(f"{worker_prefix}:codex_ticket_planning:")
                            else None
                        )
                    ),
                ),
            }
        if run.state in {"running", "cleanup_pending", "blocked"} and run.current_step == "codex_review":
            review_prefix = f"codex_sdk:{run.run_id}:codex_review:"
            review_workers = [
                worker for worker in store.workers_for_run(run.run_id)
                if worker.get("backend_kind") == "codex_sdk"
                and worker.get("state") in {"running", "failed", "rejected", "reviewed"}
                and str(worker.get("worker_id") or "").startswith(review_prefix)
            ]
            worker = review_workers[-1] if review_workers else None
            if worker is None:
                raise RunnerError("recovery_blocked", "review stage has no uniquely identified worker")
            if run.state == "blocked" and worker.get("state") == "reviewed":
                worker_id = str(worker.get("worker_id") or "")
                review_identity = worker_id[len(review_prefix):].rsplit(":", 1) if worker_id.startswith(review_prefix) else []
                if len(review_identity) == 2:
                    review_path = _safe_artifact_directory(control_root, config, run.run_id) / f"review-{review_identity[0]}-{review_identity[1]}.json"
                    if review_path.is_file() and load_json(review_path).get("approved") is True:
                        recovered = _reconcile_approved_review(
                            control_root=control_root, config=config, run=run, store=store,
                            worker=worker, brief_digest=brief_digest,
                        )
                    else:
                        recovered = _reconcile_blocked_review(
                            control_root=control_root, config=config, run=run, store=store,
                            worker=worker, brief_digest=brief_digest,
                        )
                else:
                    recovered = _reconcile_blocked_review(
                        control_root=control_root, config=config, run=run, store=store,
                        worker=worker, brief_digest=brief_digest,
                    )
                return {"created": False, **recovered}
            thread_id = str(worker.get("external_thread_id") or "")
            turn_id = str(worker.get("external_turn_id") or "")
            if not thread_id or not turn_id:
                raise RunnerError("recovery_blocked", "review lacks durable thread/turn identity")
            review_identity = str(worker.get("worker_id") or "")[len(review_prefix):].rsplit(":", 1)
            if len(review_identity) != 2:
                raise RunnerError("recovery_blocked", "review lacks SPEC/candidate identity")
            spec_key = review_identity[0]
            workspace = _implementation_workspace_path(
                control_root=control_root, config=config, run=run, spec_key=spec_key,
            )
            try:
                inspection = CodexAdapter().read_thread(thread_id=thread_id, repository_path=workspace)
            except RunnerError as exc:
                raise RunnerError(
                    "recovery_blocked", "the review thread could not be reconciled",
                    details={"inspection_error": exc.code},
                ) from exc
            turns = inspection.get("turns") if isinstance(inspection, dict) else None
            turn_count = inspection.get("turn_count") if isinstance(inspection, dict) else None
            turn_status = (
                turns[-1].get("status") if isinstance(turns, list) and turns
                and isinstance(turns[-1], dict) else None
            )
            terminal = (
                isinstance(inspection, dict)
                and inspection.get("thread_id") == thread_id
                and inspection.get("thread_status") == "idle"
                and inspection.get("active_flags") == []
                and inspection.get("started_turn") is False
                and isinstance(turns, list)
                and isinstance(turn_count, int)
                and not isinstance(turn_count, bool)
                and len(turns) == turn_count
                and bool(turns)
                and isinstance(turns[-1], dict)
                and turns[-1].get("turn_id") == turn_id
                and turn_status in {"completed", "failed", "interrupted"}
            )
            if not terminal:
                raise RunnerError("recovery_blocked", "review turn has no uniquely recoverable terminal result")
            artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
            candidate_files = sorted(artifact_directory.glob("candidate-*.json"))
            candidate_receipt = None
            candidate_short = review_identity[1]
            for path in candidate_files:
                document = load_json(path)
                candidate_name = path.stem.removeprefix("candidate-")
                if (candidate_name == spec_key or candidate_name.startswith(f"{spec_key}-")) and str(document.get("candidate_sha") or "").startswith(candidate_short):
                    candidate_receipt = document
                    break
            if candidate_receipt is None:
                for path in sorted(artifact_directory.glob(f"repair-{spec_key}-*.json")):
                    document = load_json(path)
                    candidate = document.get("candidate")
                    if isinstance(candidate, dict) and str(candidate.get("candidate_sha") or "").startswith(candidate_short):
                        candidate_receipt = candidate
                        break
            if candidate_receipt is None:
                raise RunnerError("recovery_blocked", "review stage has no matching candidate receipt")
            if turn_status in {"failed", "interrupted"}:
                operation_id = f"review:{run.run_id}:{spec_key}:{candidate_receipt['candidate_sha']}"
                if worker.get("state") != "rejected":
                    store.reject_codex_stage(
                        run.run_id,
                        operation_id,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        step_name="codex_review",
                        worker_id=str(worker["worker_id"]),
                        code=("sdk_turn_interrupted" if turn_status == "interrupted" else "sdk_turn_failed"),
                    )
                    worker = next(
                        item for item in store.workers_for_run(run.run_id)
                        if item.get("worker_id") == worker.get("worker_id")
                    )
                store.append_event(
                    run_id=run.run_id,
                    event_key=f"recovery:{run.run_id}:review-turn-reconciled:{turn_id}:{worker['updated_at']}",
                    event_type=("interrupted_review_turn_reconciled"
                                if turn_status == "interrupted"
                                else "failed_review_turn_reconciled"),
                    payload={
                        "step": "codex_review",
                        "spec_key": spec_key,
                        "candidate_sha": str(candidate_receipt["candidate_sha"]),
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "thread_status": "idle",
                        "turn_status": turn_status,
                    },
                )
                recovered = _reconcile_rejected_review(
                    control_root=control_root,
                    config=config,
                    run=run,
                    store=store,
                    worker=worker,
                    brief_digest=brief_digest,
                )
                return {"created": False, **recovered}
            review_path = artifact_directory / f"review-worker-{spec_key}-{candidate_short}.json"
            if not review_path.is_file():
                raise RunnerError("recovery_blocked", "review stage has no persisted worker receipt")
            review_document = load_json(review_path)
            final_response = review_document.get("final_response")
            if not isinstance(final_response, str) or not final_response.strip():
                raise RunnerError("recovery_blocked", "review stage has no structured worker output")
            review_result = CodexWorkerResult(
                thread_id=thread_id,
                turn_id=turn_id,
                status="completed",
                error=review_document.get("error"),
                final_response=final_response,
                item_count=int(review_document.get("item_count", 0)),
                started_at=int(review_document.get("started_at", 0)),
                completed_at=int(review_document.get("completed_at", 0)),
            )
            ticket_plan = load_json(artifact_directory / f"ticket-plan-{spec_key}.json")
            try:
                independent_review(
                    review_result,
                    implementation_thread="__recovery_review_owner__",
                    candidate_sha=str(candidate_receipt["candidate_sha"]),
                    acceptance_version=str(ticket_plan["digest"]),
                )
            except RunnerError as exc:
                operation_id = f"review:{run.run_id}:{spec_key}:{candidate_receipt['candidate_sha']}"
                if worker.get("state") != "rejected":
                    store.reject_codex_stage(
                        run.run_id,
                        operation_id,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        step_name="codex_review",
                        worker_id=str(worker["worker_id"]),
                        code=exc.code,
                    )
                    worker = next(item for item in store.workers_for_run(run.run_id) if item.get("worker_id") == worker["worker_id"])
            else:
                raise RunnerError("recovery_blocked", "review receipt was valid but the stage cannot be replayed safely")
            recovered = _reconcile_rejected_review(
                control_root=control_root,
                config=config,
                run=run,
                store=store,
                worker=worker,
                brief_digest=brief_digest,
            )
            return {"created": False, **recovered}
        if run.state in {"running", "cleanup_pending", "blocked"} and run.current_step == "codex_repair":
            workers = store.workers_for_run(run.run_id)
            worker_prefix = f"codex_sdk:{run.run_id}:codex_repair:"
            stage_workers = [
                worker for worker in workers
                if worker.get("backend_kind") == "codex_sdk"
                and worker.get("state") in {"running", "failed"}
                and str(worker.get("worker_id") or "").startswith(worker_prefix)
            ]
            worker = stage_workers[-1] if stage_workers else None
            if worker is None:
                # A repair can fail while restoring its archived owner, after
                # the durable stage intent but before the SDK emits a turn
                # identity. Reuse the last formal repair owner for this SPEC;
                # the next repair operation will create the missing worker
                # identity and remain fully acceptance-gated.
                intents = [
                    item for item in store.operations_for_run(run.run_id)
                    if str(item.get("operation_id") or "").startswith(f"repair:{run.run_id}:")
                    and item.get("state") == "intent"
                ]
                latest_intent = intents[-1] if intents else None
                if latest_intent is None:
                    raise RunnerError("recovery_blocked", "the failed repair stage has no uniquely identified worker")
                operation_parts = str(latest_intent.get("operation_id") or "").split(":")
                spec_key = operation_parts[-2] if len(operation_parts) >= 4 else ""
                owner_workers = [
                    item for item in workers
                    if item.get("backend_kind") == "codex_sdk"
                    and str(item.get("worker_id") or "") == f"{worker_prefix}{spec_key}"
                    and item.get("external_thread_id")
                ]
                owner = owner_workers[-1] if owner_workers else None
                if owner is None or not spec_key:
                    raise RunnerError("recovery_blocked", "the orphaned repair intent has no uniquely identified owner")
                thread_id = str(owner.get("external_thread_id") or "")
                artifact_directory = _safe_artifact_directory(control_root, config, run.run_id)
                review_files = sorted(artifact_directory.glob(f"review-{spec_key}-*.json"))
                ticket_path = artifact_directory / f"ticket-plan-{spec_key}.json"
                if not review_files or not ticket_path.is_file():
                    raise RunnerError("recovery_blocked", "the orphaned repair intent lacks review or ticket evidence")
                review = load_json(review_files[-1])
                findings = review.get("blocking")
                if not isinstance(findings, list) or not findings:
                    raise RunnerError("recovery_blocked", "the orphaned repair intent lacks blocking findings")
                ticket_plan = validate_ticket_plan(
                    load_json(ticket_path), expected_spec_key=spec_key,
                    expected_base_sha=git_sha(config.repository_path, config.target_ref, timeout_seconds=config.git_timeout_seconds),
                )
                workspace = _implementation_workspace_path(
                    control_root=control_root, config=config, run=run, spec_key=spec_key,
                )
                _repair_candidate(
                    control_root=control_root, config=config, brief_digest=brief_digest,
                    run=run, store=store, ticket_plan=ticket_plan, workspace=workspace,
                    findings=findings, implementation_thread=thread_id,
                    artifact_directory=artifact_directory,
                )
                return {"created": False, **_resume_after_repair_candidate(
                    control_root=control_root, config=config, brief_digest=brief_digest,
                    run=run, store=store, spec_key=spec_key,
                )}
            thread_id = str(worker.get("external_thread_id") or "")
            turn_id = str(worker.get("external_turn_id") or "")
            if not thread_id or not turn_id:
                raise RunnerError("recovery_blocked", "the repair stage has no durable thread/turn identity")
            spec_key = _repair_spec_key(run=run, worker=worker)
            workspace = _implementation_workspace_path(
                control_root=control_root, config=config, run=run, spec_key=spec_key,
            )
            try:
                inspection = CodexAdapter().read_thread(thread_id=thread_id, repository_path=workspace)
            except RunnerError as exc:
                raise RunnerError(
                    "recovery_blocked", "the repair thread could not be reconciled",
                    details={"inspection_error": exc.code},
                ) from exc
            turns = inspection.get("turns") if isinstance(inspection, dict) else None
            repair_turn_status = (
                turns[-1].get("status") if isinstance(turns, list) and turns
                and isinstance(turns[-1], dict) else None
            )
            if repair_turn_status not in {"completed", "failed", "interrupted"}:
                raise RunnerError("recovery_blocked", "the repair turn has no uniquely recoverable terminal result")
            recovered = _reconcile_repair_turn(
                control_root=control_root, config=config, run=run,
                brief_digest=brief_digest, store=store, worker=worker,
                turn_status=str(repair_turn_status),
            )
            return {"created": False, **recovered}
        if run.state in {"running", "cleanup_pending"} and run.current_step == "codex_ticket_planning":
            workers = store.workers_for_run(run.run_id)
            worker_prefix = f"codex_sdk:{run.run_id}:codex_ticket_planning:"
            stage_workers = [
                worker for worker in workers
                if worker.get("backend_kind") == "codex_sdk"
                and worker.get("state") in {"running", "failed", "interrupted"}
                and str(worker.get("worker_id") or "").startswith(worker_prefix)
            ]
            worker = stage_workers[-1] if stage_workers else None
            thread_id = str(worker.get("external_thread_id") or "") if worker else ""
            turn_id = str(worker.get("external_turn_id") or "") if worker else ""
            if worker is None or not thread_id or not turn_id:
                raise RunnerError("recovery_blocked", "the SDK operation has no uniquely recoverable external result; inspect the persisted thread/turn before retry")
            try:
                inspection = CodexAdapter().read_thread(
                    thread_id=thread_id,
                    repository_path=config.repository_path,
                )
            except RunnerError as exc:
                raise RunnerError(
                    "recovery_blocked",
                    "the persisted SDK thread could not be reconciled; inspect it before retry",
                    details={"inspection_error": exc.code},
                ) from exc
            turns = inspection.get("turns") if isinstance(inspection, dict) else None
            turn_count = inspection.get("turn_count") if isinstance(inspection, dict) else None
            completed = (
                isinstance(inspection, dict)
                and inspection.get("started_turn") is False
                and inspection.get("thread_id") == thread_id
                and inspection.get("thread_status") == "idle"
                and inspection.get("active_flags") == []
                and isinstance(turns, list)
                and isinstance(turn_count, int)
                and not isinstance(turn_count, bool)
                and turn_count == len(turns)
                and bool(turns)
                and isinstance(turns[-1], dict)
                and turns[-1].get("turn_id") == turn_id
                and turns[-1].get("status") == "completed"
            )
            terminal_retry = (
                isinstance(inspection, dict)
                and inspection.get("started_turn") is False
                and inspection.get("thread_id") == thread_id
                and inspection.get("thread_status") == "idle"
                and inspection.get("active_flags") == []
                and isinstance(turns, list)
                and isinstance(turn_count, int)
                and not isinstance(turn_count, bool)
                and turn_count == len(turns)
                and bool(turns)
                and isinstance(turns[-1], dict)
                and turns[-1].get("turn_id") == turn_id
                and turns[-1].get("status") in {"failed", "interrupted"}
            )
            if terminal_retry:
                store.append_event(
                    run_id=run.run_id,
                    event_key=f"recovery:{run.run_id}:implementation-turn-retry:{turn_id}:{worker['updated_at']}",
                    event_type="failed_sdk_turn_reconciled",
                    payload={
                        "step": run.current_step,
                        "spec_key": spec_key,
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "thread_status": "idle",
                        "turn_status": turns[-1].get("status"),
                    },
                )
                resumed = _resume_codex_stage(
                    control_root=control_root, config=config, run=run,
                    brief=brief, brief_digest=brief_digest, store=store,
                    thread_id=thread_id, spec_key=spec_key,
                )
                return {"created": False, **resumed}
            if not completed:
                raise RunnerError("recovery_blocked", "the SDK operation has no uniquely recoverable external result; inspect the persisted thread/turn before retry")
            recovered = _reconcile_completed_ticket_turn(
                control_root=control_root, config=config, run=run, brief_digest=brief_digest,
                store=store, worker=worker, thread_id=thread_id, turn_id=turn_id,
            )
            return {"created": False, **store.public_status(recovered.run_id)}
        if run.state in {"running", "cleanup_pending", "blocked"} and run.current_step == "codex_implementation":
            workers = store.workers_for_run(run.run_id)
            worker_prefix = f"codex_sdk:{run.run_id}:codex_implementation:"
            stage_workers = [
                worker for worker in workers
                if worker.get("backend_kind") == "codex_sdk"
                and worker.get("state") == "running"
                and str(worker.get("worker_id") or "").startswith(worker_prefix)
            ]
            worker = stage_workers[-1] if stage_workers else None
            thread_id = str(worker.get("external_thread_id") or "") if worker else ""
            turn_id = str(worker.get("external_turn_id") or "") if worker else ""
            if worker is None or not thread_id or not turn_id:
                raise RunnerError("recovery_blocked", "the SDK operation has no uniquely recoverable external result; inspect the persisted thread/turn before retry")
            spec_key = _implementation_spec_key(run=run, worker=worker)
            inspection_repository = _implementation_workspace_path(
                control_root=control_root, config=config, run=run, spec_key=spec_key,
            )
            try:
                inspection = CodexAdapter().read_thread(
                    thread_id=thread_id,
                    repository_path=inspection_repository,
                )
            except RunnerError as exc:
                raise RunnerError(
                    "recovery_blocked",
                    "the persisted SDK thread could not be reconciled; inspect it before retry",
                    details={"inspection_error": exc.code},
                ) from exc
            turns = inspection.get("turns") if isinstance(inspection, dict) else None
            turn_count = inspection.get("turn_count") if isinstance(inspection, dict) else None
            completed = (
                isinstance(inspection, dict)
                and inspection.get("started_turn") is False
                and inspection.get("thread_id") == thread_id
                and inspection.get("thread_status") == "idle"
                and inspection.get("active_flags") == []
                and isinstance(turns, list)
                and isinstance(turn_count, int)
                and not isinstance(turn_count, bool)
                and turn_count == len(turns)
                and bool(turns)
                and isinstance(turns[-1], dict)
                and turns[-1].get("turn_id") == turn_id
                and turns[-1].get("status") == "completed"
            )
            interrupted = (
                isinstance(inspection, dict)
                and inspection.get("started_turn") is False
                and inspection.get("thread_id") == thread_id
                and inspection.get("thread_status") == "idle"
                and inspection.get("active_flags") == []
                and isinstance(turns, list)
                and isinstance(turn_count, int)
                and not isinstance(turn_count, bool)
                and turn_count == len(turns)
                and bool(turns)
                and isinstance(turns[-1], dict)
                and turns[-1].get("turn_id") == turn_id
                and turns[-1].get("status") == "interrupted"
            )
            failed = (
                isinstance(inspection, dict)
                and inspection.get("started_turn") is False
                and inspection.get("thread_id") == thread_id
                and inspection.get("thread_status") == "idle"
                and inspection.get("active_flags") == []
                and isinstance(turns, list)
                and isinstance(turn_count, int)
                and not isinstance(turn_count, bool)
                and turn_count == len(turns)
                and bool(turns)
                and isinstance(turns[-1], dict)
                and turns[-1].get("turn_id") == turn_id
                and turns[-1].get("status") == "failed"
            )
            if interrupted or failed:
                turn_status = "interrupted" if interrupted else "failed"
                store.append_event(
                    run_id=run.run_id,
                    event_key=f"recovery:{run.run_id}:implementation-turn-retry:{turn_id}:{worker['updated_at']}",
                    event_type=("interrupted_sdk_turn_reconciled"
                                if interrupted else "failed_sdk_turn_reconciled"),
                    payload={
                        "step": run.current_step,
                        "spec_key": spec_key,
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "thread_status": "idle",
                        "turn_status": turn_status,
                    },
                )
                resumed = _resume_codex_stage(
                    control_root=control_root, config=config, run=run,
                    brief=brief, brief_digest=brief_digest, store=store,
                    thread_id=thread_id, spec_key=spec_key,
                )
                return {"created": False, **resumed}
            if not completed:
                raise RunnerError("recovery_blocked", "the SDK operation has no uniquely recoverable external result; inspect the persisted thread/turn before retry")
            recovered = _reconcile_completed_implementation_turn(
                control_root=control_root, config=config, run=run, brief_digest=brief_digest,
                store=store, worker=worker, thread_id=thread_id, turn_id=turn_id,
            )
            return {"created": False, **recovered}
        if run.state in {"starting", "running", "cleanup_pending"}:
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


def _blocked_implementation_retry_identity(*, control_root: Path, config: RunnerConfig,
                                           run: RunRecord, store: Store) -> tuple[str, str] | None:
    """Find a safe same-thread retry after a semantic implementation failure."""
    if run.current_step != "codex_implementation":
        return None
    workers = store.workers_for_run(run.run_id)
    prefix = f"codex_sdk:{run.run_id}:codex_implementation:"
    candidates = [
        worker for worker in workers
        if worker.get("backend_kind") == "codex_sdk"
        and worker.get("state") in {"running", "failed"}
        and str(worker.get("worker_id") or "").startswith(prefix)
    ]
    worker = candidates[-1] if candidates else None
    if worker is None:
        return None
    thread_id = str(worker.get("external_thread_id") or "")
    turn_id = str(worker.get("external_turn_id") or "")
    if not thread_id or not turn_id:
        return None
    try:
        spec_key = _implementation_spec_key(run=run, worker=worker)
        artifact = _safe_artifact_directory(control_root, config, run.run_id) / f"implementation-{spec_key}.json"
        persisted = load_json(artifact)
        final_response = persisted.get("final_response")
        if persisted.get("thread_id") != thread_id or persisted.get("turn_id") != turn_id:
            return None
        if persisted.get("status") != "completed" or persisted.get("error") is not None:
            return None
        if not isinstance(final_response, str) or not final_response.strip():
            return None
        document = json.loads(final_response)
        if not isinstance(document, dict):
            return None
        retry_required = (
            document.get("outcome") != "completed"
            or document.get("blockers") != []
            or document.get("questions") != []
        )
        workspace = _implementation_workspace_path(
            control_root=control_root, config=config, run=run, spec_key=spec_key,
        )
        artifacts = document.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            retry_required = True
        else:
            root = workspace.resolve()
            for item in artifacts:
                if not isinstance(item, str) or not item.strip():
                    retry_required = True
                    break
                relative = Path(item)
                path = root / relative
                if (
                    relative.is_absolute()
                    or ".." in relative.parts
                    or ".git" in relative.parts
                    or root not in path.resolve().parents
                    or not path.is_file()
                    or any(part.is_symlink() for part in [path, *path.parents])
                ):
                    retry_required = True
                    break
        if not retry_required:
            return None
        inspection = CodexAdapter().read_thread(thread_id=thread_id, repository_path=workspace)
        turns = inspection.get("turns") if isinstance(inspection, dict) else None
        turn_count = inspection.get("turn_count") if isinstance(inspection, dict) else None
        terminal = (
            isinstance(inspection, dict)
            and inspection.get("started_turn") is False
            and inspection.get("thread_id") == thread_id
            and inspection.get("thread_status") == "idle"
            and inspection.get("active_flags") == []
            and isinstance(turns, list)
            and isinstance(turn_count, int)
            and not isinstance(turn_count, bool)
            and turn_count == len(turns)
            and bool(turns)
            and isinstance(turns[-1], dict)
            and turns[-1].get("turn_id") == turn_id
            and turns[-1].get("status") == "completed"
        )
        return (thread_id, spec_key) if terminal else None
    except (RunnerError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None


def _production_completed_specs(*, control_root: Path, config: RunnerConfig, run_id: str, store: Store) -> set[str]:
    persisted = store.production_completed_specs(run_id)
    path = _safe_artifact_directory(control_root, config, run_id) / "completed-specs.json"
    if not path.exists():
        return persisted
    document = load_json(path)
    values = document.get("specs", [])
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise RunnerError("completed_specs_corrupt", "completed SPEC record is invalid")
    # The file is only a human-readable projection; only transactional Store
    # receipts may advance the production queue.
    return persisted


def _record_production_spec(*, control_root: Path, config: RunnerConfig, run_id: str, spec_key: str,
                            plan_digest: str, store: Store) -> None:
    directory = _safe_artifact_directory(control_root, config, run_id)
    completed = _production_completed_specs(control_root=control_root, config=config, run_id=run_id, store=store)
    completed.add(spec_key)
    receipt = load_json(directory / f"delivery-{spec_key}.json")
    delivery_digest = hashlib.sha256(json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    store.complete_production_spec(run_id=run_id, spec_key=spec_key, plan_digest=plan_digest,
                                   delivery_digest=delivery_digest)
    path = directory / "completed-specs.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"schema_version": "spec-runner-completed-specs/v1",
        "plan_digest": plan_digest, "specs": sorted(completed)}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    temporary.replace(path)


def _persist_delivery_evidence(*, control_root: Path, config: RunnerConfig, run_id: str,
                               spec_key: str, delivery: dict[str, object]) -> None:
    """Durably bind successful delivery to its exact plan before cleanup."""
    artifact = _safe_artifact_directory(control_root, config, run_id)
    plan = load_json(artifact / "spec-plan.json")
    ticket = load_json(artifact / f"ticket-plan-{spec_key}.json")
    record = {"schema_version": "spec-runner-production-delivery/v1", "run_id": run_id,
              "spec_key": spec_key, "plan_digest": plan.get("digest"),
              "ticket_plan_digest": ticket.get("digest"), **delivery}
    path = artifact / f"delivery-{spec_key}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                         encoding="utf-8", newline="\n")
    temporary.replace(path)


def _run_production_queue(*, control_root: Path, config: RunnerConfig, brief_digest: str,
                          run: RunRecord, store: Store, spec_plan: dict[str, object]) -> dict[str, object]:
    try:
        return _run_production_queue_impl(
            control_root=control_root,
            config=config,
            brief_digest=brief_digest,
            run=run,
            store=store,
            spec_plan=spec_plan,
        )
    except RunnerError as exc:
        decision = _record_recovery_failure(run=run, store=store, operation_id=f"start:{run.run_id}", error=exc)
        # The initial planning stage is wrapped by start(), but production
        # queue work begins after that boundary. Close the durable run before
        # returning a queue error so a process exit cannot leave it running.
        try:
            state = decision.action.value if decision.action in {
                RecoveryAction.WAIT_RETRY,
                RecoveryAction.SERVICE_WAIT,
                RecoveryAction.WAIT_FOR_CONFIG,
            } else "failed"
            store.fail_run(run.run_id, f"start:{run.run_id}", state=state)
        except RunnerError as state_error:
            raise state_error from exc
        raise


def _run_production_queue_impl(*, control_root: Path, config: RunnerConfig, brief_digest: str,
                               run: RunRecord, store: Store, spec_plan: dict[str, object]) -> dict[str, object]:
    """Drive every ready SPEC in one program-owned production run.

    The model plans the queue once. The Runner alone selects the next ready
    SPEC, persists completion, and continues from the latest plan; no parent
    conversation owns a cross-SPEC dispatch loop.
    """
    specs = spec_plan.get("specs")
    if not isinstance(specs, list) or any(not isinstance(item, dict) for item in specs):
        raise RunnerError("invalid_spec_plan", "production queue requires keyed SPEC objects")
    completed = _production_completed_specs(control_root=control_root, config=config, run_id=run.run_id, store=store)
    while len(completed) < len(specs):
        ready = [item for item in specs if str(item.get("key")) not in completed and
                 set(item.get("blocked_by", [])) <= completed]
        if not ready:
            raise RunnerError("production_queue_blocked", "no dependency-ready SPEC remains")
        spec = ready[0]
        spec_key = str(spec["key"])
        ticket_files = sorted(_safe_artifact_directory(control_root, config, run.run_id).glob(f"ticket-plan-{spec_key}.json"))
        if ticket_files and run.state == "tickets_ready":
            ticketed = run
        else:
            ticketed = _execute_codex_tickets(
                control_root=control_root, config=config, brief_digest=brief_digest, run=run,
                store=store, spec_plan={**spec_plan, "specs": [spec]})
        if ticketed.state != "tickets_ready":
            current = store.find_by_run_id(run.run_id) or run
            return {"state": current.state, **store.public_status(run.run_id)}
        ticket_files = sorted(_safe_artifact_directory(control_root, config, run.run_id).glob(f"ticket-plan-{spec_key}.json"))
        if not ticket_files:
            raise RunnerError("ticket_plan_missing", f"SPEC {spec_key} has no persisted TicketPlan")
        delivered = _execute_codex_implementation(
            control_root=control_root, config=config, brief_digest=brief_digest, run=ticketed,
            store=store, ticket_plan=load_json(ticket_files[-1]), finalize_run=False)
        if delivered.get("state") in {"waiting_ci", "cleanup_pending"}:
            return delivered
        # A completed SPEC may have been recovered from a completed CI wait.
        # Persist that fact before selecting another queue item.
        if delivered.get("state") == "spec_completed" and str(delivered.get("spec_key")) == spec_key:
            _record_production_spec(control_root=control_root, config=config, run_id=run.run_id, store=store,
                spec_key=spec_key, plan_digest=str(spec_plan.get("digest", "")))
            completed.add(spec_key)
            current = store.find_by_run_id(run.run_id)
            if current is None:
                raise RunnerError("run_missing", "production queue run disappeared during continuation")
            run = current
            continue
        if delivered.get("state") != "spec_completed":
            return delivered
        _record_production_spec(control_root=control_root, config=config, run_id=run.run_id, store=store,
            spec_key=spec_key, plan_digest=str(spec_plan.get("digest", "")))
        completed.add(spec_key)
        current = store.find_by_run_id(run.run_id)
        if current is None:
            raise RunnerError("run_missing", "production queue run disappeared during continuation")
        run = current
    store.mark_archived(run.run_id, state="completed")
    return {"state": "completed", **store.public_status(run.run_id)}


def _retry_production_cleanup(*, control_root: Path, config: RunnerConfig, run: RunRecord,
                              store: Store) -> dict[str, object]:
    """Retry only Runner-owned cleanup after a production process exit."""
    root = control_root / "delivery-workspaces"
    manifests: list[Path] = []
    for manifest in root.glob("*.manifest.json"):
        try:
            document = load_json(manifest)
        except RunnerError:
            continue
        if document.get("run_id") == run.run_id:
            manifests.append(manifest)
    artifact = _safe_artifact_directory(control_root, config, run.run_id)
    plan_path = artifact / "spec-plan.json"
    if not plan_path.is_file():
        raise RunnerError("production_cleanup_evidence_missing", "cleanup retry requires persisted delivery, plan and ticket evidence")
    plan = load_json(plan_path)
    manifest_documents = [(manifest, load_json(manifest)) for manifest in manifests]
    manifest_spec_keys = [document.get("spec_key") for _, document in manifest_documents]
    if any(not isinstance(spec_key, str) or not spec_key for spec_key in manifest_spec_keys):
        raise RunnerError("production_cleanup_evidence_missing", "workspace manifest has no SPEC identity")
    spec_keys = set(manifest_spec_keys)
    for receipt_path in artifact.glob("delivery-*.json"):
        receipt = load_json(receipt_path)
        if receipt.get("run_id") == run.run_id and isinstance(receipt.get("spec_key"), str):
            spec_keys.add(str(receipt["spec_key"]))
    if not spec_keys:
        raise RunnerError("production_cleanup_evidence_missing", "cleanup_pending run has no delivery or workspace evidence")
    if not manifests and config.github_repository is None:
        raise RunnerError("production_cleanup_evidence_missing", "cleanup_pending run has no owned workspace manifest")
    for spec_key in spec_keys:
        receipt_path = artifact / f"delivery-{spec_key}.json"
        ticket_path = artifact / f"ticket-plan-{spec_key}.json"
        if not receipt_path.is_file() or not ticket_path.is_file():
            raise RunnerError("production_cleanup_evidence_missing", "cleanup retry requires persisted delivery, plan and ticket evidence")
        receipt, ticket = load_json(receipt_path), load_json(ticket_path)
        if (receipt.get("spec_key") != spec_key or receipt.get("run_id") != run.run_id
                or receipt.get("plan_digest") != plan.get("digest")
                or receipt.get("ticket_plan_digest") != ticket.get("digest")
                or not isinstance(receipt.get("candidate"), dict)
                or not isinstance(receipt.get("review"), dict)
                or receipt.get("review", {}).get("approved") is not True
                or not isinstance(receipt.get("merge"), dict)
                or receipt.get("merge", {}).get("merged") is not True):
            raise RunnerError("production_cleanup_evidence_invalid", "persisted delivery evidence does not prove this SPEC was reviewed and merged")
        if spec_key not in set(manifest_spec_keys) and config.github_repository is not None:
            if (not isinstance(receipt.get("cleanup"), dict)
                    or receipt["cleanup"].get("outcome") != "cleaned"
                    or not isinstance(receipt.get("issue_closure"), dict)
                    or receipt["issue_closure"].get("complete") is not True):
                raise RunnerError("production_cleanup_evidence_invalid", "manifest-free SPEC lacks durable cleanup and issue closure readbacks")
    results = [cleanup_managed_workspace(
        repository=config.repository_path, workspace_root=root,
        workspace=Path(str(document["workspace"])), manifest=manifest, preserve_manifest=True)
        for manifest, document in manifest_documents]
    if any(item.get("outcome") != "cleaned" for item in results):
        return {"state": "cleanup_pending", "cleanup": results}
    closures = []
    closures_by_spec: dict[str, dict[str, object] | None] = {}
    for spec_key in sorted(spec_keys):
        ticket_plan = load_json(artifact / f"ticket-plan-{spec_key}.json")
        try:
            closure = _close_published_ticket_plan(
                config=config, plan=ticket_plan, run_id=run.run_id, store=store,
            )
            closures.append(closure)
            closures_by_spec[spec_key] = closure
        except RunnerError as exc:
            return {"state": "cleanup_pending", "cleanup": results,
                    "issue_closure": {"spec_key": spec_key, "error_code": exc.code}}
    finalized = [cleanup_managed_workspace(
        repository=config.repository_path, workspace_root=root,
        workspace=Path(str(document["workspace"])), manifest=manifest)
        for manifest, document in manifest_documents]
    if any(item.get("outcome") != "cleaned" for item in finalized):
        return {"state": "cleanup_pending", "cleanup": results, "manifest_cleanup": finalized}
    for spec_key, closure in closures_by_spec.items():
        delivery_path = artifact / f"delivery-{spec_key}.json"
        delivery = load_json(delivery_path)
        delivery["issue_closure"] = closure
        if spec_key in set(manifest_spec_keys):
            delivery["cleanup"] = {"outcome": "cleaned", "recovered": True}
        _write_json_atomic(delivery_path, delivery)
    for spec_key in sorted(spec_keys):
        _record_production_spec(control_root=control_root, config=config, run_id=run.run_id, store=store,
            spec_key=str(spec_key), plan_digest=str(plan["digest"]))
    store.set_run_state(run.run_id, "spec_completed")
    completed = {"state": "spec_completed", "cleanup": results, "issue_closures": closures}
    if len(spec_keys) == 1:
        completed["spec_key"] = next(iter(spec_keys))
    else:
        completed["spec_keys"] = sorted(spec_keys)
    return completed


def _prepare_clean_migration(*, store: Store, config: RunnerConfig, run: RunRecord,
                             migration: dict[str, object], stage: str, input_revision: str) -> str | None:
    """Create one clean successor after durable source handover.

    The successor identity is recorded before the first SDK turn. Replays use
    the stored identity and never create a second successor for the same key.
    """
    migration_key = str(migration.get("migration_key") or "")
    source_thread_id = str(migration.get("source_thread_id") or "")
    handover = migration.get("handover")
    if not migration_key or not source_thread_id or not isinstance(handover, dict):
        raise RunnerError("thread_migration_invalid", "clean migration requires migration key, source thread and handover")
    handover_digest = hashlib.sha256(json.dumps(handover, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    persisted = store.prepare_thread_migration(
        migration_key=migration_key, run_id=run.run_id, stage=stage, source_thread_id=source_thread_id,
        handover_digest=handover_digest, input_revision=input_revision, owner_generation=int(migration.get("owner_generation", 0)),
    )
    if persisted.get("state") == "uncertain":
        raise RunnerError("thread_successor_uncertain", "successor creation is uncertain; reconcile the recorded migration before retry")
    if not isinstance(persisted.get("handover"), dict):
        persisted = store.record_migration_handover(migration_key=migration_key, handover=handover)
    successor_id = str(persisted.get("successor_thread_id") or "")
    if not successor_id:
        try:
            created = CodexAdapter().start_clean_thread(
                repository_path=config.repository_path, model=config.model_name,
            )
        except RunnerError as exc:
            # A transport error after the provider may have accepted creation
            # is deliberately non-retryable until external identity is read back.
            store.record_migration_uncertainty(migration_key=migration_key, details={"code": exc.code, "stage": stage})
            raise RunnerError("thread_successor_uncertain", "clean successor creation has no uniquely confirmed identity") from exc
        successor_id = str(created.get("thread_id") or "")
        if not successor_id:
            store.record_migration_uncertainty(migration_key=migration_key, details={"reason": "identity_missing", "stage": stage})
            raise RunnerError("thread_successor_uncertain", "clean successor creation returned no identity")
        persisted = store.record_migration_successor(migration_key=migration_key, successor_thread_id=successor_id, successor=created)
    worker_id = f"codex_sdk:{run.run_id}:{stage}"
    transferred = store.complete_migration_owner_transfer(
        migration_key=migration_key, expected_generation=int(persisted.get("owner_generation", 0)), owner_worker_id=worker_id,
    )
    return str(transferred.get("successor_thread_id") or successor_id)


def _migration_for_run(store: Store, run_id: str) -> dict[str, object] | None:
    migrations = store.thread_migrations_for_run(run_id)
    return migrations[0] if len(migrations) == 1 else None


def _migration_source_archive_retry_pending(*, store: Store, run_id: str) -> bool:
    migration = _migration_for_run(store, run_id)
    if migration is None:
        return False
    return store.migration_milestone(
        str(migration["migration_key"]), "source_archive_pending"
    ) is not None


def _verified_migration_progress(*, store: Store, run: RunRecord, stage: str,
                                 verification: dict[str, object]) -> dict[str, object]:
    """Require durable evidence before recording migration business progress.

    Production delivery does not use the generic stage verification table; its
    proof is the transactional production SPEC completion receipt. The example
    and non-production paths must carry a verified stage receipt bound to this
    run.
    """
    if stage == "production_delivery":
        completed = sorted(store.production_completed_specs(run.run_id))
        if not completed:
            raise RunnerError(
                "thread_migration_progress_unverified",
                "production migration has no durable completed SPEC receipt",
            )
        run_status = verification.get("run") if isinstance(verification, dict) else None
        state = run_status.get("state") if isinstance(run_status, dict) else None
        if state not in {"spec_completed", "completed"}:
            raise RunnerError(
                "thread_migration_progress_unverified",
                "production migration status is not a completed SPEC state",
            )
        return {"completed_specs": completed, "run_state": state}

    receipts = verification.get("verification") if isinstance(verification, dict) else None
    if not isinstance(receipts, list):
        raise RunnerError(
            "thread_migration_progress_unverified",
            "migration progress requires a durable verification receipt",
        )
    matching = [
        item for item in receipts
        if isinstance(item, dict)
        and item.get("run_id") == run.run_id
        and item.get("outcome") == "verified"
    ]
    if not matching:
        raise RunnerError(
            "thread_migration_progress_unverified",
            "migration progress has no verified receipt bound to this run",
        )
    receipt = matching[-1]
    return {"receipt_id": f"verification:{run.run_id}:{receipt.get('stage')}",
            "stage": receipt.get("stage"), "outcome": receipt.get("outcome")}


def _finalize_migration_business_progress(*, config: RunnerConfig, store: Store,
                                          run: RunRecord, stage: str,
                                          resume_state: str,
                                          verification: dict[str, object]) -> dict[str, object] | None:
    """Record verified progress, then archive the stopped source thread.

    Source archive is an independent cleanup obligation.  A successor may not
    be replaced or recreated when archive readback fails, so the run is left
    at cleanup_pending and the same migration is retried later.
    """
    migration = _migration_for_run(store, run.run_id)
    if migration is None:
        return None
    migration_key = str(migration["migration_key"])
    existing_progress = store.migration_milestone(migration_key, "business_progress_verified")
    if existing_progress is None:
        evidence = _verified_migration_progress(
            store=store, run=run, stage=stage, verification=verification,
        )
        progress = {
            "run_id": run.run_id,
            "stage": stage,
            "resume_state": resume_state,
            "successor_thread_id": migration.get("successor_thread_id"),
            "verification_digest": digest(evidence),
            "evidence": evidence,
        }
        store.record_migration_milestone(
            migration_key=migration_key, milestone="business_progress_verified", receipt=progress,
        )
    if store.migration_milestone(migration_key, "source_archived") is not None:
        return None
    try:
        archive = CodexAdapter().archive_and_readback(
            thread_id=str(migration["source_thread_id"]), repository_path=config.repository_path,
        )
        if archive.get("thread_id") != migration["source_thread_id"] or archive.get("archived") is not True:
            raise RunnerError("archive_readback_failed", "source thread archive readback did not match its identity")
    except RunnerError as exc:
        if store.migration_milestone(migration_key, "source_archive_pending") is None:
            store.record_migration_milestone(
                migration_key=migration_key,
                milestone="source_archive_pending",
                receipt={"run_id": run.run_id, "resume_state": resume_state, "error_code": exc.code},
            )
        store.set_run_state(run.run_id, "cleanup_pending")
        return {"state": "cleanup_pending", "migration_cleanup": {"outcome": "pending", "error_code": exc.code}, **store.public_status(run.run_id)}
    store.record_migration_milestone(
        migration_key=migration_key,
        milestone="source_archived",
        receipt={"run_id": run.run_id, "source_thread_id": migration["source_thread_id"], "archive": archive},
    )
    return None


def _retry_migration_source_archive(*, config: RunnerConfig, store: Store,
                                    run: RunRecord) -> dict[str, object]:
    migration = _migration_for_run(store, run.run_id)
    if migration is None:
        return store.public_status(run.run_id)
    migration_key = str(migration["migration_key"])
    progress = store.migration_milestone(migration_key, "business_progress_verified")
    pending = store.migration_milestone(migration_key, "source_archive_pending")
    if progress is None or pending is None:
        raise RunnerError("thread_migration_cleanup_invalid", "source archive retry lacks verified progress")
    if store.migration_milestone(migration_key, "source_archived") is None:
        try:
            archive = CodexAdapter().archive_and_readback(
                thread_id=str(migration["source_thread_id"]), repository_path=config.repository_path,
            )
            if archive.get("thread_id") != migration["source_thread_id"] or archive.get("archived") is not True:
                raise RunnerError("archive_readback_failed", "source thread archive readback did not match its identity")
        except RunnerError as exc:
            return {"state": "cleanup_pending", "migration_cleanup": {"outcome": "pending", "error_code": exc.code}, **store.public_status(run.run_id)}
        store.record_migration_milestone(
            migration_key=migration_key,
            milestone="source_archived",
            receipt={"run_id": run.run_id, "source_thread_id": migration["source_thread_id"], "archive": archive},
        )
    resume_state = str(progress["receipt"].get("resume_state") or "ready_for_next")
    store.set_run_state(run.run_id, resume_state)
    return {"state": resume_state, "migration_cleanup": {"outcome": "cleaned"}, **store.public_status(run.run_id)}


def _continue_initial_clean_migration(*, control_root: Path, config: RunnerConfig, run: RunRecord,
                                     brief: str, brief_digest: str, store: Store,
                                     successor_thread_id: str) -> dict[str, object]:
    """Send the first business turn only after a recorded successor is owned."""
    if run.current_step == "codex_planning":
        finished = _execute_codex_planning(
            control_root=control_root, config=config, brief=brief, brief_digest=brief_digest,
            run=run, store=store, thread_id=successor_thread_id,
        )
        if finished.state in {"needs_input", "paused", "cancelled"}:
            return store.public_status(finished.run_id)
        plan_path = _safe_artifact_directory(control_root, config, run.run_id) / "spec-plan.json"
        if finished.state == "planned" and config.workflow_mode == "production":
            delivery = _run_production_queue(
                control_root=control_root, config=config, brief_digest=brief_digest,
                run=finished, store=store, spec_plan=load_json(plan_path),
            )
            if delivery.get("state") in {"spec_completed", "completed"}:
                pending = _finalize_migration_business_progress(
                    config=config, store=store, run=finished, stage="production_delivery",
                    resume_state=str(delivery["state"]), verification=delivery,
                )
                return pending or delivery
            return delivery
        if finished.state == "planned":
            ticketed = _execute_codex_tickets(
                control_root=control_root, config=config, brief_digest=brief_digest,
                run=finished, store=store, spec_plan=load_json(plan_path),
            )
            return store.public_status(ticketed.run_id)
        return store.public_status(finished.run_id)
    if run.current_step == "codex_example":
        finished = _execute_codex_example(
            control_root=control_root, config=config, brief=brief, brief_digest=brief_digest,
            run=run, store=store, thread_id=successor_thread_id,
        )
        if finished.state == "needs_input":
            return store.public_status(finished.run_id)
        if finished.state == "paused":
            return store.public_status(finished.run_id)
        verified = _verify_and_archive(control_root=control_root, config=config, run=finished, store=store, final_state="ready_for_next")
        pending = _finalize_migration_business_progress(
            config=config, store=store, run=finished, stage=finished.current_step,
            resume_state="ready_for_next", verification=verified,
        )
        if pending is not None:
            return pending
        current = store.find_by_run_id(finished.run_id) or finished
        store.append_event(
            run_id=finished.run_id, event_key=f"migration:{finished.run_id}:next-stage",
            event_type="next_stage_started", payload={"from_step": "codex_example", "to_step": "codex_second"},
        )
        return _advance_second_stage(control_root=control_root, config=config, run=current, brief_digest=brief_digest, store=store)
    raise RunnerError("thread_migration_stage_invalid", "clean migration can only resume an initial Codex stage")


def start(*, brief_file: Path, config_file: Path, control_root: Path, launch_key: str,
          run_id: str | None = None, takeover_key: str | None = None,
          launch_token: str | None = None, migration: dict[str, object] | None = None) -> dict[str, object]:
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
    takeover_record = store.takeover_record(takeover_key) if takeover_key else None
    if takeover_key and takeover_record is None:
        store.close()
        raise RunnerError("takeover_record_missing", "takeover continuation requires an existing durable takeover record")
    owner_token = (
        f"{requested_run_id}:launch:{launch_token}:{uuid.uuid4().hex}"
        if launch_token
        else f"{requested_run_id}:{os.getpid()}:{uuid.uuid4().hex}"
    )
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
        if existing is not None:
            owner_token = (
                f"{existing.run_id}:launch:{launch_token}:{uuid.uuid4().hex}"
                if launch_token
                else f"{existing.run_id}:{os.getpid()}:{uuid.uuid4().hex}"
            )
        if existing:
            config_matches_legacy_acceptance_upgrade = _acceptance_upgrade_compatible(existing, config)
            if existing.input_digest != brief_digest or (
                existing.config_digest != config.digest
                and not config_matches_legacy_acceptance_upgrade
            ):
                raise RunnerError(
                    "launch_key_input_conflict",
                    "launch_key already belongs to different normalized input",
                    details={"run_id": existing.run_id},
                )
            if takeover_key and _takeover_context(
                control_root=control_root, config=config, run=existing,
                expected_takeover_key=takeover_key,
            ) is None:
                raise RunnerError(
                    "takeover_context_missing",
                    "existing takeover continuation has no durable context artifact",
                    details={"run_id": existing.run_id, "takeover_key": takeover_key},
                )
            # Reconcile an atomic bundle that may have outlived the process
            # before its SQLite receipt transaction committed. This uses the
            # existing launch-key run and therefore cannot create a second run.
            _reconcile_continuation_bundles(
                control_root=control_root, config=config, run=existing, store=store,
            )
            if existing.state in {"completed", "cancelled", "blocked_writer_busy"}:
                if launch_token:
                    store.register_runtime(
                        existing.run_id,
                        pid=os.getpid(),
                        owner_token=owner_token,
                        log_path=os.fspath(control_root / existing.log_path),
                    )
                return {"created": False, **store.public_status(existing.run_id)}
            global_lease = _acquire_global_lease(scope=lease_scope, owner_token=owner_token, stale_after_seconds=stale_after_seconds)
            store.acquire_lease(scope=lease_scope, run_id=existing.run_id, owner_token=owner_token, stale_after_seconds=stale_after_seconds)
            store.register_runtime(
                existing.run_id,
                pid=os.getpid(),
                owner_token=owner_token,
                log_path=os.fspath(control_root / existing.log_path),
            )
            heartbeat_stop, heartbeat_thread = _start_lease_heartbeat(control_root=control_root, scope=lease_scope, owner_token=owner_token, global_path=global_lease)
            if _recovery_waits(run=existing, store=store, config=config):
                return {"created": False, **store.public_status(existing.run_id)}
            existing = store.find_by_run_id(existing.run_id) or existing
            if existing.state == "blocked" and config.execution_backend == "codex_sdk" and existing.current_step == "codex_planning":
                retry_thread = _blocked_planning_retry_thread(
                    control_root=control_root, config=config, run=existing,
                    store=store, brief_digest=brief_digest,
                )
                if retry_thread is not None:
                    resumed = _resume_codex_stage(
                        control_root=control_root, config=config, run=existing,
                        brief=brief, brief_digest=brief_digest, store=store,
                        thread_id=retry_thread,
                    )
                    return {"created": False, **resumed}
            if existing.state == "blocked" and config.execution_backend == "codex_sdk":
                retry_identity = _blocked_implementation_retry_identity(
                    control_root=control_root, config=config, run=existing, store=store,
                )
                if retry_identity is not None:
                    resumed = _resume_codex_stage(
                        control_root=control_root, config=config, run=existing,
                        brief=brief, brief_digest=brief_digest, store=store,
                        thread_id=retry_identity[0], spec_key=retry_identity[1],
                    )
                    return {"created": False, **resumed}
            if existing.state == "ready_for_next":
                final_status = _advance_second_stage(
                    control_root=control_root, config=config, run=existing, brief_digest=brief_digest, store=store
                )
                return {"created": False, **final_status}
            if existing.state == "planned":
                plan_path = _safe_artifact_directory(control_root, config, existing.run_id) / "spec-plan.json"
                if not plan_path.is_file():
                    raise RunnerError("spec_plan_missing", "planned run has no persisted SpecPlan")
                return {"created": False, **_run_production_queue(
                    control_root=control_root, config=config, brief_digest=brief_digest, run=existing,
                    store=store, spec_plan=load_json(plan_path))}
            if existing.state == "tickets_ready" and config.workflow_mode == "production":
                plan_path = _safe_artifact_directory(control_root, config, existing.run_id) / "spec-plan.json"
                return {"created": False, **_run_production_queue(
                    control_root=control_root, config=config, brief_digest=brief_digest, run=existing,
                    store=store, spec_plan=load_json(plan_path))}
            if existing.state == "waiting_ci" and config.workflow_mode == "production":
                resumed = _resume_waiting_github(control_root=control_root, config=config, run=existing, store=store, finalize_run=False)
                if resumed.get("state") == "spec_completed":
                    plan_path = _safe_artifact_directory(control_root, config, existing.run_id) / "spec-plan.json"
                    current = store.find_by_run_id(existing.run_id)
                    assert current is not None
                    return {"created": False, **_run_production_queue(control_root=control_root, config=config,
                        brief_digest=brief_digest, run=current, store=store, spec_plan=load_json(plan_path))}
                return {"created": False, **resumed}
            if existing.state == "spec_completed" and config.workflow_mode == "production":
                plan_path = _safe_artifact_directory(control_root, config, existing.run_id) / "spec-plan.json"
                return {"created": False, **_run_production_queue(
                    control_root=control_root, config=config, brief_digest=brief_digest, run=existing,
                    store=store, spec_plan=load_json(plan_path))}
            if existing.state == "cleanup_pending":
                if _migration_source_archive_retry_pending(store=store, run_id=existing.run_id):
                    migration_cleanup = _retry_migration_source_archive(config=config, store=store, run=existing)
                    if migration_cleanup.get("migration_cleanup") is not None:
                        if migration_cleanup.get("state") == "ready_for_next":
                            current = store.find_by_run_id(existing.run_id) or existing
                            if config.workflow_mode == "production":
                                plan_path = _safe_artifact_directory(control_root, config, existing.run_id) / "spec-plan.json"
                                if plan_path.is_file():
                                    return {"created": False, **_run_production_queue(
                                        control_root=control_root, config=config, brief_digest=brief_digest,
                                        run=current, store=store, spec_plan=load_json(plan_path),
                                    )}
                            return {"created": False, **_advance_second_stage(
                                control_root=control_root, config=config, run=current,
                                brief_digest=brief_digest, store=store,
                            )}
                        return {"created": False, **migration_cleanup}
                if config.workflow_mode != "production":
                    return {"created": False, **store.public_status(existing.run_id)}
                cleanup = _retry_production_cleanup(control_root=control_root, config=config, run=existing, store=store)
                if cleanup.get("state") == "spec_completed":
                    plan_path = _safe_artifact_directory(control_root, config, existing.run_id) / "spec-plan.json"
                    current = store.find_by_run_id(existing.run_id)
                    assert current is not None
                    return {"created": False, **_run_production_queue(control_root=control_root, config=config,
                        brief_digest=brief_digest, run=current, store=store, spec_plan=load_json(plan_path))}
                return {"created": False, **cleanup}
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
            if migration is not None and config.execution_backend == "codex_sdk" and existing.state in {"starting", "failed"}:
                persisted_migration = store.thread_migration(str(migration.get("migration_key") or ""))
                if persisted_migration is not None and persisted_migration.get("state") == "uncertain":
                    raise RunnerError("thread_successor_uncertain", "successor creation is uncertain; reconcile the recorded migration before retry")
                workers = store.workers_for_run(existing.run_id)
                current_worker = workers[-1] if workers else {}
                if not current_worker.get("external_turn_id"):
                    successor = (str(persisted_migration.get("successor_thread_id") or "") if persisted_migration else "")
                    if not successor:
                        successor = _prepare_clean_migration(
                            store=store, config=config, run=existing, migration=migration,
                            stage=existing.current_step, input_revision=brief_digest,
                        ) or ""
                    if not successor:
                        raise RunnerError("thread_successor_missing", "owner transfer has no successor identity")
                    resumed = _continue_initial_clean_migration(
                        control_root=control_root, config=config, run=existing, brief=brief,
                        brief_digest=brief_digest, store=store, successor_thread_id=successor,
                    )
                    return {"created": False, **resumed}

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
                decision = _record_recovery_failure(run=existing, store=store, operation_id=f"start:{existing.run_id}", error=exc)
                if decision.action in {RecoveryAction.WAIT_RETRY, RecoveryAction.SERVICE_WAIT}:
                    store.fail_run(existing.run_id, f"start:{existing.run_id}", state=decision.action.value)
                    return {"created": False, **store.public_status(existing.run_id)}
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
        if takeover_record is not None:
            _write_json_atomic(
                _safe_artifact_directory(control_root, config, requested_run_id) / "takeover-context.json",
                {
                    "schema_version": "spec-runner-takeover-context/v1",
                    "run_id": requested_run_id,
                    "takeover_key": takeover_key,
                    "record": takeover_record,
                },
            )
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
        successor_thread_id: str | None = None
        try:
            if migration is not None:
                if config.execution_backend != "codex_sdk":
                    raise RunnerError("thread_migration_backend_invalid", "clean thread migration requires the Codex SDK backend")
                successor_thread_id = _prepare_clean_migration(
                    store=store, config=config, run=record, migration=migration,
                    stage=stage_name, input_revision=brief_digest,
                )
            if config.delivery_plan is not None:
                return {"created": True, **_run_delivery_plan(control_root=control_root, config=config, run=record, store=store)}
            if config.execution_backend == "deterministic_test":
                finished = _execute_deterministic_example(
                    control_root=control_root, config=config, brief=brief, brief_digest=brief_digest, run=record, store=store
                )
            elif config.workflow_mode == "production":
                finished = _execute_codex_planning(
                    control_root=control_root, config=config, brief=brief, brief_digest=brief_digest, run=record, store=store,
                    thread_id=successor_thread_id,
                )
            else:
                finished = _execute_codex_example(
                    control_root=control_root, config=config, brief=brief, brief_digest=brief_digest, run=record, store=store,
                    thread_id=successor_thread_id,
                )
        except RunnerError as exc:
            decision = _record_recovery_failure(run=record, store=store, operation_id=operation_id, error=exc)
            state = decision.action.value if decision.action in {
                RecoveryAction.WAIT_RETRY,
                RecoveryAction.SERVICE_WAIT,
                RecoveryAction.WAIT_FOR_CONFIG,
            } else "failed"
            store.fail_run(record.run_id, operation_id, state=state)
            if decision.action in {RecoveryAction.WAIT_RETRY, RecoveryAction.SERVICE_WAIT}:
                return {"created": True, **store.public_status(record.run_id)}
            raise
        if finished.state in {"paused", "cancelled"}:
            if finished.state == "cancelled":
                return {"created": True, **_finalize_cancelled_codex(config=config, run=finished, store=store)}
            return {"created": True, **store.public_status(finished.run_id)}
        if finished.state == "needs_input":
            return {"created": True, **store.public_status(finished.run_id)}
        if finished.state == "planned":
            plan_path = _safe_artifact_directory(control_root, config, finished.run_id) / "spec-plan.json"
            if config.workflow_mode == "production":
                return {"created": True, **_run_production_queue(
                    control_root=control_root, config=config, brief_digest=brief_digest, run=finished,
                    store=store, spec_plan=load_json(plan_path))}
            ticketed = _execute_codex_tickets(control_root=control_root, config=config, brief_digest=brief_digest,
                run=finished, store=store, spec_plan=load_json(plan_path))
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


def _acceptance_upgrade_compatible(existing: RunRecord, config: RunnerConfig) -> bool:
    """Allow the acceptance addition or a timeout-only acceptance update."""
    return (
        config.workflow_mode == "production"
        and bool(config.acceptance_ids)
        and bool(config.acceptance_checks)
        and existing.config_digest in {
            config.legacy_acceptance_digest,
            config.acceptance_timeout_compatible_digest,
        }
    )


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
    return drive(brief_file=brief_file, config_file=config_file, control_root=control_root, launch_key=launch_key)


def _launch_claim(*, control_root: Path, run_id: str, child_pid: int, launch_token: str | None = None,
                  launch_key: str | None = None) -> dict[str, object] | None:
    """Read a detached launch claim without creating control state."""
    try:
        store = Store.open(control_root, create=False)
    except RunnerError:
        return None
    try:
        record = store.find_by_run_id(run_id)
        if record is None and launch_key:
            record = store.find_by_launch_key(launch_key)
        runtime = store.runtime_for_run(run_id) if record else None
        if record is not None:
            runtime = store.runtime_for_run(record.run_id)
        token_claim = (
            isinstance(launch_token, str)
            and bool(launch_token)
            and isinstance(runtime, dict)
            and record is not None
            and str(runtime.get("owner_token", "")).startswith(f"{record.run_id}:launch:{launch_token}:")
        )
        # On Windows a detached interpreter can be started through a launcher
        # process whose PID is returned by Popen while the interpreter that
        # owns the Runner lease receives a different PID. The launch token is
        # passed only to this child and persisted in its runtime owner record,
        # providing an exact durable identity for that case. A matching PID
        # remains sufficient for older direct-start behavior.
        if record and runtime and (int(runtime["pid"]) == child_pid or token_claim):
            return {
                "started": True,
                "pid": child_pid,
                "run_id": record.run_id,
                "run": store.public_status(record.run_id),
            }
        return None
    finally:
        store.close()


def _terminate_unclaimed_child(child: subprocess.Popen[bytes], *, timeout_seconds: float = 2.0) -> bool:
    """Stop only a child that never claimed a durable Runner identity."""
    if child.poll() is not None:
        return True
    try:
        child.terminate()
        child.wait(timeout=timeout_seconds)
        return True
    except subprocess.TimeoutExpired:
        try:
            child.kill()
            child.wait(timeout=timeout_seconds)
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False
    except OSError:
        return False


def launch(*, brief_file: Path, config_file: Path, control_root: Path, launch_key: str,
           handshake_timeout_seconds: float = 10.0) -> dict[str, object]:
    """Start a detached Runner and return only after its durable handshake."""
    launch_key = _validate_launch_key(launch_key)
    if handshake_timeout_seconds <= 0:
        raise RunnerError("launch_timeout_invalid", "detached launch handshake timeout must be positive")
    # The child runs from control_root, so resolve inputs before spawning it.
    brief_file = brief_file.expanduser().resolve()
    config_file = config_file.expanduser().resolve()
    control_root = control_root.expanduser().resolve()
    # Validate all user inputs before creating the child or control files.
    read_brief(brief_file)
    RunnerConfig.from_file(config_file, control_root)
    run_id = str(uuid.uuid4())
    launch_token = secrets.token_hex(32)
    log_root = control_root / "launcher-logs"
    log_root.mkdir(parents=True, exist_ok=True)
    stdout_path = log_root / f"{run_id}.stdout.log"
    stderr_path = log_root / f"{run_id}.stderr.log"
    command = [
        sys.executable,
        "-m",
        "spec_runner.cli",
        "drive",
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
        "--launch-token",
        launch_token,
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
    deadline = time.monotonic() + handshake_timeout_seconds
    while time.monotonic() < deadline:
        claimed = _launch_claim(control_root=control_root, run_id=run_id, child_pid=child.pid,
                                launch_token=launch_token, launch_key=launch_key)
        if claimed is not None:
            return {"log_path": os.fspath(stdout_path), **claimed}
        if child.poll() is not None:
            raise RunnerError(
                "launch_handshake_failed",
                "detached Runner exited before claiming the run",
                details={"exit_code": child.returncode, "stderr_log": os.fspath(stderr_path)},
            )
        time.sleep(0.05)
    claimed = _launch_claim(control_root=control_root, run_id=run_id, child_pid=child.pid,
                            launch_token=launch_token, launch_key=launch_key)
    if claimed is not None:
        return {"log_path": os.fspath(stdout_path), **claimed}
    terminated = _terminate_unclaimed_child(child)
    if not terminated:
        raise RunnerError(
            "launch_cleanup_failed",
            "detached Runner missed its handshake and could not be stopped safely",
            details={"pid": child.pid, "stdout_log": os.fspath(stdout_path), "stderr_log": os.fspath(stderr_path)},
        )
    raise RunnerError(
        "launch_handshake_timeout",
        "detached Runner did not claim the run before the handshake deadline",
        details={"pid": child.pid, "stdout_log": os.fspath(stdout_path), "stderr_log": os.fspath(stderr_path), "terminated": True},
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
