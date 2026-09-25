from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import hashlib
import uuid
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from spec_runner.errors import RunnerError
from spec_runner.store import RunRecord, Store, now
from spec_runner.config import RunnerConfig
from spec_runner import workflow
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




def _prepare_migration(store: Store, run: RunRecord, key: str) -> None:
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
        owner_worker_id=f"codex_sdk:{run.run_id}:codex_example",
    )

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
                resume_state="ready_for_next", verification={"verification": [{"run_id": run.run_id, "stage": "codex_example", "outcome": "verified"}]},
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


def test_migration_progress_requires_durable_verification_or_production_completion(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "control", create=True)
    try:
        run = _run(tmp_path)
        store.create_run(run, "start:acceptance-migration-run")
        _prepare_migration(store, run, "acceptance:verified-progress")
        config = SimpleNamespace(repository_path=tmp_path)
        with pytest.raises(RunnerError, match="durable verification receipt"):
            _finalize_migration_business_progress(
                config=config, store=store, run=run, stage="codex_example",
                resume_state="ready_for_next", verification={"run": {"state": "ready_for_next"}},
            )

        store2 = Store.open(tmp_path / "control-production", create=True)
        try:
            production_run = _run(tmp_path)
            production_run = replace(production_run, run_id="acceptance-production-migration", launch_key="acceptance-production-migration")
            store2.create_run(production_run, "start:acceptance-production-migration")
            _prepare_migration(store2, production_run, "acceptance:production-progress")
            with pytest.raises(RunnerError, match="completed SPEC receipt"):
                _finalize_migration_business_progress(
                    config=config, store=store2, run=production_run, stage="production_delivery",
                    resume_state="completed", verification={"run": {"state": "completed"}},
                )
        finally:
            store2.close()
    finally:
        store.close()


def test_public_restart_keeps_ordinary_production_cleanup_on_its_own_route(tmp_path: Path, monkeypatch) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    brief_file = tmp_path / "brief.md"
    brief_file.write_text("cleanup", encoding="utf-8")
    config_file = tmp_path / "runner.json"
    config_file.write_text(json.dumps({
        "schema_version": "spec-runner-config/v1",
        "repository_path": str(repository),
        "target_ref": "HEAD",
        "artifact_root": "artifacts",
        "execution_backend": "deterministic_test",
        "allowed_stages": ["example"],
        "model": {"name": "deterministic-test", "effort": "none"},
        "authorization": {"artifact_roots": ["artifacts"]},
        "workflow": {"mode": "production"},
    }), encoding="utf-8")
    control = tmp_path / "control"
    config = RunnerConfig.from_file(config_file, control)
    store = Store.open(control, create=True)
    try:
        run = replace(_run(tmp_path), run_id=str(uuid.uuid4()), launch_key="public-cleanup",
                      input_digest=hashlib.sha256(brief_file.read_bytes()).hexdigest(),
                      repository_path=str(repository), config_digest=config.digest,
                      backend_kind="deterministic_test", state="cleanup_pending")
        store.create_run(run, "start:public-cleanup-run")
        _prepare_migration(store, run, "public-cleanup-migration")
    finally:
        store.close()

    archive_calls = []
    monkeypatch.setattr(
        "spec_runner.workflow._retry_migration_source_archive",
        lambda **kwargs: archive_calls.append(kwargs) or pytest.fail("ordinary cleanup must not retry source archive"),
    )
    monkeypatch.setattr(
        "spec_runner.workflow._retry_production_cleanup",
        lambda **kwargs: {"state": "cleanup_pending", "cleanup": {"outcome": "pending"}},
    )
    result = workflow.start(
        brief_file=brief_file, config_file=config_file, control_root=control,
        launch_key="public-cleanup",
    )
    assert result["state"] == "cleanup_pending"
    assert archive_calls == []
