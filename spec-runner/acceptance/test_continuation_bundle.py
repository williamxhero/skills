from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from spec_runner.continuation import build_bundle, read_bundle, write_bundle_atomic
from spec_runner.errors import RunnerError
from spec_runner.store import RunRecord, Store, now
from spec_runner.workflow import _reconcile_continuation_bundles


def bundle_input():
    return {
        "run_id": "run-1", "spec_key": "S1", "stage": "implement", "generation": 3,
        "input_revision": "input-digest", "requirements": [{"text": "finish"}],
        "confirmed_decisions": [{"id": "d1", "value": "keep workspace"}],
        "tickets": [{"key": "S1.1", "state": "open"}], "dependencies": [],
        "workspace": {"path": "worktree", "branch": "runner/S1", "head": "abc"},
        "verified_items": [{"id": "tests", "receipt": "r1"}],
        "remaining_items": [{"id": "review"}], "tests": [{"command": ["pytest"], "passed": True}],
        "review": [], "unconfirmed_operations": [{"id": "github:pr", "state": "unknown"}],
        "authorization": {"repository": "owner/repo", "write_scope": ["fixture"]},
        "last_verified_progress": "tests-pass", "source_refs": [{"kind": "git", "ref": "abc"}],
    }


def test_bundle_is_atomic_and_round_trips(tmp_path):
    path = tmp_path / "control" / "continuation.json"
    written = write_bundle_atomic(path, build_bundle(bundle_input()))
    assert path.is_file()
    assert read_bundle(path).public() == written
    assert "encrypted_content" not in json.dumps(written)


def test_bundle_rejects_hidden_or_encrypted_history():
    document = bundle_input()
    document["requirements"] = [{"encrypted_content": "opaque"}]
    with pytest.raises(RunnerError) as error:
        build_bundle(document)
    assert error.value.code == "continuation_forbidden_material"


def test_bundle_digest_and_identity_are_verified():
    document = build_bundle(bundle_input()).public()
    document["remaining_items"] = [{"id": "changed"}]
    with pytest.raises(RunnerError) as error:
        build_bundle(document)
    assert error.value.code == "continuation_digest_mismatch"


def _run_record(root):
    timestamp = now()
    return RunRecord(
        run_id="run-1", launch_key="launch-1", input_digest="input-digest",
        config_digest="config", repository_path=str(root), target_ref="HEAD",
        artifact_root="artifacts", backend_kind="codex_sdk", state="starting",
        current_step="implement", log_path="logs/run-1.jsonl",
        created_at=timestamp, updated_at=timestamp,
    )


def test_bundle_receipt_recovers_when_process_exits_between_file_and_store(tmp_path):
    control = tmp_path / "control"
    store = Store.open(control, create=True)
    run = _run_record(tmp_path)
    store.create_run(run, "start:run-1")
    path = control / "artifacts" / run.run_id / "continuation-S1.json"
    document = write_bundle_atomic(path, build_bundle(bundle_input()))
    store.close()  # Simulate the process ending before receipt registration.

    reopened = Store.open(control, create=False)
    try:
        receipt = reopened.record_continuation_bundle(run_id=run.run_id, bundle_path=path, bundle=document)
        assert receipt["bundle_digest"] == document["bundle_digest"]
        status = reopened.public_status(run.run_id)
        assert len(status["continuation"]) == 1
        assert status["continuation"][0]["workspace_identity"] == document["workspace"]
        assert any(event["event_type"] == "continuation_bundle_registered" for event in status["events"])
    finally:
        reopened.close()


def test_continuation_receipt_digest_conflict_fails_closed(tmp_path):
    control = tmp_path / "control"
    store = Store.open(control, create=True)
    run = _run_record(tmp_path)
    store.create_run(run, "start:run-1")
    path = control / "artifacts" / run.run_id / "continuation-S1.json"
    first = build_bundle(bundle_input()).public()
    write_bundle_atomic(path, build_bundle(bundle_input()))
    store.record_continuation_bundle(run_id=run.run_id, bundle_path=path, bundle=first)
    changed = dict(bundle_input())
    changed["remaining_items"] = [{"id": "different"}]
    second = build_bundle(changed).public()
    try:
        with pytest.raises(RunnerError, match="identity or digest changed"):
            store.record_continuation_bundle(run_id=run.run_id, bundle_path=path, bundle=second)
    finally:
        store.close()


def test_runner_reconciliation_adopts_only_the_existing_run(tmp_path):
    control = tmp_path / "control"
    store = Store.open(control, create=True)
    run = _run_record(tmp_path)
    store.create_run(run, "start:run-1")
    path = control / "artifacts" / run.run_id / "continuation-S1.json"
    document = write_bundle_atomic(path, build_bundle(bundle_input()))
    store.close()

    reopened = Store.open(control, create=False)
    try:
        config = SimpleNamespace(artifact_root=Path("artifacts"))
        receipts = _reconcile_continuation_bundles(
            control_root=control, config=config, run=run, store=reopened,
        )
        replay = _reconcile_continuation_bundles(
            control_root=control, config=config, run=run, store=reopened,
        )
        assert receipts[0]["bundle_digest"] == document["bundle_digest"]
        assert replay[0]["receipt_id"] == receipts[0]["receipt_id"]
        assert len(reopened.public_status(run.run_id)["continuation"]) == 1
    finally:
        reopened.close()


def test_bundle_has_a_total_context_budget():
    document = bundle_input()
    document["requirements"] = [{"text": "x" * 15_000} for _ in range(20)]
    with pytest.raises(RunnerError) as error:
        build_bundle(document)
    assert error.value.code == "continuation_bundle_too_large"
