from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from spec_runner.models import RunContext, RunnerRequest, StageResult
from spec_runner.runner import Runner


def test_stage_result_preserves_legacy_status_projection() -> None:
    payload = {
        "created": True,
        "run": {"run_id": "run-1", "state": "completed"},
        "steps": [],
    }

    result = StageResult.from_public(payload)

    assert result.run_id == "run-1"
    assert result.state == "completed"
    assert result.public() == payload


def test_runner_start_is_a_typed_compatibility_seam(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def start_legacy(**kwargs):
        observed.update(kwargs)
        return {"created": True, "run": {"run_id": "run-1", "state": "completed"}}

    monkeypatch.setattr("spec_runner.workflow._start_legacy", start_legacy)
    request = RunnerRequest(
        brief_file=Path("brief.md"),
        config_file=Path("runner.json"),
        control_root=Path(".control"),
        launch_key="launch-1",
    )

    result = Runner().start(request)

    assert result == {"created": True, "run": {"run_id": "run-1", "state": "completed"}}
    assert observed["launch_key"] == "launch-1"
    assert observed["run_id"] is None


def test_run_context_keeps_durable_status_at_one_edge() -> None:
    store = SimpleNamespace(public_status=lambda run_id: {"run": {"run_id": run_id}})
    run = SimpleNamespace(run_id="run-1")
    context = RunContext(
        control_root=Path(".control"),
        config=SimpleNamespace(),
        brief="brief",
        brief_digest="digest",
        run=run,
        store=store,
    )

    assert context.public_status() == {"run": {"run_id": "run-1"}}
