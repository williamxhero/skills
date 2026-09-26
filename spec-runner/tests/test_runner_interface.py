from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from spec_runner.models import RunContext, RunnerRequest, StageResult
from spec_runner.runner import Runner
from spec_runner.stage_progression import StageProgression


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


def test_runner_accepts_a_lifecycle_port_and_projects_stage_result() -> None:
    class Port:
        def start(self, request):
            assert request.launch_key == "port-1"
            return {"created": True, "run": {"run_id": "run-1", "state": "planned"}}

        def drive(self, request):
            return {"run": {"run_id": "run-1", "state": "completed"}}

        def resume(self, request):
            return {"run": {"run_id": "run-1", "state": "running"}}

        def control(self, **kwargs):
            return {"accepted": True, **kwargs}

        def launch(self, **kwargs):
            return {"started": True, "run_id": "run-1"}

        def status(self, **kwargs):
            return {"run": {"run_id": kwargs["run_id"], "state": "running"}}

        def doctor(self, **kwargs):
            return {"valid": True}

    request = RunnerRequest(
        brief_file=Path("brief.md"),
        config_file=Path("runner.json"),
        control_root=Path(".control"),
        launch_key="port-1",
    )
    runner = Runner(port=Port())

    assert runner.start(request)["run"]["state"] == "planned"
    assert runner.drive(request)["run"]["state"] == "completed"
    assert runner.resume(request)["run"]["state"] == "running"
    assert runner.control(control_root=Path(".control"), run_id="run-1", requested_state="pause_requested")["accepted"]
    assert runner.launch(request=request)["started"]
    assert runner.status(control_root=Path(".control"), run_id="run-1")["run"]["run_id"] == "run-1"
    assert runner.doctor(config_file=None, control_root=Path(".control"))["valid"]


def test_runner_launch_preserves_detached_handshake_projection(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def launch_legacy(**kwargs):
        observed.update(kwargs)
        return {
            "started": True,
            "pid": 123,
            "run_id": "run-1",
            "run": {"run_id": "run-1", "state": "running"},
            "log_path": "launcher.log",
        }

    monkeypatch.setattr("spec_runner.workflow._launch_legacy", launch_legacy)
    result = Runner().launch(
        request=RunnerRequest(
            brief_file=Path("brief.md"),
            config_file=Path("runner.json"),
            control_root=Path(".control"),
            launch_key="launch-1",
        ),
        handshake_timeout_seconds=3.0,
    )

    assert result["started"] is True
    assert result["run"]["state"] == "running"
    assert observed["handshake_timeout_seconds"] == 3.0


def test_runner_start_executes_deterministic_workflow_through_public_seam(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    brief = tmp_path / "brief.md"
    brief.write_text("# deterministic\n", encoding="utf-8")
    config = tmp_path / "runner.json"
    config.write_text(json.dumps({
        "schema_version": "spec-runner-config/v1",
        "repository_path": str(repository),
        "target_ref": "HEAD",
        "artifact_root": "artifacts",
        "execution_backend": "deterministic_test",
        "allowed_stages": ["example"],
        "model": {"name": "deterministic-test", "effort": "none"},
        "authorization": {"artifact_roots": ["artifacts"]},
    }), encoding="utf-8")
    request = RunnerRequest(
        brief_file=brief,
        config_file=config,
        control_root=tmp_path / "control",
        launch_key="runner-public-1",
    )

    result = Runner().start(request)

    assert result["created"] is True
    assert result["run"]["state"] == "completed"
    assert result["step"]["state"] == "archived"
    assert len(result["verification"]) == 2


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


def test_stage_progression_selects_production_recovery_routes() -> None:
    config = SimpleNamespace(
        delivery_plan=None,
        execution_backend="codex_sdk",
        workflow_mode="production",
    )
    run = SimpleNamespace(state="waiting_ci")

    route = StageProgression.select(run, config)

    assert route.kind == "waiting_github"
    assert route.production is True


def test_stage_progression_routes_controlled_input_to_recovery() -> None:
    config = SimpleNamespace(
        delivery_plan=None,
        execution_backend="codex_sdk",
        workflow_mode="production",
    )
    run = SimpleNamespace(state="needs_input")

    route = StageProgression.select(run, config, has_control=True)

    assert route.kind == "recover"


def test_stage_progression_keeps_cleanup_status_for_nonproduction_runs() -> None:
    config = SimpleNamespace(
        delivery_plan=None,
        execution_backend="deterministic_test",
        workflow_mode="single",
    )
    run = SimpleNamespace(state="cleanup_pending")

    assert StageProgression.select(run, config).kind == "cleanup_status"


def test_stage_progression_selects_initial_stage_without_io() -> None:
    config = SimpleNamespace(
        delivery_plan=None,
        execution_backend="codex_sdk",
        workflow_mode="production",
    )

    assert StageProgression.initial_stage(config) == "codex_planning"


@pytest.mark.parametrize(
    ("state", "mode", "expected"),
    [
        ("completed", "production", "terminal"),
        ("blocked_writer_busy", "single", "terminal"),
        ("ready_for_next", "single", "ready_for_next"),
        ("planned", "production", "planned"),
        ("tickets_ready", "production", "production_queue"),
        ("tickets_ready", "single", "recover"),
        ("waiting_merge_queue", "production", "waiting_github"),
        ("spec_completed", "production", "production_queue"),
        ("cleanup_pending", "production", "cleanup_production"),
        ("failed", "single", "recover"),
    ],
)
def test_stage_progression_maps_persisted_state_to_one_route(state, mode, expected) -> None:
    config = SimpleNamespace(
        delivery_plan=None,
        execution_backend="codex_sdk" if mode == "production" else "deterministic_test",
        workflow_mode=mode,
    )
    run = SimpleNamespace(state=state)

    assert StageProgression.select(run, config).kind == expected
