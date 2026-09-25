"""Executable hybrid recovery probe at the published Codex adapter boundary.

This probe uses the real CodexAdapter implementation with a tiny SDK-shaped
fake. Faults are injected only through the adapter's explicit acceptance seam;
the report never claims a live provider incident.
"""
from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from spec_runner.codex_adapter import CodexAdapter
from spec_runner.errors import RunnerError
from spec_runner.store import RunRecord, Store, now
from spec_runner import workflow


class _Result:
    id = "turn-hybrid"
    status = types.SimpleNamespace(value="completed")
    error = None
    final_response = "completed"
    items = ["message"]
    started_at = 1
    completed_at = 2


class _Turn:
    id = "turn-hybrid"

    def run(self) -> _Result:
        return _Result()


class _Thread:
    id = "thread-hybrid"

    def turn(self, prompt: Any, **kwargs: Any) -> _Turn:
        return _Turn()


class _Codex:
    def __init__(self, config: Any) -> None:
        self.thread = _Thread()

    def __enter__(self) -> "_Codex":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def thread_start(self, **kwargs: Any) -> _Thread:
        return self.thread

    def thread_resume(self, thread_id: str, **kwargs: Any) -> _Thread:
        return self.thread


_SDK = types.SimpleNamespace(
    CodexConfig=lambda **kwargs: kwargs,
    Codex=object,
    Sandbox=types.SimpleNamespace(workspace_write="workspace-write", read_only="read-only"),
    ApprovalMode=types.SimpleNamespace(deny_all="deny_all"),
)


def _run_case(root: Path, case: dict[str, Any]) -> dict[str, Any]:
    fault = case
    events: list[dict[str, Any]] = []

    def inject(point: str, context: dict[str, object]) -> None:
        events.append({"point": point, "context": dict(context)})
        if point != fault["point"]:
            return
        details = {
            "fault_observation": {
                "message": fault["message"],
                "source": "hybrid_injected_fault",
                "structured": True,
                "request_admission": fault["request_admission"],
                "execution_outcome": fault["execution_outcome"],
                "thread_id": context.get("thread_id"),
                "turn_id": context.get("turn_id"),
                "http_status": fault.get("http_status"),
                "code": fault.get("code"),
                "evidence": ["adapter_boundary", "injected_fault", fault["id"]],
            }
        }
        raise RunnerError(fault["code"], fault["message"], details=details)

    adapter = CodexAdapter(
        codex_factory=lambda config: _Codex(config),
        sdk_module=_SDK,
        fault_injector=inject,
    )
    error: RunnerError | None = None
    try:
        adapter.run(
            prompt="bounded hybrid acceptance probe",
            repository_path=root,
            model="gpt-hybrid-test",
            effort="high",
        )
    except RunnerError as exc:
        error = exc
    if error is None:
        raise AssertionError(f"fault {fault['id']} did not cross the adapter boundary")

    control = root / ("control-" + fault["id"])
    store = Store.open(control, create=True)
    run = RunRecord(
        run_id=f"hybrid-{fault['id']}",
        launch_key=f"hybrid-{fault['id']}",
        input_digest="hybrid",
        config_digest="hybrid",
        repository_path=str(root),
        target_ref="HEAD",
        artifact_root="artifacts",
        backend_kind="codex_sdk",
        state="starting",
        current_step="codex_planning",
        log_path=f"logs/{fault['id']}.jsonl",
        created_at=now(),
        updated_at=now(),
    )
    store.create_run(run, "start:" + run.run_id)
    try:
        decision = workflow._record_recovery_failure(
            run=run,
            store=store,
            operation_id="hybrid:" + fault["id"],
            error=error,
        )
        observation = store.public_status(run.run_id)["recovery"]["episodes"][0]["observations"][0]["observation"]
        return {
            "id": fault["id"],
            "fault_point": fault["point"],
            "adapter_events": events,
            "thread_id": observation.get("thread_id"),
            "turn_id": observation.get("turn_id"),
            "request_admission": observation.get("request_admission"),
            "execution_outcome": observation.get("execution_outcome"),
            "family": observation.get("family"),
            "reason": observation.get("reason"),
            "decision": decision.public(),
            "evidence_kind": "deterministic_fixture",
            "live_provider_incident": False,
        }
    finally:
        store.close()


def run_probe(*, output: Path | None = None) -> dict[str, Any]:
    faults = [
        {
            "id": "encrypted_mismatch",
            "point": "after_turn_completed",
            "code": "injected_encrypted_item_mismatch",
            "message": "encrypted item-id mismatch",
            "request_admission": "accepted",
            "execution_outcome": "unknown",
        },
        {
            "id": "capacity",
            "point": "after_turn_completed",
            "code": "injected_capacity",
            "message": "capacity temporarily unavailable",
            "request_admission": "rejected",
            "execution_outcome": "failed",
            "http_status": 429,
        },
        {
            "id": "stream_disconnect",
            "point": "after_turn_started",
            "code": "injected_stream_disconnect",
            "message": "stream disconnected",
            "request_admission": "accepted",
            "execution_outcome": "unknown",
        },
        {
            "id": "route_not_found",
            "point": "after_turn_completed",
            "code": "injected_route_not_found",
            "message": "route not found",
            "request_admission": "rejected",
            "execution_outcome": "failed",
            "http_status": 404,
        },
        {
            "id": "authorization",
            "point": "after_turn_completed",
            "code": "injected_authorization",
            "message": "permission denied",
            "request_admission": "rejected",
            "execution_outcome": "failed",
            "http_status": 403,
        },
    ]
    with __import__("tempfile").TemporaryDirectory(prefix="spec-runner-hybrid-") as temporary:
        root = Path(temporary)
        cases = [_run_case(root, fault) for fault in faults]
    report: dict[str, Any] = {
        "schema_version": "spec-runner-hybrid-fault-probe/v1",
        "evidence_kind": "deterministic_fixture",
        "adapter_boundary": "CodexAdapter",
        "live_provider_incident": False,
        "cases": cases,
        "passed": all(
            case["thread_id"] == "thread-hybrid"
            and case["turn_id"] == "turn-hybrid"
            and case["evidence_kind"] == "deterministic_fixture"
            for case in cases
        ),
        "unverified": [
            "live Codex process restart",
            "native Windows detached-parent exit",
            "live GitHub merge queue",
            "hybrid real SDK fault injection",
            "real provider incident",
        ],
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_probe(output=args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
