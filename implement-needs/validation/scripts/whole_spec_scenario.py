"""Deterministic normalized-backend scenario for whole-SPEC qualification.

The scenario is intentionally side-effect free.  It exercises controller
recovery at the normalized seam and records the injected fault matrix separately
from live backend evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScenarioClock:
    value: int = 0

    def advance(self, seconds: int) -> int:
        if seconds < 0:
            raise ValueError("seconds must be non-negative")
        self.value += seconds
        return self.value


@dataclass
class FaultPlan:
    delayed_history_reads: int = 0
    duplicate_notifications: bool = False
    disconnect_once: bool = False
    capacity_once: bool = False
    route_drift: bool = False
    archive_readback_failure: bool = False
    host_missing: bool = False


class FaultInjectingBackend:
    """Small fake backend with formal identities and explicit readbacks."""

    def __init__(self, plan: FaultPlan | None = None):
        self.plan = plan or FaultPlan()
        self.events: list[dict[str, Any]] = []
        self.threads: dict[tuple[str, str], dict[str, Any]] = {}
        self.turns: dict[tuple[str, str], dict[str, Any]] = {}
        self._counter = 0

    def create_thread(self, *, run_id: str, task_id: str, attempt_id: str, kind: str = "spec",
                      model: str = "gpt-5.6-sol", effort: str = "high") -> dict[str, Any]:
        self._counter += 1
        formal = f"thread-{self._counter}"
        host = "local"
        item = {"formal_thread_id": formal, "host_id": host, "run_id": run_id,
                "task_id": task_id, "attempt_id": attempt_id, "owner_id": "scenario",
                "cwd": "C:/scenario", "project_id": "scenario-project", "kind": kind,
                "lifecycle": "created", "archived": False,
                "route": {"model": model, "effort": effort}}
        self.threads[(formal, host)] = item
        self.events.append({"type": "thread_created", "identity": dict(item)})
        return dict(item)

    def read_thread(self, formal_thread_id: str, host_id: str) -> dict[str, Any]:
        item = self.threads[(formal_thread_id, host_id)]
        return dict(item)

    def read_applied_route(self, formal_thread_id: str, host_id: str) -> dict[str, Any]:
        item = self.threads[(formal_thread_id, host_id)]
        route = dict(item["route"])
        if self.plan.route_drift:
            route["model"] = "gpt-5.6-luna"
        return {**route, "evidence": ["scenario:applied-route-readback"]}

    def send(self, formal_thread_id: str, host_id: str, *, message: str = "assignment") -> dict[str, Any]:
        item = self.threads[(formal_thread_id, host_id)]
        if self.plan.capacity_once and not any(e.get("type") == "capacity_failure" for e in self.events):
            self.events.append({"type": "capacity_failure", "formal_thread_id": formal_thread_id})
            return {"status": "failed", "error": {"code": "model_capacity"}}
        self._counter += 1
        turn = f"turn-{self._counter}"
        self.turns[(formal_thread_id, turn)] = {"turn_id": turn, "status": "completed", "message": message}
        item["lifecycle"] = "working"
        self.events.append({"type": "turn_started", "formal_thread_id": formal_thread_id, "turn_id": turn})
        if self.plan.disconnect_once:
            self.plan.disconnect_once = False
            self.events.append({"type": "stream_disconnected", "formal_thread_id": formal_thread_id, "turn_id": turn})
            raise ConnectionError("scenario stream disconnected")
        completion = {"turn_id": turn, "status": "completed", "output": "scenario"}
        self.events.append({"type": "turn_completed", **completion})
        return completion

    def archive(self, formal_thread_id: str, host_id: str) -> dict[str, Any]:
        self.threads[(formal_thread_id, host_id)]["archived"] = True
        self.threads[(formal_thread_id, host_id)]["lifecycle"] = "archived"
        self.events.append({"type": "archive_requested", "formal_thread_id": formal_thread_id})
        return {"status": "requested", "evidence": ["scenario:archive"]}

    def archive_readback(self, formal_thread_id: str, host_id: str) -> dict[str, Any]:
        archived = self.threads[(formal_thread_id, host_id)]["archived"]
        if self.plan.archive_readback_failure:
            archived = False
        return {"archived": archived, "evidence": ["scenario:archive-readback"]}

    def inventory(self, run_id: str) -> dict[str, Any]:
        tasks = [dict(item) for item in self.threads.values() if item["run_id"] == run_id]
        if self.plan.host_missing and tasks:
            tasks = tasks[:-1]
        return {"reconciliation_status": "complete", "tasks": tasks}


def run_fault_matrix(seed: int = 129) -> dict[str, Any]:
    """Return reproducible scenario evidence; no production task is accessed."""
    plan = FaultPlan(delayed_history_reads=1, duplicate_notifications=True,
                     disconnect_once=True, capacity_once=True)
    backend = FaultInjectingBackend(plan)
    thread = backend.create_thread(run_id=f"scenario-{seed}", task_id="SPEC-1", attempt_id="01")
    try:
        first = backend.send(thread["formal_thread_id"], thread["host_id"])
    except ConnectionError as exc:
        first = {"status": "stream_disconnected", "error": str(exc)}
    archive = backend.archive(thread["formal_thread_id"], thread["host_id"])
    readback = backend.archive_readback(thread["formal_thread_id"], thread["host_id"])
    return {"scenario_kind": "normalized_fake_backend", "seed": seed,
            "faults": plan.__dict__, "events": backend.events,
            "first_attempt": first, "archive": archive, "archive_readback": readback,
            "live_external_evidence": False}
