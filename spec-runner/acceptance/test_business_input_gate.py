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
