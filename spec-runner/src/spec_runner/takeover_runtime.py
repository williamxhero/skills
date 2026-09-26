from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .errors import RunnerError
from .plans import digest
from .store import Store
from .takeover import (
    completion_action,
    frontier_execution_steps,
    frontier_step_transition,
    inspect_takeover,
    plan_frontier,
    perform_cleanup,
    record_takeover_transition,
    refresh_takeover_evidence,
    verify_frontier_step,
    write_takeover_record,
)


RunnerStarter = Callable[..., dict[str, object]]


@dataclass(frozen=True)
class TakeoverExecutionRequest:
    """Typed input for one takeover application."""

    inventory: dict[str, Any]
    control_root: Path
    takeover_key: str
    git_timeout_seconds: float
    brief: Path | None = None
    config: Path | None = None
    launch_key: str | None = None
    thread_id: str | None = None
    repository: Path | None = None
    handover_policy: str = "require_stop_confirmation"


def _latest_transition(transitions: object, *, event_key: str, state: str) -> dict[str, object] | None:
    if not isinstance(transitions, list):
        return None
    return next(
        (
            item for item in reversed(transitions)
            if isinstance(item, dict)
            and item.get("event_key") == event_key
            and item.get("state") == state
        ),
        None,
    )


class TakeoverRuntime:
    """Observe, persist, and execute one takeover frontier.

    Source-thread reading remains an adapter in the CLI. This module owns the
    ordering rules after inventory exists, so CLI argument handling cannot turn
    backend availability into frontier capability.
    """

    def __init__(
        self,
        request: TakeoverExecutionRequest,
        *,
        start_runner: RunnerStarter,
        interrupt_thread: Callable[..., dict[str, object]],
    ) -> None:
        self.request = request
        self.start_runner = start_runner
        self.interrupt_thread = interrupt_thread

    def apply(self) -> dict[str, object]:
        request = self.request
        report = inspect_takeover(request.inventory, git_timeout_seconds=request.git_timeout_seconds)
        frontier = plan_frontier(report)
        existing = self._read_existing_record()
        if existing is not None:
            report, frontier = self._reuse_stable_observation(
                report=report,
                frontier=frontier,
                existing=existing,
            )
        record = write_takeover_record(
            control_root=request.control_root,
            takeover_key=request.takeover_key,
            report=report,
            frontier=frontier,
        )
        action = completion_action(report, git_timeout_seconds=request.git_timeout_seconds)
        result: dict[str, object] = {**record, "frontier": frontier, "action": action}
        stored_record = record.get("record") if isinstance(record.get("record"), dict) else None
        last_transition = stored_record.get("last_transition") if isinstance(stored_record, dict) else None
        if (
            not record.get("created")
            and isinstance(last_transition, dict)
            and str(last_transition.get("event_key", "")).startswith(f"{request.takeover_key}:handover:reobserved:")
            and isinstance(stored_record, dict)
            and isinstance(stored_record.get("report"), dict)
            and isinstance(stored_record.get("frontier"), dict)
        ):
            report = stored_record["report"]
            frontier = stored_record["frontier"]
            action = completion_action(report, git_timeout_seconds=request.git_timeout_seconds)
            result["report"] = report
            result["frontier"] = frontier
            result["action"] = action
        if record.get("created"):
            observed = record_takeover_transition(
                control_root=request.control_root,
                takeover_key=request.takeover_key,
                state="observed",
                event_key=f"{request.takeover_key}:observed:{report['digest']}",
                payload={"report_digest": report["digest"], "frontier_digest": frontier["digest"]},
            )
            result["record"] = observed["record"]
            result["transitions"] = observed["transitions"]
        prior_state = stored_record.get("state") if isinstance(stored_record, dict) else None
        if (
            action["state"] in {"blocked", "waiting_handover"}
            and request.thread_id
            and request.handover_policy == "interrupt_then_takeover"
        ):
            report, frontier, action = self._interrupt_and_reobserve(
                result=result,
                inventory=request.inventory,
                report=report,
                frontier=frontier,
                prior_state=prior_state,
            )
            result["action"] = action
        return self.execute(
            result=result,
            report=report,
            frontier=frontier,
            action=action,
            record=record,
        )

    def _read_existing_record(self) -> dict[str, object] | None:
        database = self.request.control_root.expanduser().resolve() / "spec-runner.sqlite3"
        if not database.is_file():
            return None
        store = Store.open(self.request.control_root.expanduser().resolve(), create=False)
        try:
            record = store.takeover_record(self.request.takeover_key)
            return record if isinstance(record, dict) else None
        finally:
            store.close()

    def _reuse_stable_observation(
        self,
        *,
        report: dict[str, object],
        frontier: dict[str, object],
        existing: dict[str, object],
    ) -> tuple[dict[str, object], dict[str, object]]:
        stored_report = existing.get("report")
        stored_frontier = existing.get("frontier")
        stored_snapshot = stored_report.get("repository_snapshot") if isinstance(stored_report, dict) else None
        stored_handover = stored_report.get("handover") if isinstance(stored_report, dict) else None
        stored_facts = stored_report.get("historical_facts") if isinstance(stored_report, dict) else None
        fresh_facts = self.request.inventory.get("facts")
        stored_material = self._source_material_digest(
            stored_facts.get("source_observation") if isinstance(stored_facts, dict) else None
        )
        fresh_material = self._source_material_digest(
            fresh_facts.get("source_observation") if isinstance(fresh_facts, dict) else None
        )
        fresh_snapshot = report.get("repository_snapshot")
        stored_thread_ids = sorted(
            str(item.get("thread_id")) for item in stored_report.get("adopted_threads", [])
            if isinstance(item, dict) and isinstance(item.get("thread_id"), str)
        ) if isinstance(stored_report, dict) and isinstance(stored_report.get("adopted_threads"), list) else []
        fresh_thread_ids = sorted(
            str(item.get("id")) for item in self.request.inventory.get("source_threads", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ) if isinstance(self.request.inventory.get("source_threads"), list) else []
        if (
            isinstance(stored_report, dict)
            and isinstance(stored_frontier, dict)
            and isinstance(stored_snapshot, dict)
            and isinstance(fresh_snapshot, dict)
            and isinstance(stored_handover, dict)
            and stored_handover.get("state") == "released"
            and stored_snapshot.get("snapshot_digest") == fresh_snapshot.get("snapshot_digest")
            and stored_thread_ids == fresh_thread_ids
            and stored_material == fresh_material
        ):
            return stored_report, stored_frontier
        return report, frontier

    @staticmethod
    def _source_material_digest(observation: object) -> str | None:
        if not isinstance(observation, dict):
            return None
        return digest({
            "thread": observation.get("thread"),
            "business_items": observation.get("business_items"),
            "completeness": observation.get("completeness"),
            "turn_count": observation.get("turn_count"),
        })

    def _interrupt_and_reobserve(
        self,
        *,
        result: dict[str, object],
        inventory: dict[str, Any],
        report: dict[str, object],
        frontier: dict[str, object],
        prior_state: object,
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        request = self.request
        intent = record_takeover_transition(
            control_root=request.control_root,
            takeover_key=request.takeover_key,
            state="handover_interrupt_intent",
            event_key=f"{request.takeover_key}:handover:interrupt:intent",
            payload={"thread_id": request.thread_id},
        )
        result["record"] = intent["record"]
        result["transitions"] = intent["transitions"]
        if prior_state == "handover_interrupt_intent":
            handover: dict[str, object] = {
                "schema_version": "spec-runner-sdk-thread-interrupt/v1",
                "thread_id": request.thread_id,
                "accepted": None,
                "reason": "interrupt_outcome_unknown",
                "evidence_limits": {"dispatcher_quiesced": False, "ownership_transferred": False},
            }
        else:
            if request.repository is None:
                raise RunnerError("takeover_repository_required", "--repository is required with --thread-id")
            handover = self.interrupt_thread(
                thread_id=request.thread_id,
                repository_path=request.repository.resolve(),
            )
        result["handover"] = handover
        finished = record_takeover_transition(
            control_root=request.control_root,
            takeover_key=request.takeover_key,
            state="handover_interrupt_observed",
            event_key=f"{request.takeover_key}:handover:interrupt:{digest(handover)}",
            payload=handover,
        )
        result["record"] = finished["record"]
        result["transitions"] = finished["transitions"]
        if handover.get("accepted") is not True:
            return report, frontier, completion_action(report, git_timeout_seconds=request.git_timeout_seconds)
        refreshed_inventory = json.loads(json.dumps(inventory, ensure_ascii=False))
        refreshed_threads = refreshed_inventory.get("source_threads", [])
        if not isinstance(refreshed_threads, list):
            raise RunnerError("takeover_inventory_invalid", "source_threads must remain a list after handover")
        refreshed_source = next(
            (item for item in refreshed_threads if isinstance(item, dict) and item.get("id") == request.thread_id),
            None,
        )
        if not isinstance(refreshed_source, dict):
            raise RunnerError("takeover_source_missing", "handover readback does not identify the source thread")
        refreshed_source["handover_evidence"] = handover
        refreshed_source["active"] = False
        observation_after = handover.get("observation_after")
        if isinstance(observation_after, dict):
            refreshed_source["observation"] = observation_after
            refreshed_facts = refreshed_inventory.get("facts")
            if isinstance(refreshed_facts, dict):
                refreshed_facts["source_observation"] = observation_after
        report = inspect_takeover(refreshed_inventory, git_timeout_seconds=request.git_timeout_seconds)
        frontier = plan_frontier(report)
        refreshed = refresh_takeover_evidence(
            control_root=request.control_root,
            takeover_key=request.takeover_key,
            report=report,
            frontier=frontier,
            event_key=f"{request.takeover_key}:handover:reobserved:{report['digest']}",
            payload={
                "thread_id": request.thread_id,
                "handover_digest": digest(handover),
                "report_digest": report["digest"],
                "frontier_digest": frontier["digest"],
            },
        )
        result["report"] = report
        result["frontier"] = frontier
        result["record"] = refreshed["record"]
        result["transitions"] = refreshed["transitions"]
        return report, frontier, completion_action(report, git_timeout_seconds=request.git_timeout_seconds)

    def execute(
        self,
        *,
        result: dict[str, object],
        report: dict[str, object],
        frontier: dict[str, object],
        action: dict[str, object],
        record: dict[str, object],
    ) -> dict[str, object]:
        request = self.request
        execution_steps = frontier_execution_steps(
            frontier,
            result.get("transitions") if isinstance(result.get("transitions"), list) else None,
        )
        result["execution"] = self._execution_projection(frontier, execution_steps)
        unsupported_steps = [step for step in execution_steps if not self._supports_frontier_step(step)]
        if unsupported_steps:
            result["execution"] = {
                "state": "blocked",
                "steps": [step.public() for step in execution_steps],
                "blocker": "frontier_step_handler_missing",
            }

        if execution_steps:
            self._verify_adoption_if_possible(
                result=result,
                report=report,
                frontier=frontier,
                execution_steps=execution_steps,
                unsupported_steps=unsupported_steps,
            )
            execution_steps = frontier_execution_steps(
                frontier,
                result.get("transitions") if isinstance(result.get("transitions"), list) else None,
            )

        self._cleanup_if_required(result=result, report=report, action=action, record=record)
        self._continue_remaining_acceptance(
            result=result,
            report=report,
            frontier=frontier,
            execution_steps=execution_steps,
            action=action,
        )
        return result

    @staticmethod
    def _execution_projection(frontier: dict[str, object], steps: list[Any]) -> dict[str, object]:
        return {
            "state": (
                frontier["state"]
                if frontier["state"] != "planned"
                else ("pending" if steps else "verified")
            ),
            "steps": [step.public() for step in steps],
        }

    @staticmethod
    def _supports_frontier_step(step: Any) -> bool:
        return (
            (step.kind == "adopt" and step.target == "working_tree")
            or (step.kind == "resume" and step.target == "remaining_acceptance")
        )

    def _verify_adoption_if_possible(
        self,
        *,
        result: dict[str, object],
        report: dict[str, object],
        frontier: dict[str, object],
        execution_steps: list[Any],
        unsupported_steps: list[Any],
    ) -> None:
        adoption_step = next(
            (step for step in execution_steps if step.kind == "adopt" and step.target == "working_tree"),
            None,
        )
        adoption_evidence = verify_frontier_step(report, adoption_step) if adoption_step else None
        if adoption_step is None or adoption_evidence is None:
            return
        request = self.request
        verify_event_key, verify_payload = frontier_step_transition(
            adoption_step,
            state="frontier_step_verified",
            payload={"evidence": adoption_evidence},
        )
        verified = record_takeover_transition(
            control_root=request.control_root,
            takeover_key=request.takeover_key,
            state="frontier_step_verified",
            event_key=f"{request.takeover_key}:{verify_event_key}",
            payload=verify_payload,
        )
        result["record"] = verified["record"]
        result["transitions"] = verified["transitions"]
        remaining = frontier_execution_steps(frontier, result["transitions"])
        result["execution"] = {
            "state": "blocked" if unsupported_steps else ("verified" if not remaining else "pending"),
            "steps": [step.public() for step in remaining],
            **({"blocker": "frontier_step_handler_missing"} if unsupported_steps else {}),
        }

    def _cleanup_if_required(
        self,
        *,
        result: dict[str, object],
        report: dict[str, object],
        action: dict[str, object],
        record: dict[str, object],
    ) -> None:
        if action["state"] != "cleanup_pending":
            return
        request = self.request
        prior_record = record.get("record") if isinstance(record.get("record"), dict) else None
        prior_state = prior_record.get("state") if isinstance(prior_record, dict) else None
        last_transition = prior_record.get("last_transition") if isinstance(prior_record, dict) else None
        prior_cleanup = (
            last_transition.get("payload")
            if isinstance(last_transition, dict)
            and last_transition.get("state") == "cleanup_pending"
            and isinstance(last_transition.get("payload"), dict)
            else None
        )
        if prior_state == "cleaned":
            result["cleanup"] = {"outcome": "cleaned", "attempted": 0, "results": [], "replayed": True}
            result["action"] = {**action, "state": "cleaned"}
            return
        intent = record_takeover_transition(
            control_root=request.control_root,
            takeover_key=request.takeover_key,
            state="cleanup_intent",
            event_key=f"{request.takeover_key}:cleanup:intent",
            payload={"targets": report.get("historical_facts", {}).get("cleanup_targets", [])},
        )
        result["record"] = intent["record"]
        result["transitions"] = intent["transitions"]
        result["cleanup"] = perform_cleanup(
            report,
            prior_cleanup=prior_cleanup,
            git_timeout_seconds=request.git_timeout_seconds,
        )
        cleanup_digest = digest(result["cleanup"])
        cleanup_state = "cleaned" if result["cleanup"]["outcome"] == "cleaned" else "cleanup_pending"
        finished = record_takeover_transition(
            control_root=request.control_root,
            takeover_key=request.takeover_key,
            state=cleanup_state,
            event_key=f"{request.takeover_key}:cleanup:result:{cleanup_digest}",
            payload=result["cleanup"],
        )
        result["record"] = finished["record"]
        result["transitions"] = finished["transitions"]
        if cleanup_state == "cleaned":
            result["action"] = {**action, "state": "cleaned"}

    def _continue_remaining_acceptance(
        self,
        *,
        result: dict[str, object],
        report: dict[str, object],
        frontier: dict[str, object],
        execution_steps: list[Any],
        action: dict[str, object],
    ) -> None:
        request = self.request
        transitions = result.get("transitions")
        execution_result = next(
            (item for item in reversed(transitions)
             if isinstance(item, dict) and item.get("state") == "execution_started"),
            None,
        ) if isinstance(transitions, list) else None
        current_record = result.get("record") if isinstance(result.get("record"), dict) else None
        existing_transition = current_record.get("last_transition") if isinstance(current_record, dict) else None
        if execution_result is not None:
            existing_transition = execution_result
        existing_runner = (
            existing_transition.get("payload", {}).get("runner")
            if isinstance(existing_transition, dict) and isinstance(existing_transition.get("payload"), dict)
            else None
        )
        active_step = execution_steps[0] if execution_steps else None
        supports_runner = (
            (active_step is not None and active_step.kind == "resume" and active_step.target == "remaining_acceptance")
            or (not execution_steps and isinstance(existing_runner, dict))
        )
        if action["state"] == "resume_delivery" and frontier["state"] == "planned" and supports_runner and isinstance(existing_runner, dict):
            result["runner"] = existing_runner
        elif action["state"] == "resume_delivery" and frontier["state"] == "planned" and supports_runner and request.brief and request.config:
            launch_key = request.launch_key or f"takeover:{request.takeover_key}"
            intent_transition = _latest_transition(
                transitions,
                event_key=f"{request.takeover_key}:execution:intent",
                state="execution_intent",
            )
            prior_payload = intent_transition.get("payload") if isinstance(intent_transition, dict) else None
            if isinstance(prior_payload, dict):
                execution_payload = prior_payload
            else:
                adopted = report.get("adopted_threads", [])
                if not adopted and not report.get("unresolved") and not report.get("historical_facts", {}).get("source_observation"):
                    execution_payload = {"launch_key": launch_key, "frontier_digest": frontier["digest"]}
                else:
                    if not isinstance(adopted, list) or len(adopted) != 1 or not isinstance(adopted[0], dict):
                        raise RunnerError("thread_migration_source_ambiguous", "clean migration requires exactly one adopted source thread")
                    source_thread_id = str(adopted[0].get("thread_id") or "")
                    handover = adopted[0].get("handover")
                    if not source_thread_id or not isinstance(handover, dict):
                        raise RunnerError("thread_migration_handover_missing", "clean migration requires persisted source handover evidence")
                    execution_payload = {
                        "launch_key": launch_key,
                        "run_id": str(uuid.uuid4()),
                        "migration_key": f"{request.takeover_key}:thread-migration",
                        "source_thread_id": source_thread_id,
                        "handover": handover,
                        "owner_generation": 0,
                        "frontier_digest": frontier["digest"],
                    }
            step_event_key, step_payload = frontier_step_transition(
                active_step,
                state="frontier_step_started",
                payload={"launch_key": launch_key},
            )
            started = record_takeover_transition(
                control_root=request.control_root,
                takeover_key=request.takeover_key,
                state="frontier_step_started",
                event_key=f"{request.takeover_key}:{step_event_key}",
                payload=step_payload,
            )
            result["record"] = started["record"]
            result["transitions"] = started["transitions"]
            intent = record_takeover_transition(
                control_root=request.control_root,
                takeover_key=request.takeover_key,
                state="execution_intent",
                event_key=f"{request.takeover_key}:execution:intent",
                payload=execution_payload,
            )
            result["record"] = intent["record"]
            result["transitions"] = intent["transitions"]
            result["runner"] = self.start_runner(
                brief_file=request.brief,
                config_file=request.config,
                control_root=request.control_root,
                launch_key=launch_key,
                run_id=str(execution_payload.get("run_id") or uuid.uuid4()),
                takeover_key=request.takeover_key,
                migration=({
                    "migration_key": str(execution_payload["migration_key"]),
                    "source_thread_id": str(execution_payload["source_thread_id"]),
                    "handover": execution_payload["handover"],
                    "owner_generation": int(execution_payload.get("owner_generation", 0)),
                } if "migration_key" in execution_payload else None),
            )
            completed = record_takeover_transition(
                control_root=request.control_root,
                takeover_key=request.takeover_key,
                state="execution_started",
                event_key=f"{request.takeover_key}:execution:result:{digest(result['runner'])}",
                payload={"launch_key": launch_key, "runner": result["runner"]},
            )
            result["record"] = completed["record"]
            result["transitions"] = completed["transitions"]
        elif action["state"] == "resume_delivery" and bool(request.brief) != bool(request.config):
            raise RunnerError("takeover_inputs_incomplete", "takeover continuation requires both --brief and --config")

        runner = result.get("runner")
        if (
            active_step is not None
            and active_step.kind == "resume"
            and active_step.target == "remaining_acceptance"
            and isinstance(runner, dict)
        ):
            run_projection = runner.get("run")
            if isinstance(run_projection, dict) and run_projection.get("state") == "completed":
                verify_event_key, verify_payload = frontier_step_transition(
                    active_step,
                    state="frontier_step_verified",
                    payload={"runner": runner},
                )
                verified = record_takeover_transition(
                    control_root=request.control_root,
                    takeover_key=request.takeover_key,
                    state="frontier_step_verified",
                    event_key=f"{request.takeover_key}:{verify_event_key}",
                    payload=verify_payload,
                )
                result["record"] = verified["record"]
                result["transitions"] = verified["transitions"]
                remaining = frontier_execution_steps(frontier, result["transitions"])
                result["execution"] = {
                    "state": "verified" if not remaining else "partial",
                    "steps": [step.public() for step in remaining],
                }
