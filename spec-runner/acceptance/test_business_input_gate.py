from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from spec_runner.errors import RunnerError
from spec_runner.store import RunRecord, Store, now


def test_answer_is_durable_wakeup_and_conflicting_answer_is_rejected(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "control", create=True)
    try:
        timestamp = now()
        run = RunRecord(
            run_id="input-gate-run",
            launch_key="input-gate-launch",
            input_digest="brief",
            config_digest="config",
            repository_path=str(tmp_path),
            target_ref="HEAD",
            artifact_root="artifacts",
            backend_kind="codex_sdk",
            state="needs_input",
            current_step="codex_example",
            log_path="logs/input.jsonl",
            created_at=timestamp,
            updated_at=timestamp,
        )
        store.create_run(run, "start:input-gate-run")
        answer = store.submit_answer(run_id=run.run_id, question_id="Q1", value="array-of-objects")
        wake = store.request_control(run.run_id, "resume_requested")
        assert answer["value"] == "array-of-objects"
        assert wake["requested_state"] == "resume_requested"
        assert store.answers_for_run(run.run_id)[0]["question_id"] == "Q1"
        with pytest.raises(RunnerError) as conflict:
            store.submit_answer(run_id=run.run_id, question_id="Q1", value="array-of-arrays")
        assert conflict.value.code == "answer_conflict"
    finally:
        store.close()


def test_answer_contract_rejects_unknown_choice_and_stale_requirement_revision(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "control", create=True)
    try:
        timestamp = now()
        run = RunRecord(
            run_id="input-contract-run",
            launch_key="input-contract-launch",
            input_digest="brief-v2",
            config_digest="config",
            repository_path=str(tmp_path),
            target_ref="HEAD",
            artifact_root="artifacts",
            backend_kind="codex_sdk",
            state="needs_input",
            current_step="codex_planning",
            log_path="logs/input-contract.jsonl",
            created_at=timestamp,
            updated_at=timestamp,
        )
        store.create_run(run, "start:input-contract-run")
        question = {"id": "format", "question": "Which format?", "options": ["json", "csv"]}
        with pytest.raises(RunnerError) as invalid_choice:
            store.submit_answer_and_wake(
                run_id=run.run_id,
                question_id="format",
                value="xml",
                question=question,
                expected_input_digest="brief-v2",
            )
        assert invalid_choice.value.code == "answer_value_invalid"
        with pytest.raises(RunnerError) as stale:
            store.submit_answer_and_wake(
                run_id=run.run_id,
                question_id="format",
                value="json",
                question=question,
                expected_input_digest="brief-v1",
            )
        assert stale.value.code == "answer_input_stale"
        assert store.answers_for_run(run.run_id) == []
    finally:
        store.close()
