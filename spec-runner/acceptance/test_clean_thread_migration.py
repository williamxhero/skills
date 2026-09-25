from __future__ import annotations

from pathlib import Path
import hashlib
import json

import pytest

from spec_runner.errors import RunnerError
from spec_runner.store import RunRecord, Store, now


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
