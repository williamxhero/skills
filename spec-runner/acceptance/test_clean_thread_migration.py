from __future__ import annotations

from pathlib import Path
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from spec_runner.errors import RunnerError
from spec_runner.store import RunRecord, Store, now
from spec_runner.workflow import _finalize_migration_business_progress, _retry_migration_source_archive


def _run(root: Path) -> RunRecord:
    timestamp = now()
    return RunRecord(
        run_id="acceptance-migration-run", launch_key="acceptance-migration",
        input_digest="brief-v1", config_digest="config-v1", repository_path=str(root),
        target_ref="HEAD", artifact_root="artifacts", backend_kind="codex_sdk",
        state="starting", current_step="codex_example", log_path="logs/run.jsonl",
        created_at=timestamp, updated_at=timestamp,
    )


def test_clean_migration_acceptance_covers_receipts_and_stale_generation(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "control", create=True)
    try:
        run = _run(tmp_path)
        store.create_run(run, "start:acceptance-migration-run")
        key = "acceptance:clean-migration"
        store.prepare_thread_migration(
            migration_key=key, run_id=run.run_id, stage="codex_example",
            source_thread_id="source-thread", handover_digest=hashlib.sha256(json.dumps({"thread_id": "source-thread", "accepted": True}, sort_keys=True).encode("utf-8")).hexdigest(),
            input_revision="brief-v1",
        )
        with pytest.raises(RunnerError, match="handover"):
            store.record_migration_successor(migration_key=key, successor_thread_id="successor-thread")
        store.record_migration_handover(
            migration_key=key,
            handover={"thread_id": "source-thread", "accepted": True},
        )
        store.record_migration_successor(
            migration_key=key, successor_thread_id="successor-thread",
            successor={"thread_id": "successor-thread", "turn_started": False, "forked": False},
        )
        store.complete_migration_owner_transfer(
            migration_key=key, expected_generation=0,
            owner_worker_id="codex_sdk:acceptance-migration-run:codex_example",
        )
        stale = store.record_migration_event(
            migration_key=key, generation=0, event_key="acceptance:stale",
            payload={"thread_id": "source-thread"},
        )
        current = store.record_migration_event(
            migration_key=key, generation=1, event_key="acceptance:current",
            payload={"thread_id": "successor-thread"},
        )
        assert stale["applied"] is False
        assert current["applied"] is True
        receipt = store.thread_migration(key)
        assert receipt is not None
        assert receipt["state"] == "owner_transferred"
        assert receipt["successor_thread_id"] == "successor-thread"
        assert receipt["owner_generation"] == 1
        assert receipt["handover"]["thread_id"] == "source-thread"
        assert any(event["event_type"] == "thread_migration_stale_event" for event in store.events_for_run(run.run_id))
    finally:
        store.close()


def test_business_progress_and_source_archive_are_separate_resumable_milestones(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "control", create=True)
    try:
        run = _run(tmp_path)
        store.create_run(run, "start:acceptance-migration-run")
        key = "acceptance:business-progress"
        handover = {"thread_id": "source-thread", "accepted": True}
        store.prepare_thread_migration(
            migration_key=key, run_id=run.run_id, stage="codex_example",
            source_thread_id="source-thread", handover_digest=hashlib.sha256(
                json.dumps(handover, sort_keys=True).encode("utf-8")
            ).hexdigest(), input_revision="brief-v1",
        )
        store.record_migration_handover(migration_key=key, handover=handover)
        store.record_migration_successor(
            migration_key=key, successor_thread_id="successor-thread",
            successor={"thread_id": "successor-thread", "turn_started": False},
        )
        store.complete_migration_owner_transfer(
            migration_key=key, expected_generation=0,
            owner_worker_id="codex_sdk:acceptance-migration-run:codex_example",
        )

        class ArchiveAdapter:
            calls = 0

            def archive_and_readback(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
                ArchiveAdapter.calls += 1
                if ArchiveAdapter.calls == 1:
                    raise RunnerError("archive_failed", "source archive temporarily unavailable")
                return {"thread_id": thread_id, "archived": True, "pages_read": 2}

        config = SimpleNamespace(repository_path=tmp_path)
        with patch("spec_runner.workflow.CodexAdapter", ArchiveAdapter):
            pending = _finalize_migration_business_progress(
                config=config, store=store, run=run, stage="codex_example",
                resume_state="ready_for_next", verification={"receipt": "verified"},
            )
            assert pending is not None
            assert pending["state"] == "cleanup_pending"
            assert store.migration_milestone(key, "business_progress_verified") is not None
            assert store.migration_milestone(key, "source_archived") is None

            cleaned = _retry_migration_source_archive(config=config, store=store, run=run)

        assert cleaned["state"] == "ready_for_next"
        assert cleaned["migration_cleanup"]["outcome"] == "cleaned"
        assert store.migration_milestone(key, "source_archived")["receipt"]["archive"]["pages_read"] == 2
        assert ArchiveAdapter.calls == 2
    finally:
        store.close()
