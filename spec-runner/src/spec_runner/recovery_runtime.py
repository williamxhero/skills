"""Durable recovery coordination behind the Runner orchestration seam.

``recovery.py`` owns the side-effect-free policy.  This module owns the
stateful part: observing a failure, reserving a retry budget, persisting the
decision and reading durable wait deadlines.  The public event and receipt
shapes intentionally remain unchanged.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from .errors import RunnerError
from .recovery import (
    FaultFamily,
    RecoveryAction,
    RecoveryDecision,
    RecoverySnapshot,
    decide_recovery,
    observation_from_error,
)
from .store import RunRecord, Store


class RecoveryRuntime:
    """Coordinate durable recovery state for one persisted run."""

    @staticmethod
    def episode_identity(*, run_id: str, operation_kind: str, stage: str, generation: int = 0) -> str:
        identity = f"{run_id}:{operation_kind}:{stage}:{generation}"
        return "episode-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @classmethod
    def record_failure(cls, *, run: RunRecord, store: Store, operation_id: str,
                       error: RunnerError) -> RecoveryDecision:
        """Persist one observed fault and its deterministic state transition."""
        operation_kind = "codex_turn" if run.backend_kind == "codex_sdk" else run.backend_kind
        episode_id = cls.episode_identity(
            run_id=run.run_id, operation_kind=operation_kind, stage=run.current_step,
        )
        existing = store.recovery_episode(episode_id) or {}
        if not existing:
            existing = store.upsert_recovery_episode(
                episode_id=episode_id, run_id=run.run_id, operation_kind=operation_kind,
                stage=run.current_step, generation=0,
            )
        workers = store.workers_for_run(run.run_id)
        worker = workers[-1] if workers else {}
        fault = error.details.get("fault_observation") if isinstance(error.details, dict) else None
        observation = observation_from_error(
            operation_kind=operation_kind,
            error=fault if isinstance(fault, dict) else error,
            run_id=run.run_id,
            stage=run.current_step,
            attempt=int(existing.get("same_thread_attempts") or 0) + int(existing.get("capacity_attempts") or 0) + 1,
            worker_id=(str(fault.get("worker_id")) if isinstance(fault, dict) and fault.get("worker_id") else (str(worker.get("worker_id")) if worker.get("worker_id") else None)),
            thread_id=(str(fault.get("thread_id")) if isinstance(fault, dict) and fault.get("thread_id") else (str(error.details.get("thread_id")) if error.details.get("thread_id") else None)),
            turn_id=(str(fault.get("turn_id")) if isinstance(fault, dict) and fault.get("turn_id") else (str(error.details.get("turn_id")) if error.details.get("turn_id") else None)),
            last_verified_progress=None,
        )
        route_circuit: dict[str, object] | None = None
        if observation.family == FaultFamily.ROUTE_NOT_FOUND.value and observation.route_scope != "unknown":
            cooldown = observation.retry_after_seconds
            try:
                cooldown_seconds = max(30.0, float(cooldown)) if cooldown is not None else 30.0
            except (TypeError, ValueError):
                cooldown_seconds = 30.0
            route_circuit = store.record_route_failure(
                route_scope=observation.route_scope,
                failure_fingerprint=observation.fingerprint,
                cooldown_seconds=cooldown_seconds,
                owner_token=f"recovery:{run.run_id}",
            )
        counters = {
            key: int(existing.get(key) or 0)
            for key in (
                "same_thread_attempts", "capacity_attempts", "route_probe_attempts",
                "clean_probe_attempts", "migration_attempts", "no_progress_attempts",
            )
        }
        prior_episodes = store.recovery_for_run(run.run_id).get("episodes", [])
        prior = next((item for item in prior_episodes if item.get("episode_id") == episode_id), {})
        decisions = prior.get("decisions", []) if isinstance(prior, dict) else []
        prior_action = decisions[-1].get("decision", {}).get("action") if decisions else None
        counter_name: str | None = None
        if observation.family == FaultFamily.CAPACITY.value:
            counter_name = "capacity_attempts"
        elif observation.family in {
            FaultFamily.FAST_NOT_CONFIGURED.value,
            FaultFamily.STREAM_DISCONNECTED.value,
            FaultFamily.UNKNOWN.value,
        }:
            counter_name = "same_thread_attempts"
        elif observation.family == FaultFamily.ROUTE_NOT_FOUND.value and prior_action == RecoveryAction.USE_APPROVED_ROUTE.value:
            counter_name = "route_probe_attempts"
        elif observation.family == FaultFamily.ENCRYPTED_ITEM_MISMATCH.value:
            if prior_action == RecoveryAction.PROBE_CLEAN_CONTEXT.value:
                counter_name = "clean_probe_attempts"
            elif prior_action == RecoveryAction.REQUEST_CLEAN_MIGRATION.value:
                counter_name = "migration_attempts"
        attempt_identity = observation.request_id or observation.turn_id
        if counter_name and attempt_identity:
            reserved = store.reserve_recovery_budget(
                reservation_id=f"{episode_id}:attempt:{attempt_identity}",
                episode_id=episode_id, counter_name=counter_name,
            )
            counters = {key: int(reserved.get(key) or 0) for key in counters}
        snapshot = RecoverySnapshot(
            run_id=run.run_id,
            operation_kind=operation_kind,
            stage=run.current_step,
            active_execution=worker.get("state") == "running" and observation.execution_outcome == "unknown",
            request_admission=observation.request_admission,
            execution_outcome=observation.execution_outcome,
            same_thread_attempts=counters["same_thread_attempts"],
            capacity_attempts=counters["capacity_attempts"],
            route_probe_attempts=counters["route_probe_attempts"],
            clean_probe_attempts=counters["clean_probe_attempts"],
            migration_attempts=counters["migration_attempts"],
            no_progress_attempts=counters["no_progress_attempts"],
            thread_id=observation.thread_id or (str(worker.get("external_thread_id")) if worker.get("external_thread_id") else None),
            turn_id=observation.turn_id or (str(worker.get("external_turn_id")) if worker.get("external_turn_id") else None),
        )
        decision = decide_recovery(snapshot, [observation], now=datetime.now(timezone.utc))
        if route_circuit is not None:
            from dataclasses import replace
            decision = replace(
                decision,
                evidence=decision.evidence + (f"route_circuit:{route_circuit['state']}",),
                preconditions=decision.preconditions + ("route_probe_requires_atomic_half_open_lease",),
            )
        state = decision.action.value
        store.upsert_recovery_episode(
            episode_id=episode_id, run_id=run.run_id, operation_kind=operation_kind,
            stage=run.current_step, generation=0, state=state, counters=counters,
            retry_deadline=decision.next_check_at if decision.action == RecoveryAction.WAIT_RETRY else None,
            wait_deadline=decision.next_check_at if decision.action == RecoveryAction.SERVICE_WAIT else None,
            last_verified_progress=observation.last_verified_progress,
        )
        observation_id = f"{episode_id}:observation:{observation.fingerprint}:{observation.turn_id or 'no-turn'}"
        store.record_recovery_observation(
            observation_id=observation_id, episode_id=episode_id,
            observation=observation.public(),
        )
        store.append_event(
            run_id=run.run_id,
            event_key=f"recovery:{operation_id}:observation:{observation_id}",
            event_type="fault_observed",
            payload={"operation_id": operation_id, "episode_id": episode_id,
                     "observation": observation.public()},
        )
        decision_id = f"{episode_id}:decision:{decision.action.value}:{observation.fingerprint}:{counters['same_thread_attempts']}:{counters['capacity_attempts']}"
        store.record_recovery_decision(
            decision_id=decision_id, episode_id=episode_id, decision=decision.public(),
        )
        store.append_event(
            run_id=run.run_id,
            event_key=f"recovery:{operation_id}:{decision_id}",
            event_type="recovery_decision_recorded",
            payload={"operation_id": operation_id, "episode_id": episode_id, "decision": decision.public()},
        )
        if route_circuit is not None:
            store.append_event(
                run_id=run.run_id,
                event_key=f"recovery:{operation_id}:route-circuit:{observation.fingerprint}",
                event_type="route_circuit_updated",
                payload={
                    "operation_id": operation_id,
                    "route_scope": observation.route_scope,
                    "state": route_circuit.get("state"),
                    "reason": route_circuit.get("reason"),
                    "failure_fingerprint": observation.fingerprint,
                },
            )
        action_events = {
            RecoveryAction.OBSERVE: "reconcile_started",
            RecoveryAction.WAIT_RETRY: "retry_scheduled",
            RecoveryAction.SERVICE_WAIT: "service_wait",
            RecoveryAction.RESUME_SAME_THREAD: "retry_started",
            RecoveryAction.USE_APPROVED_ROUTE: "route_changed",
            RecoveryAction.PROBE_CLEAN_CONTEXT: "probe_result",
            RecoveryAction.REQUEST_CLEAN_MIGRATION: "migration_requested",
            RecoveryAction.ADOPT_RESULT: "progress_verified",
            RecoveryAction.BLOCKED: "recovery_blocked",
        }
        event_type = action_events.get(decision.action)
        if event_type:
            store.append_event(
                run_id=run.run_id,
                event_key=f"recovery:{operation_id}:{decision_id}:{event_type}",
                event_type=event_type,
                payload={"operation_id": operation_id, "episode_id": episode_id,
                         "action": decision.action.value, "next_check_at": decision.next_check_at,
                         "reason": decision.reason},
            )
        return decision

    @staticmethod
    def waits(*, run: RunRecord, store: Store) -> bool:
        recovery = store.recovery_for_run(run.run_id).get("episodes", [])
        if not recovery:
            return False
        episode = recovery[-1]
        decisions = episode.get("decisions") if isinstance(episode, dict) else None
        decision = decisions[-1].get("decision") if isinstance(decisions, list) and decisions else None
        if not isinstance(decision, dict):
            return False
        action = str(decision.get("action") or "")
        if run.state not in {action, "paused"} or action == RecoveryAction.OBSERVE.value:
            return False
        if action not in {
            RecoveryAction.WAIT_RETRY.value, RecoveryAction.SERVICE_WAIT.value,
            RecoveryAction.WAIT_FOR_CONFIG.value, RecoveryAction.BLOCKED.value,
            RecoveryAction.NEEDS_INPUT.value, RecoveryAction.PROBE_CLEAN_CONTEXT.value,
            RecoveryAction.REQUEST_CLEAN_MIGRATION.value, RecoveryAction.USE_APPROVED_ROUTE.value,
            RecoveryAction.RECONNECT_RUNTIME.value,
        }:
            return False
        if run.state == "paused":
            control = store.control_for_run(run.run_id)
            if control and control.get("requested_state") in {"pause_requested", "cancel_requested"}:
                return True
            store.set_run_state(run.run_id, action)
        deadline = episode.get("wait_deadline") if action == RecoveryAction.SERVICE_WAIT.value else episode.get("retry_deadline")
        if deadline:
            try:
                parsed_deadline = datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
                if parsed_deadline.tzinfo is None:
                    parsed_deadline = parsed_deadline.replace(tzinfo=timezone.utc)
                if parsed_deadline > datetime.now(timezone.utc):
                    return True
            except (TypeError, ValueError):
                return True
            store.fail_run(run.run_id, f"start:{run.run_id}", state="failed")
            store.append_event(
                run_id=run.run_id,
                event_key=f"recovery:{run.run_id}:wait-expired:{action}",
                event_type="recovery_wait_expired",
                payload={"action": action, "deadline": deadline},
            )
            return False
        return True

    @staticmethod
    def wait_record(*, store: Store, run_id: str) -> tuple[str, str] | None:
        recovery = store.recovery_for_run(run_id).get("episodes", [])
        if not isinstance(recovery, list) or not recovery:
            return None
        episode = recovery[-1]
        decisions = episode.get("decisions") if isinstance(episode, dict) else None
        decision = decisions[-1].get("decision") if isinstance(decisions, list) and decisions else None
        if not isinstance(decision, dict):
            return None
        action = str(decision.get("action") or "")
        if action not in {RecoveryAction.WAIT_RETRY.value, RecoveryAction.SERVICE_WAIT.value}:
            return None
        key = "wait_deadline" if action == RecoveryAction.SERVICE_WAIT.value else "retry_deadline"
        deadline = episode.get(key)
        if not isinstance(deadline, str) or not deadline:
            raise RunnerError("recovery_deadline_missing", "persisted recovery wait has no deadline", details={"run_id": run_id, "action": action})
        try:
            parsed = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RunnerError("recovery_deadline_invalid", "persisted recovery wait deadline is invalid", details={"run_id": run_id, "action": action}) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return action, parsed.astimezone(timezone.utc).isoformat()

