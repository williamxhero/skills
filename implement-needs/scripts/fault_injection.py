"""Deterministic normalized backend for controller recovery qualification.

The adapter models observable backend boundaries rather than private transport
calls.  Every injected fault has a stable code, a replayable counter, formal
thread/turn identities, and an authoritative readback path.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


FAULTS = (
    "parent_turn_end",
    "model_capacity",
    "stream_disconnect",
    "empty_history",
    "permanent_empty_history",
    "delayed_notification",
    "lost_response",
    "route_drift",
    "identity_drift",
    "archive_readback_failure",
    "host_inventory_missing",
)


class InjectedFault(ConnectionError):
    def __init__(self, code: str, *, formal_thread_id: str | None = None, turn_id: str | None = None):
        self.code = code
        self.formal_thread_id = formal_thread_id
        self.turn_id = turn_id
        super().__init__(code)


@dataclass
class FaultPlan:
    parent_turn_end: int = 0
    model_capacity: int = 0
    stream_disconnect: int = 0
    empty_history: int = 0
    permanent_empty_history: bool = False
    delayed_notification: int = 0
    lost_response: int = 0
    route_drift: bool = False
    identity_drift: bool = False
    archive_readback_failure: bool = False
    host_inventory_missing: bool = False

    def __post_init__(self) -> None:
        for name in FAULTS:
            value = getattr(self, name)
            if isinstance(value, bool):
                continue
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"fault count must be a non-negative integer: {name}")

    def consume(self, name: str) -> bool:
        if name not in FAULTS:
            raise ValueError(f"unknown fault: {name}")
        value = getattr(self, name)
        if isinstance(value, bool):
            return value
        if value < 1:
            return False
        setattr(self, name, value - 1)
        return True


@dataclass
class FaultInjectionBackend:
    plan: FaultPlan = field(default_factory=FaultPlan)
    counter: int = 0
    threads: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    turns: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    notifications: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    history_reads: dict[tuple[str, str], int] = field(default_factory=dict)

    def _next(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}-{self.counter}"

    def create_thread(self, *, run_id: str, task_id: str, attempt_id: str,
                      model: str = "gpt-5.6-sol", effort: str = "high") -> dict[str, Any]:
        formal = self._next("thread")
        item = {
            "formal_thread_id": formal, "host_id": "local", "run_id": run_id,
            "task_id": task_id, "attempt_id": attempt_id, "owner_id": "fault-backend",
            "cwd": "C:/scenario", "project_id": "scenario-project", "lifecycle": "idle",
            "archived": False, "route": {"model": model, "effort": effort},
        }
        self.threads[(formal, "local")] = item
        self.events.append({"type": "thread_created", "identity": copy.deepcopy(item)})
        return copy.deepcopy(item)

    def read_thread(self, formal_thread_id: str, host_id: str = "local") -> dict[str, Any]:
        item = copy.deepcopy(self.threads[(formal_thread_id, host_id)])
        if self.plan.identity_drift:
            item["formal_thread_id"] = f"{formal_thread_id}-drift"
        return item

    def read_applied_route(self, formal_thread_id: str, host_id: str = "local") -> dict[str, Any]:
        route = dict(self.threads[(formal_thread_id, host_id)]["route"])
        if self.plan.route_drift:
            route["model"] = "gpt-5.6-luna"
        return {**route, "evidence": [f"fault://route/{formal_thread_id}"]}

    def send_turn(self, formal_thread_id: str, host_id: str = "local", *, message: str = "assignment") -> dict[str, Any]:
        item = self.threads[(formal_thread_id, host_id)]
        if self.plan.consume("model_capacity"):
            event = {"type": "turn_failed", "code": "model_capacity", "formal_thread_id": formal_thread_id}
            self.events.append(event)
            raise InjectedFault("model_capacity", formal_thread_id=formal_thread_id)
        turn_id = self._next("turn")
        completed = {"turn_id": turn_id, "status": "completed", "output": message}
        self.turns[(formal_thread_id, turn_id)] = completed
        item["lifecycle"] = "working"
        self.events.append({"type": "turn_started", "formal_thread_id": formal_thread_id, "turn_id": turn_id})
        if self.plan.consume("parent_turn_end"):
            self.events.append({"type": "parent_turn_ended", "formal_thread_id": formal_thread_id, "turn_id": turn_id})
            raise InjectedFault("parent_turn_end", formal_thread_id=formal_thread_id, turn_id=turn_id)
        if self.plan.consume("stream_disconnect"):
            self.events.append({"type": "stream_disconnected", "formal_thread_id": formal_thread_id, "turn_id": turn_id})
            raise InjectedFault("stream_disconnect", formal_thread_id=formal_thread_id, turn_id=turn_id)
        self.events.append({"type": "turn_completed", **completed})
        notification = {"method": "turn/completed", "threadId": formal_thread_id, "turnId": turn_id, "params": completed}
        if self.plan.consume("delayed_notification"):
            notification["delayed"] = True
        self.notifications.append(notification)
        if self.plan.consume("lost_response"):
            self.events.append({"type": "response_lost", "formal_thread_id": formal_thread_id, "turn_id": turn_id})
            raise InjectedFault("lost_response", formal_thread_id=formal_thread_id, turn_id=turn_id)
        return dict(completed)

    def read_history(self, formal_thread_id: str, turn_id: str) -> dict[str, Any]:
        key = (formal_thread_id, turn_id)
        count = self.history_reads.get(key, 0)
        self.history_reads[key] = count + 1
        if self.plan.permanent_empty_history or self.plan.consume("empty_history"):
            return {"thread_id": formal_thread_id, "turn_id": turn_id, "rollout": None, "temporary": True}
        return {"thread_id": formal_thread_id, "turn_id": turn_id,
                "rollout": copy.deepcopy(self.turns[key]), "temporary": False}

    def drain_notifications(self) -> list[dict[str, Any]]:
        result = copy.deepcopy(self.notifications)
        self.notifications.clear()
        return result

    def archive(self, formal_thread_id: str, host_id: str = "local") -> dict[str, Any]:
        item = self.threads[(formal_thread_id, host_id)]
        item["archived"] = True
        item["lifecycle"] = "archived"
        self.events.append({"type": "archive_requested", "formal_thread_id": formal_thread_id})
        return {"status": "requested", "evidence": [f"fault://archive/{formal_thread_id}"]}

    def archive_readback(self, formal_thread_id: str, host_id: str = "local") -> dict[str, Any]:
        archived = self.threads[(formal_thread_id, host_id)]["archived"]
        if self.plan.archive_readback_failure:
            archived = False
        return {"archived": archived, "evidence": [f"fault://archive-readback/{formal_thread_id}"]}

    def inventory(self, run_id: str) -> dict[str, Any]:
        tasks = [copy.deepcopy(item) for item in self.threads.values() if item["run_id"] == run_id]
        if self.plan.host_inventory_missing and tasks:
            tasks.pop()
        return {"reconciliation_status": "complete", "tasks": tasks}

    def restart(self) -> "FaultInjectionBackend":
        """Return a fresh process-equivalent backend from a JSON-like snapshot."""
        clone = FaultInjectionBackend(plan=copy.deepcopy(self.plan), counter=self.counter,
                                      threads=copy.deepcopy(self.threads), turns=copy.deepcopy(self.turns),
                                      notifications=copy.deepcopy(self.notifications), events=copy.deepcopy(self.events),
                                      history_reads=copy.deepcopy(self.history_reads))
        return clone

    def snapshot(self) -> dict[str, Any]:
        return {"counter": self.counter, "threads": copy.deepcopy(self.threads),
                "turns": copy.deepcopy(self.turns), "notifications": copy.deepcopy(self.notifications),
                "events": copy.deepcopy(self.events), "history_reads": copy.deepcopy(self.history_reads),
                "plan": self.plan.__dict__.copy()}
