"""Stage execution seam for the durable Runner lifecycle.

The compatibility workflow still owns the individual stage implementations.
This module owns the route-to-stage decision after a run has been opened.  It
keeps the large legacy entry point focused on input normalization, leases,
failure recording, and final public projections.
"""

from __future__ import annotations

import json
from typing import Protocol

from .errors import RunnerError
from .models import RunContext, StageResult
from .production_runtime import ProductionWorkflow
from .stage_progression import StageRoute
from .workflow_port import LegacyWorkflowPort


class StageExecutor(Protocol):
    """Execute a persisted route behind one typed interface."""

    def execute(self, context: RunContext, route: StageRoute) -> StageResult | None: ...


class WorkflowStageExecutor:
    """Adapt legacy stage functions to the typed stage execution seam.

    The adapter is intentionally narrow.  Moving a stage implementation behind
    this interface does not alter its receipt or SQLite contract, and the
    import stays lazy so the compatibility module remains cycle-free.
    """

    def __init__(self, port: LegacyWorkflowPort | None = None) -> None:
        self._port = port or LegacyWorkflowPort()

    def execute(self, context: RunContext, route: StageRoute) -> StageResult | None:
        port = self._port
        run = context.run
        config = context.config
        store = context.store
        control_root = context.control_root
        brief_digest = context.brief_digest

        if route.kind == "prepared_plan":
            production = self._production_workflow(context)
            planned = production.plan()
            payload = self._production_workflow(context.with_run(planned)).start_queue(
                port.load_json(production.artifact_directory() / "spec-plan.json")
            )
            return StageResult.from_public(payload)

        if route.kind == "prepared_ticket":
            production = self._production_workflow(context)
            plan_path = production.artifact_directory() / "spec-plan.json"
            if not plan_path.is_file():
                raise RunnerError("spec_plan_missing", "prepared TicketPlan recovery needs the persisted SpecPlan")
            return StageResult.from_public(production.start_queue(port.load_json(plan_path)))

        if route.kind == "tracker_intake":
            production = self._production_workflow(context)
            adopted = production.intake()
            current = store.find_by_run_id(run.run_id) or adopted
            plan_path = production.artifact_directory() / "spec-plan.json"
            payload = self._production_workflow(context.with_run(current)).start_queue(
                port.load_json(plan_path)
            )
            return StageResult.from_public(payload)

        if route.kind == "ready_for_next":
            payload = port.advance_second_stage(
                control_root=control_root, config=config, run=run,
                brief_digest=brief_digest, store=store,
            )
            return StageResult.from_public(payload)

        if route.kind in {"planned", "production_queue"} and (
            route.kind == "planned" or run.state in {"tickets_ready", "spec_completed"}
        ):
            production = self._production_workflow(context)
            plan_path = production.artifact_directory() / "spec-plan.json"
            if route.kind == "planned" and not plan_path.is_file():
                raise RunnerError("spec_plan_missing", "planned run has no persisted SpecPlan")
            payload = production.start_queue(port.load_json(plan_path))
            return StageResult.from_public(payload)

        if route.kind == "waiting_github":
            production = self._production_workflow(context)
            payload = production.resume_waiting_github(finalize_run=False)
            payload = self._continue_production_after_spec(production, payload)
            return StageResult.from_public(payload)

        if route.kind == "reviewed":
            production = self._production_workflow(context)
            payload = production.resume_reviewed_delivery()
            payload = self._continue_production_after_spec(production, payload)
            return StageResult.from_public(payload)

        if route.kind in {"cleanup_migration", "cleanup_production", "cleanup_status"}:
            if route.kind == "cleanup_migration":
                migration_cleanup = port.retry_migration_source_archive(
                    config=config, store=store, run=run,
                )
                if migration_cleanup.get("migration_cleanup") is not None:
                    if migration_cleanup.get("state") == "ready_for_next":
                        current = store.find_by_run_id(run.run_id) or run
                        if config.workflow_mode == "production":
                            plan_path = port.safe_artifact_directory(control_root, config, run.run_id) / "spec-plan.json"
                            if plan_path.is_file():
                                production = self._production_workflow(context.with_run(current))
                                payload = production.start_queue(port.load_json(plan_path))
                                return StageResult.from_public(payload)
                        payload = port.advance_second_stage(
                            control_root=control_root, config=config, run=current,
                            brief_digest=brief_digest, store=store,
                        )
                        return StageResult.from_public(payload)
                    return StageResult.from_public(migration_cleanup)
            if route.kind == "cleanup_status":
                return StageResult.from_public(store.public_status(run.run_id))
            production = self._production_workflow(context)
            payload = production.retry_cleanup()
            payload = self._continue_production_after_spec(production, payload)
            return StageResult.from_public(payload)

        if route.kind == "needs_input":
            worker_result = port.safe_artifact_directory(control_root, config, run.run_id) / "worker-result.json"
            questions: list[dict[str, object]] = []
            if worker_result.is_file():
                try:
                    document = json.loads(worker_result.read_text(encoding="utf-8"))
                    questions = [
                        item for item in document.get("questions", [])
                        if isinstance(item, dict) and isinstance(item.get("id"), str)
                    ]
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    questions = []
            answer_ids = {str(item["question_id"]) for item in store.answers_for_run(run.run_id)}
            if questions and {str(item["id"]) for item in questions}.issubset(answer_ids) and config.execution_backend == "codex_sdk":
                payload = port.resume_codex_stage(
                    control_root=control_root, config=config, run=run, brief=context.brief,
                    brief_digest=brief_digest, store=store,
                )
                return StageResult.from_public(payload)
            return StageResult.from_public(store.public_status(run.run_id))

        if route.kind == "migration":
            migration = context.migration or {}
            persisted = store.thread_migration(str(migration.get("migration_key") or ""))
            if persisted is not None and persisted.get("state") == "uncertain":
                raise RunnerError(
                    "thread_successor_uncertain",
                    "successor creation is uncertain; reconcile the recorded migration before retry",
                )
            workers = store.workers_for_run(run.run_id)
            current_worker = workers[-1] if workers else {}
            if not current_worker.get("external_turn_id"):
                successor = str(persisted.get("successor_thread_id") or "") if persisted else ""
                if not successor:
                    successor = port.prepare_clean_migration(
                        store=store, config=config, run=run, migration=migration,
                        stage=run.current_step, input_revision=brief_digest,
                    ) or ""
                if not successor:
                    raise RunnerError("thread_successor_missing", "owner transfer has no successor identity")
                payload = port.continue_initial_clean_migration(
                    control_root=control_root, config=config, run=run, brief=context.brief,
                    brief_digest=brief_digest, store=store, successor_thread_id=successor,
                )
                return StageResult.from_public(payload)
            return None

        if route.kind == "paused":
            if config.execution_backend == "codex_sdk":
                payload = port.resume_codex_stage(
                    control_root=control_root, config=config, run=run, brief=context.brief,
                    brief_digest=brief_digest, store=store,
                )
            else:
                payload = port.advance_second_stage(
                    control_root=control_root, config=config, run=run,
                    brief_digest=brief_digest, store=store,
                )
            return StageResult.from_public(payload)

        return None

    def _production_workflow(self, context: RunContext) -> ProductionWorkflow:
        """Build the deep production module at the production seam."""
        return self._port.production_runtime(
            control_root=context.control_root,
            config=context.config,
            brief=context.brief,
            brief_digest=context.brief_digest,
            run=context.run,
            store=context.store,
        )

    def _continue_production_after_spec(self, production, payload: dict[str, object]) -> dict[str, object]:
        """Continue the queue through the production module."""
        if payload.get("state") != "spec_completed":
            return payload
        return production.continue_after_spec(payload)


def execute_stage(context: RunContext, route: StageRoute, executor: StageExecutor | None = None) -> StageResult | None:
    """Execute a route using the default adapter or an injected test adapter."""

    return (executor or WorkflowStageExecutor()).execute(context, route)
