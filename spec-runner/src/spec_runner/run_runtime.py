"""Durable run lifecycle behind the compatibility workflow entry.

``workflow.py`` historically contained the complete start implementation.
This module owns the lifecycle policy for opening a run, reconciling an
existing run, selecting its first stage, and releasing the writer leases.  It
deliberately calls the existing workflow adapters lazily: the public receipt
and SQLite contracts remain in one place while the lifecycle interface becomes
small enough to exercise through ``Runner``.
"""

from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path

from .config import RunnerConfig, read_brief
from .errors import RunnerError
from .models import RunContext
from .recovery import RecoveryAction
from .recovery_runtime import RecoveryEpisode
from .scope_lock import ScopeLock
from .store import RunRecord, Store, now
from .stage_progression import StageProgression
from .workflow_port import LegacyWorkflowPort


class RunRuntime:
    """Own one durable start/reconcile lifecycle.

    The interface is one operation: start or reopen the run identified by the
    normalized launch key.  Stage implementations, Git, SDK, and receipt
    writers remain adapters behind the compatibility workflow module.
    """

    def __init__(self, port: LegacyWorkflowPort | None = None) -> None:
        self._port = port or LegacyWorkflowPort()

    def start(
        self,
        *,
        brief_file: Path,
        config_file: Path,
        control_root: Path,
        launch_key: str,
        run_id: str | None = None,
        takeover_key: str | None = None,
        launch_token: str | None = None,
        migration: dict[str, object] | None = None,
    ) -> dict[str, object]:
        port = self._port
        launch_key = port.validate_launch_key(launch_key)
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
                config_matches_legacy_acceptance_upgrade = port.acceptance_upgrade_compatible(existing, config)
                if existing.input_digest != brief_digest or (
                    existing.config_digest != config.digest
                    and not config_matches_legacy_acceptance_upgrade
                ):
                    raise RunnerError(
                        "launch_key_input_conflict",
                        "launch_key already belongs to different normalized input",
                        details={"run_id": existing.run_id},
                    )
                if takeover_key and port.takeover_context(
                    control_root=control_root,
                    config=config,
                    run=existing,
                    expected_takeover_key=takeover_key,
                ) is None:
                    raise RunnerError(
                        "takeover_context_missing",
                        "existing takeover continuation has no durable context artifact",
                        details={"run_id": existing.run_id, "takeover_key": takeover_key},
                    )
                port.reconcile_continuation_bundles(
                    control_root=control_root,
                    config=config,
                    run=existing,
                    store=store,
                )
                route = StageProgression.select(
                    existing,
                    config,
                    has_control=(
                        store.control_for_run(existing.run_id) is not None
                        if existing.state in {"needs_input", "paused"}
                        else False
                    ),
                    migration_requested=(migration is not None),
                    migration_archive_pending=(
                        port.migration_source_archive_retry_pending(
                            store=store,
                            run_id=existing.run_id,
                        )
                        if existing.state == "cleanup_pending"
                        else False
                    ),
                )
                if route.kind == "terminal":
                    if launch_token:
                        store.register_runtime(
                            existing.run_id,
                            pid=os.getpid(),
                            owner_token=owner_token,
                            log_path=os.fspath(control_root / existing.log_path),
                        )
                    return {"created": False, **store.public_status(existing.run_id)}
                global_lease = port.acquire_global_lease(
                    scope=lease_scope,
                    owner_token=owner_token,
                    stale_after_seconds=stale_after_seconds,
                )
                store.acquire_lease(
                    scope=lease_scope,
                    run_id=existing.run_id,
                    owner_token=owner_token,
                    stale_after_seconds=stale_after_seconds,
                )
                store.register_runtime(
                    existing.run_id,
                    pid=os.getpid(),
                    owner_token=owner_token,
                    log_path=os.fspath(control_root / existing.log_path),
                )
                heartbeat_stop, heartbeat_thread = port.start_lease_heartbeat(
                    control_root=control_root,
                    scope=lease_scope,
                    owner_token=owner_token,
                    global_path=global_lease,
                )
                if RecoveryEpisode(run=existing, store=store).waits():
                    return {"created": False, **store.public_status(existing.run_id)}
                existing = store.find_by_run_id(existing.run_id) or existing
                route = StageProgression.select(
                    existing,
                    config,
                    has_control=(
                        store.control_for_run(existing.run_id) is not None
                        if existing.state in {"needs_input", "paused"}
                        else False
                    ),
                    migration_requested=migration is not None,
                    migration_archive_pending=(
                        port.migration_source_archive_retry_pending(
                            store=store,
                            run_id=existing.run_id,
                        )
                        if existing.state == "cleanup_pending"
                        else False
                    ),
                )
                try:
                    stage_result = port.execute_stage(
                        RunContext(
                            control_root=control_root,
                            config=config,
                            brief=brief,
                            brief_digest=brief_digest,
                            run=existing,
                            store=store,
                            migration=migration,
                        ),
                        route,
                    )
                except RunnerError as exc:
                    recovery_run = port.latest_durable_run(store=store, run=existing)
                    decision = RecoveryEpisode(run=recovery_run, store=store).record_failure(
                        operation_id=f"start:{recovery_run.run_id}",
                        error=exc,
                    )
                    if decision.action in {RecoveryAction.WAIT_RETRY, RecoveryAction.SERVICE_WAIT}:
                        store.fail_run(
                            recovery_run.run_id,
                            f"start:{recovery_run.run_id}",
                            state=decision.action.value,
                        )
                        return {"created": False, **store.public_status(recovery_run.run_id)}
                    store.set_run_state(recovery_run.run_id, "blocked")
                    store.append_event(
                        run_id=recovery_run.run_id,
                        event_key=f"recovery:{recovery_run.run_id}:blocked",
                        event_type="recovery_blocked",
                        payload={"code": exc.code, "message": exc.message},
                    )
                    raise
                if stage_result is not None:
                    return {"created": False, **stage_result.public()}
                if route.kind == "blocked_recovery" and existing.current_step == "codex_planning":
                    retry_thread = port.blocked_planning_retry_thread(
                        control_root=control_root,
                        config=config,
                        run=existing,
                        store=store,
                        brief_digest=brief_digest,
                    )
                    if retry_thread is not None:
                        resumed = port.resume_codex_stage(
                            control_root=control_root,
                            config=config,
                            run=existing,
                            brief=brief,
                            brief_digest=brief_digest,
                            store=store,
                            thread_id=retry_thread,
                        )
                        return {"created": False, **resumed}
                if route.kind == "blocked_recovery":
                    retry_identity = port.blocked_implementation_retry_identity(
                        control_root=control_root,
                        config=config,
                        run=existing,
                        store=store,
                    )
                    if retry_identity is not None:
                        resumed = port.resume_codex_stage(
                            control_root=control_root,
                            config=config,
                            run=existing,
                            brief=brief,
                            brief_digest=brief_digest,
                            store=store,
                            thread_id=retry_identity[0],
                            spec_key=retry_identity[1],
                        )
                        return {"created": False, **resumed}
                try:
                    recovered_status = port.recover_after_process_exit(
                        control_root=control_root,
                        config=config,
                        run=existing,
                        brief=brief,
                        brief_digest=brief_digest,
                        store=store,
                    )
                except RunnerError as exc:
                    recovery_run = port.latest_durable_run(store=store, run=existing)
                    decision = RecoveryEpisode(run=recovery_run, store=store).record_failure(
                        operation_id=f"start:{recovery_run.run_id}",
                        error=exc,
                    )
                    if decision.action in {RecoveryAction.WAIT_RETRY, RecoveryAction.SERVICE_WAIT}:
                        store.fail_run(
                            recovery_run.run_id,
                            f"start:{recovery_run.run_id}",
                            state=decision.action.value,
                        )
                        return {"created": False, **store.public_status(recovery_run.run_id)}
                    store.set_run_state(recovery_run.run_id, "blocked")
                    store.append_event(
                        run_id=recovery_run.run_id,
                        event_key=f"recovery:{recovery_run.run_id}:blocked",
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
            stage_name = StageProgression.initial_stage(config)
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
                port.write_json_atomic(
                    port.safe_artifact_directory(control_root, config, requested_run_id) / "takeover-context.json",
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
                store.acquire_lease(
                    scope=lease_scope,
                    run_id=requested_run_id,
                    owner_token=owner_token,
                    stale_after_seconds=stale_after_seconds,
                )
            except RunnerError:
                store.fail_run(requested_run_id, operation_id, state="blocked_writer_busy")
                raise
            global_lease = port.acquire_global_lease(
                scope=lease_scope,
                owner_token=owner_token,
                stale_after_seconds=stale_after_seconds,
            )
            heartbeat_stop, heartbeat_thread = port.start_lease_heartbeat(
                control_root=control_root,
                scope=lease_scope,
                owner_token=owner_token,
                global_path=global_lease,
            )
            store.write_log(
                control_root,
                requested_run_id,
                {"event": "run_started", "backend_kind": config.execution_backend},
            )
            port.test_fault_pause(control_root=control_root, run_id=requested_run_id, point="after_first_intent")
            successor_thread_id: str | None = None
            try:
                if migration is not None:
                    if config.execution_backend != "codex_sdk":
                        raise RunnerError("thread_migration_backend_invalid", "clean thread migration requires the Codex SDK backend")
                    successor_thread_id = port.prepare_clean_migration(
                        store=store,
                        config=config,
                        run=record,
                        migration=migration,
                        stage=stage_name,
                        input_revision=brief_digest,
                    )
                if config.delivery_plan is not None:
                    return {
                        "created": True,
                        **port.run_delivery_plan(
                            control_root=control_root,
                            config=config,
                            run=record,
                            store=store,
                        ),
                    }
                if config.execution_backend == "deterministic_test":
                    finished = port.execute_deterministic_example(
                        control_root=control_root,
                        config=config,
                        brief=brief,
                        brief_digest=brief_digest,
                        run=record,
                        store=store,
                    )
                elif config.workflow_mode == "production":
                    finished = port.production_runtime(
                        control_root=control_root,
                        config=config,
                        brief=brief,
                        brief_digest=brief_digest,
                        run=record,
                        store=store,
                    ).plan(thread_id=successor_thread_id)
                else:
                    finished = port.execute_codex_example(
                        control_root=control_root,
                        config=config,
                        brief=brief,
                        brief_digest=brief_digest,
                        run=record,
                        store=store,
                        thread_id=successor_thread_id,
                    )
            except RunnerError as exc:
                recovery_run = port.latest_durable_run(store=store, run=record)
                decision = RecoveryEpisode(run=recovery_run, store=store).record_failure(
                    operation_id=operation_id,
                    error=exc,
                )
                state = decision.action.value if decision.action in {
                    RecoveryAction.WAIT_RETRY,
                    RecoveryAction.SERVICE_WAIT,
                    RecoveryAction.WAIT_FOR_CONFIG,
                } else "failed"
                store.fail_run(recovery_run.run_id, operation_id, state=state)
                if decision.action in {RecoveryAction.WAIT_RETRY, RecoveryAction.SERVICE_WAIT}:
                    return {"created": True, **store.public_status(recovery_run.run_id)}
                raise
            if finished.state in {"paused", "cancelled"}:
                if finished.state == "cancelled":
                    return {
                        "created": True,
                        **port.finalize_cancelled_codex(
                            config=config,
                            run=finished,
                            store=store,
                        ),
                    }
                return {"created": True, **store.public_status(finished.run_id)}
            if finished.state == "needs_input":
                return {"created": True, **store.public_status(finished.run_id)}
            if finished.state == "planned":
                plan_path = port.safe_artifact_directory(control_root, config, finished.run_id) / "spec-plan.json"
                if config.workflow_mode == "production":
                    return {
                        "created": True,
                        **port.run_production_queue(
                            control_root=control_root,
                            config=config,
                            brief_digest=brief_digest,
                            run=finished,
                            store=store,
                            spec_plan=port.load_json(plan_path),
                        ),
                    }
                ticketed = port.execute_codex_tickets(
                    control_root=control_root,
                    config=config,
                    brief_digest=brief_digest,
                    run=finished,
                    store=store,
                    spec_plan=port.load_json(plan_path),
                )
                return {"created": True, **store.public_status(ticketed.run_id)}
            port.verify_and_archive(
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
                payload={
                    "from_step": finished.current_step,
                    "to_step": "deterministic_second" if config.execution_backend == "deterministic_test" else "codex_second",
                },
            )
            final_status = port.advance_second_stage(
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
            port.release_global_lease(global_lease, owner_token)
            store.close()
