"""Typed adapter for the legacy workflow implementation.

``RunRuntime`` owns lifecycle policy and should not know the private function
graph in ``workflow``.  This adapter is the only compatibility seam for the
remaining implementation.  Keeping the imports lazy preserves the existing
test and acceptance monkeypatch points while allowing the implementation to
move behind smaller modules later.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import RunContext


class LegacyWorkflowPort:
    """Expose lifecycle operations without leaking workflow internals."""

    @staticmethod
    def _workflow():
        from . import workflow

        return workflow

    def validate_launch_key(self, value: str) -> str:
        return self._workflow()._validate_launch_key(value)

    def acceptance_upgrade_compatible(self, existing: Any, config: Any) -> bool:
        return self._workflow()._acceptance_upgrade_compatible(existing, config)

    def takeover_context(self, **kwargs: Any) -> Any:
        return self._workflow()._takeover_context(**kwargs)

    def reconcile_continuation_bundles(self, **kwargs: Any) -> Any:
        return self._workflow()._reconcile_continuation_bundles(**kwargs)

    def migration_source_archive_retry_pending(self, **kwargs: Any) -> bool:
        return self._workflow()._migration_source_archive_retry_pending(**kwargs)

    def local_issue_closure_pending(self, **kwargs: Any) -> bool:
        return self._workflow()._local_issue_closure_pending(**kwargs)

    def acquire_global_lease(self, **kwargs: Any) -> Any:
        return self._workflow()._acquire_global_lease(**kwargs)

    def start_lease_heartbeat(self, **kwargs: Any) -> Any:
        return self._workflow()._start_lease_heartbeat(**kwargs)

    def execute_stage(self, context: RunContext, route: Any) -> Any:
        return self._workflow().execute_stage(context, route)

    def latest_durable_run(self, **kwargs: Any) -> Any:
        return self._workflow()._latest_durable_run(**kwargs)

    def blocked_planning_retry_thread(self, **kwargs: Any) -> str | None:
        return self._workflow()._blocked_planning_retry_thread(**kwargs)

    def resume_codex_stage(self, **kwargs: Any) -> Any:
        return self._workflow()._resume_codex_stage(**kwargs)

    def blocked_implementation_retry_identity(self, **kwargs: Any) -> Any:
        return self._workflow()._blocked_implementation_retry_identity(**kwargs)

    def recover_after_process_exit(self, **kwargs: Any) -> Any:
        return self._workflow()._recover_after_process_exit(**kwargs)

    def write_json_atomic(self, path: Path, document: dict[str, object]) -> None:
        self._workflow()._write_json_atomic(path, document)

    def safe_artifact_directory(self, control_root: Path, config: Any, run_id: str) -> Path:
        return self._workflow()._safe_artifact_directory(control_root, config, run_id)

    def test_fault_pause(self, **kwargs: Any) -> None:
        self._workflow()._test_fault_pause(**kwargs)

    def prepare_clean_migration(self, **kwargs: Any) -> str | None:
        return self._workflow()._prepare_clean_migration(**kwargs)

    def run_delivery_plan(self, **kwargs: Any) -> dict[str, object]:
        return self._workflow()._run_delivery_plan(**kwargs)

    def execute_deterministic_example(self, **kwargs: Any) -> Any:
        return self._workflow()._execute_deterministic_example(**kwargs)

    def production_runtime(self, **kwargs: Any) -> Any:
        return self._workflow()._production_runtime(**kwargs)

    def execute_codex_example(self, **kwargs: Any) -> Any:
        return self._workflow()._execute_codex_example(**kwargs)

    def finalize_cancelled_codex(self, **kwargs: Any) -> dict[str, object]:
        return self._workflow()._finalize_cancelled_codex(**kwargs)

    def run_production_queue(self, **kwargs: Any) -> dict[str, object]:
        return self._workflow()._run_production_queue(**kwargs)

    def load_json(self, path: Path) -> dict[str, object]:
        return self._workflow().load_json(path)

    def execute_codex_tickets(self, **kwargs: Any) -> Any:
        return self._workflow()._execute_codex_tickets(**kwargs)

    def verify_and_archive(self, **kwargs: Any) -> Any:
        return self._workflow()._verify_and_archive(**kwargs)

    def advance_second_stage(self, **kwargs: Any) -> dict[str, object]:
        return self._workflow()._advance_second_stage(**kwargs)

    def resume_waiting_github(self, **kwargs: Any) -> dict[str, object]:
        return self._workflow()._resume_waiting_github(**kwargs)

    def resume_reviewed_delivery(self, **kwargs: Any) -> dict[str, object]:
        return self._workflow()._resume_reviewed_delivery(**kwargs)

    def retry_migration_source_archive(self, **kwargs: Any) -> dict[str, object]:
        return self._workflow()._retry_migration_source_archive(**kwargs)

    def retry_production_cleanup(self, **kwargs: Any) -> dict[str, object]:
        return self._workflow()._retry_production_cleanup(**kwargs)

    def continue_initial_clean_migration(self, **kwargs: Any) -> dict[str, object]:
        return self._workflow()._continue_initial_clean_migration(**kwargs)

    def release_global_lease(self, lease: Any, owner_token: str) -> None:
        self._workflow()._release_global_lease(lease, owner_token)
