"""Process-exit recovery coordination behind the Runner seam.

The policy and durable episode remain in ``recovery.py`` and
``recovery_runtime.py``.  This module owns only the evidence-driven
coordination that maps a persisted run to one safe next action.
"""

from __future__ import annotations

def recover_after_process_exit(*, control_root: Path, config: RunnerConfig, run: RunRecord, brief: str, brief_digest: str, store: Store) -> dict[str, object] | None:
    """Reconcile only evidence that can be proven locally; never replay an unknown SDK call."""
    from . import workflow
    CodexAdapter = workflow.CodexAdapter
    CodexWorkerResult = workflow.CodexWorkerResult
    Path = workflow.Path
    RunRecord = workflow.RunRecord
    RunnerConfig = workflow.RunnerConfig
    RunnerError = workflow.RunnerError
    Store = workflow.Store
    _adopt_existing_ticket_plan = workflow._adopt_existing_ticket_plan
    _advance_second_stage = workflow._advance_second_stage
    _archive_worker_readback = workflow._archive_worker_readback
    _execute_deterministic_example = workflow._execute_deterministic_example
    _implementation_spec_key = workflow._implementation_spec_key
    _implementation_workspace_path = workflow._implementation_workspace_path
    _planning_turn_requires_retry = workflow._planning_turn_requires_retry
    _reconcile_approved_review = workflow._reconcile_approved_review
    _reconcile_blocked_review = workflow._reconcile_blocked_review
    _reconcile_completed_implementation_turn = workflow._reconcile_completed_implementation_turn
    _reconcile_completed_planning_turn = workflow._reconcile_completed_planning_turn
    _reconcile_completed_ticket_turn = workflow._reconcile_completed_ticket_turn
    _reconcile_rejected_review = workflow._reconcile_rejected_review
    _reconcile_repair_turn = workflow._reconcile_repair_turn
    _repair_candidate = workflow._repair_candidate
    _repair_spec_key = workflow._repair_spec_key
    _resume_after_repair_candidate = workflow._resume_after_repair_candidate
    _resume_codex_stage = workflow._resume_codex_stage
    _run_delivery_plan = workflow._run_delivery_plan
    _safe_artifact_directory = workflow._safe_artifact_directory
    _verify_and_archive = workflow._verify_and_archive
    git_sha = workflow.git_sha
    independent_review = workflow.independent_review
    latest_worker = workflow.latest_worker
    load_json = workflow.load_json
    read_turn_evidence = workflow.read_turn_evidence
    validate_ticket_plan = workflow.validate_ticket_plan
    if config.delivery_plan is not None:
        store.append_event(
            run_id=run.run_id,
            event_key=f"recovery:{run.run_id}:delivery:detected",
            event_type="recovery_detected",
            payload={"step": "delivery_plan", "state": run.state},
        )
        return {"created": False, **_run_delivery_plan(control_root=control_root, config=config, run=run, store=store)}
    if config.execution_backend != "deterministic_test":
        if run.state in {"starting", "running", "cleanup_pending", "failed"} and run.current_step == "codex_planning":
            workers = store.workers_for_run(run.run_id)
            worker_id = f"codex_sdk:{run.run_id}:codex_planning"
            worker = latest_worker(
                workers=workers, backend_kind="codex_sdk",
                states={"running", "failed", "interrupted", "rejected"},
                exact_id=worker_id,
            )
            thread_id = str(worker.get("external_thread_id") or "") if worker else ""
            turn_id = str(worker.get("external_turn_id") or "") if worker else ""
            if worker is None or not thread_id or not turn_id:
                raise RunnerError("recovery_blocked", "the planning stage has no uniquely identified worker result")
            evidence = read_turn_evidence(
                adapter=CodexAdapter(), thread_id=thread_id, turn_id=turn_id,
                repository_path=config.repository_path,
                error_message="the persisted planning thread could not be reconciled; inspect it before retry",
            )
            inspection = evidence.inspection
            terminal_completed = evidence.has_status("completed")
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
            else:
                completed = _reconcile_completed_planning_turn(
                    control_root=control_root, config=config, run=run, store=store,
                    thread_id=thread_id, turn_id=turn_id, brief_digest=brief_digest,
                )
                return {"created": False, **store.public_status(completed.run_id)}
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
            worker = latest_worker(
                workers=workers, backend_kind="codex_sdk", states={"failed"},
                exact_id=(expected_worker_id if run.current_step == "codex_example" else None),
                prefix=(None if run.current_step == "codex_example" else f"{worker_prefix}:{run.current_step}:" if run.current_step in {"codex_ticket_planning", "codex_implementation"} else f"{worker_prefix}:{run.current_step}"),
            )
            if run.current_step == "codex_example":
                worker = latest_worker(
                    workers=workers, backend_kind="codex_sdk", states={"failed"},
                    exact_id=expected_worker_id,
                )
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
            evidence = read_turn_evidence(
                adapter=CodexAdapter(), thread_id=thread_id, turn_id=turn_id,
                repository_path=inspection_repository,
                error_message="the persisted SDK thread could not be reconciled; inspect it before retry",
            )
            inspection = evidence.inspection
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
        if run.state in {"starting", "running", "cleanup_pending", "blocked"} and run.current_step == "codex_review":
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
                expected_review = independent_review(
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
                review_path = artifact_directory / f"review-{spec_key}-{candidate_receipt['candidate_sha'][:12]}.json"
                if review_path.is_file():
                    persisted_review = load_json(review_path)
                    if any(persisted_review.get(key) != value for key, value in expected_review.items()):
                        raise RunnerError("recovery_blocked", "persisted completed review receipt changed")
                _archive_worker_readback(
                    store=store, run_id=run.run_id, operation_id=f"review:{run.run_id}:{spec_key}:{candidate_receipt['candidate_sha']}",
                    thread_id=thread_id, turn_id=turn_id, repository_path=workspace,
                )
                store.complete_codex_stage(
                    run.run_id,
                    f"review:{run.run_id}:{spec_key}:{candidate_receipt['candidate_sha']}",
                    thread_id=thread_id, turn_id=turn_id, state="reviewed",
                    step_name="codex_review", worker_id=str(worker["worker_id"]),
                )
                store.append_event(
                    run_id=run.run_id,
                    event_key=f"recovery:{run.run_id}:completed-review-turn:{turn_id}",
                    event_type="completed_sdk_turn_reconciled",
                    payload={"step": "codex_review", "spec_key": spec_key,
                             "thread_id": thread_id, "turn_id": turn_id},
                )
                return {"created": False, **store.public_status(run.run_id)}
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
        if run.state in {"starting", "running", "cleanup_pending"} and run.current_step == "codex_ticket_planning":
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
