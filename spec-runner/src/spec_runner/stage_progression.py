"""Typed stage selection for the durable Runner lifecycle.

The compatibility workflow still owns stage implementations. This module owns
the decision table that chooses the next implementation for a persisted run.
It is deliberately side-effect free, so the lifecycle seam can be tested
without opening SQLite or creating external work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .config import RunnerConfig
from .store import RunRecord


RouteKind = Literal[
    "terminal",
    "blocked_recovery",
    "ready_for_next",
    "planned",
    "production_queue",
    "waiting_github",
    "cleanup_migration",
    "cleanup_production",
    "cleanup_status",
    "needs_input",
    "migration",
    "paused",
    "recover",
]


@dataclass(frozen=True)
class StageRoute:
    """The next lifecycle action selected from durable run state."""

    kind: RouteKind
    state: str
    production: bool = False


class StageProgression:
    """Select the next stage without performing I/O or side effects."""

    @staticmethod
    def initial_stage(config: RunnerConfig) -> str:
        if config.delivery_plan is not None:
            return "delivery_plan"
        if config.execution_backend == "deterministic_test":
            return "deterministic_example"
        if config.workflow_mode == "production":
            return "codex_planning"
        return "codex_example"

    @staticmethod
    def select(
        run: RunRecord,
        config: RunnerConfig,
        *,
        has_control: bool = False,
        migration_requested: bool = False,
        migration_archive_pending: bool = False,
    ) -> StageRoute:
        state = run.state
        production = config.workflow_mode == "production"
        if state in {"completed", "cancelled", "blocked_writer_busy"}:
            return StageRoute("terminal", state, production)
        if state == "blocked" and config.execution_backend == "codex_sdk":
            return StageRoute("blocked_recovery", state, production)
        if state == "ready_for_next":
            return StageRoute("ready_for_next", state, production)
        if state == "planned":
            return StageRoute("planned", state, production)
        if state == "tickets_ready" and production:
            return StageRoute("production_queue", state, production)
        if state in {"waiting_ci", "waiting_merge_queue"} and production:
            return StageRoute("waiting_github", state, production)
        if state == "spec_completed" and production:
            return StageRoute("production_queue", state, production)
        if state == "cleanup_pending":
            if migration_archive_pending:
                return StageRoute("cleanup_migration", state, production)
            return StageRoute("cleanup_production" if production else "cleanup_status", state, production)
        if state == "needs_input" and not has_control:
            return StageRoute("needs_input", state, production)
        if migration_requested and config.execution_backend == "codex_sdk" and state in {"starting", "failed"}:
            return StageRoute("migration", state, production)
        if state == "paused" and not has_control:
            return StageRoute("paused", state, production)
        return StageRoute("recover", state, production)
