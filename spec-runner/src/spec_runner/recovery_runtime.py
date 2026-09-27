"""Durable recovery coordination behind the Runner orchestration seam.

``recovery.py`` owns the side-effect-free policy.  This module owns the
stateful part: observing a failure, reserving a retry budget, persisting the
decision and reading durable wait deadlines.  The public event and receipt
shapes intentionally remain unchanged.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from dataclasses import replace
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


@dataclass(frozen=True)
class RecoveryTransition:
    """Durable result of observing one failure at a lifecycle seam."""

    decision: RecoveryDecision
    state: str

    @property
    def waits(self) -> bool:
        return self.decision.action in {
            RecoveryAction.WAIT_RETRY,
            RecoveryAction.SERVICE_WAIT,
        }


@dataclass(frozen=True)
class RecoveryEpisode:
    """Coordinate durable recovery for one run through a narrow interface."""

    run: RunRecord
    store: Store

    @staticmethod
    def episode_identity(*, run_id: str, operation_kind: str, stage: str, generation: int = 0) -> str:
        identity = f"{run_id}:{operation_kind}:{stage}:{generation}"
        return "episode-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def record_failure(self, *, operation_id: str, error: RunnerError,
                       reconciliation_id: str | None = None) -> RecoveryDecision:
        """Persist one observed fault and its deterministic state transition."""
        run = self.run
        store = self.store
        operation_kind = "codex_turn" if run.backend_kind == "codex_sdk" else run.backend_kind
        episode_id = self.episode_identity(
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
        inspection_error = error.details.get("inspection_error") if isinstance(error.details, dict) else None
        external_result_unreconciled = error.code == "recovery_blocked" or bool(inspection_error)
        counter_name: str | None = None
        if not external_result_unreconciled:
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
        # A recovery_blocked error is raised when the Runner cannot prove the
        # outcome of an existing external operation (for example, a read-only
        # SDK thread inspection failed).  Feeding that error back into the
        # generic unknown-fault policy would permit one same-thread retry,
        # which could duplicate an unresolved provider side effect.  Preserve
        # the policy's budget projection, but force the durable action closed.
        if external_result_unreconciled:
            evidence = list(decision.evidence)
            if inspection_error:
                evidence.append(f"inspection_error:{inspection_error}")
            decision = replace(
                decision,
                action=RecoveryAction.BLOCKED,
                reason="external_result_unreconciled",
                evidence=tuple(dict.fromkeys(evidence)),
                preconditions=("manual_reconciliation_required", "do_not_create_worker"),
                next_check_at=None,
            )
        prior_observations = prior.get("observations", []) if isinstance(prior, dict) else []
        prior_observation = (prior_observations[-1].get("observation", {})
                             if prior_observations else {})
        prior_identity = prior_observation.get("request_id") or prior_observation.get("turn_id")
        if (
            attempt_identity and attempt_identity == prior_identity
            and existing.get("state") == decision.action.value
            and decision.action in {RecoveryAction.WAIT_RETRY, RecoveryAction.SERVICE_WAIT}
        ):
            deadline = (existing.get("retry_deadline") if decision.action == RecoveryAction.WAIT_RETRY
                        else existing.get("wait_deadline"))
            if deadline:
                decision = replace(decision, next_check_at=str(deadline))
        # A new provider attempt must have an identity that survives a process
        # restart. Without one, retrying could evade the durable budget or
        # replay a request whose outcome has not been reconciled.
        if (
            not attempt_identity
            and observation.request_admission == "rejected"
            and decision.action in {
            RecoveryAction.WAIT_RETRY,
            RecoveryAction.SERVICE_WAIT,
            RecoveryAction.RESUME_SAME_THREAD,
            RecoveryAction.USE_APPROVED_ROUTE,
            RecoveryAction.PROBE_CLEAN_CONTEXT,
            RecoveryAction.REQUEST_CLEAN_MIGRATION,
            }
        ):
            decision = replace(
                decision,
                action=RecoveryAction.OBSERVE,
                reason="attempt_identity_missing",
                preconditions=("reconcile_attempt_identity_before_retry",),
                next_check_at=None,
            )
        if route_circuit is not None:
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
        observation_identity = observation.request_id or observation.turn_id or operation_id
        observation_key = hashlib.sha256(observation_identity.encode("utf-8")).hexdigest()
        observation_id = f"{episode_id}:observation:{observation.fingerprint}:{observation_key}"
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
        if reconciliation_id is not None:
            decision_id += ":reconciled:" + hashlib.sha256(reconciliation_id.encode("utf-8")).hexdigest()
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

    def waits(self) -> bool:
        run = self.run
        store = self.store
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

    def wait_record(self) -> tuple[str, str] | None:
        store = self.store
        run_id = self.run.run_id
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


class RecoveryRuntime:
    """Coordinate durable failure transitions and legacy recovery calls."""

    def __init__(self, *, run: RunRecord, store: Store) -> None:
        self.run = run
        self.store = store

    def transition_failure(
        self,
        *,
        operation_id: str,
        error: RunnerError,
        allow_wait_for_config: bool = False,
        terminal_state: str = "blocked",
    ) -> RecoveryTransition:
        """Record a failure and persist the lifecycle state it permits.

        The initial stage may remain in ``wait_for_config`` while an existing
        run fails closed to ``blocked`` for the same decision.  Keeping that
        distinction here prevents each caller from implementing its own copy
        of the recovery state machine.
        """
        decision = RecoveryEpisode(run=self.run, store=self.store).record_failure(
            operation_id=operation_id,
            error=error,
        )
        waiting_actions = {
            RecoveryAction.WAIT_RETRY,
            RecoveryAction.SERVICE_WAIT,
        }
        if allow_wait_for_config:
            waiting_actions.add(RecoveryAction.WAIT_FOR_CONFIG)
        if decision.action in waiting_actions:
            state = decision.action.value
            self.store.fail_run(self.run.run_id, operation_id, state=state)
        else:
            state = terminal_state
            self.store.set_run_state(self.run.run_id, state)
            self.store.append_event(
                run_id=self.run.run_id,
                event_key=f"recovery:{self.run.run_id}:blocked",
                event_type="recovery_blocked",
                payload={"code": error.code, "message": error.message},
            )
        return RecoveryTransition(decision=decision, state=state)

    def reconcile_failed_turn(self, *, operation_id: str, thread_id: str,
                              turn_id: str) -> RecoveryTransition | None:
        """Apply the recorded fault policy after readback proves a turn failed.

        Older runs without a fault observation retain their existing recovery
        path. A recorded unknown outcome must be settled before another turn.
        """
        episode_id = RecoveryEpisode.episode_identity(
            run_id=self.run.run_id, operation_kind="codex_turn",
            stage=self.run.current_step,
        )
        episode = next((item for item in self.store.recovery_for_run(self.run.run_id)["episodes"]
                        if item["episode_id"] == episode_id), None)
        if episode is None:
            return None
        observations = episode["observations"]
        if not observations:
            return None
        decisions = episode["decisions"]
        prior = decisions[-1]["decision"] if decisions else {}
        prior_action = prior.get("action")
        if prior_action == RecoveryAction.BLOCKED.value and prior.get("reason") == "external_result_unreconciled":
            workers = self.store.workers_for_run(self.run.run_id)
            owner = workers[-1] if workers else {}
            if owner.get("external_thread_id") != thread_id or owner.get("external_turn_id") != turn_id:
                return None
            # The failed readback observation has no turn identity.  Only the
            # previous observation for the exact persisted turn may be settled.
            matched = next((item["observation"] for item in reversed(observations)
                            if item["observation"].get("thread_id") == thread_id
                            and item["observation"].get("turn_id") == turn_id), None)
            if matched is None:
                return None
            latest = matched
        else:
            latest = observations[-1]["observation"]
            if latest.get("thread_id") != thread_id or latest.get("turn_id") != turn_id:
                return None
        if latest.get("execution_outcome") not in {"unknown", "failed"}:
            return None
        fault = dict(latest)
        fault["execution_outcome"] = "failed"
        fault["request_admission"] = "accepted"
        if prior_action == RecoveryAction.OBSERVE.value or (
            prior_action == RecoveryAction.BLOCKED.value
            and prior.get("reason") == "external_result_unreconciled"
        ):
            decision = RecoveryEpisode(run=self.run, store=self.store).record_failure(
                operation_id=operation_id,
                error=RunnerError("sdk_turn_failed", str(fault.get("message") or "SDK turn failed"),
                                  details={"fault_observation": fault}),
                reconciliation_id=(f"{thread_id}:{turn_id}:failed"
                                   if prior_action == RecoveryAction.BLOCKED.value else None),
            )
        elif prior_action in {
            RecoveryAction.WAIT_FOR_CONFIG.value, RecoveryAction.BLOCKED.value,
        }:
            decision = RecoveryDecision(
                action=RecoveryAction(str(prior_action)), reason=str(prior.get("reason") or ""),
                evidence=tuple(str(item) for item in prior.get("evidence", [])),
                preconditions=tuple(str(item) for item in prior.get("preconditions", [])),
                next_check_at=prior.get("next_check_at"),
                remaining_budget=dict(prior.get("remaining_budget") or {}),
                family=str(prior.get("family") or "unknown"),
            )
        elif prior_action in {RecoveryAction.WAIT_RETRY.value, RecoveryAction.SERVICE_WAIT.value}:
            decision = RecoveryDecision(
                action=RecoveryAction(str(prior_action)), reason=str(prior.get("reason") or ""),
                evidence=tuple(str(item) for item in prior.get("evidence", [])),
                preconditions=tuple(str(item) for item in prior.get("preconditions", [])),
                next_check_at=prior.get("next_check_at"),
                remaining_budget=dict(prior.get("remaining_budget") or {}),
                family=str(prior.get("family") or "unknown"),
            )
        else:
            return None
        if latest.get("execution_outcome") == "unknown":
            observation_id = f"{episode_id}:terminal:{turn_id}:failed"
            self.store.record_recovery_observation(
                observation_id=observation_id, episode_id=episode_id, observation=fault,
            )
            self.store.append_event(
                run_id=self.run.run_id,
                event_key=f"recovery:{operation_id}:{observation_id}",
                event_type="fault_outcome_reconciled",
                payload={"episode_id": episode_id, "thread_id": thread_id,
                         "turn_id": turn_id, "execution_outcome": "failed"},
            )
        if decision.action in {RecoveryAction.WAIT_RETRY, RecoveryAction.SERVICE_WAIT}:
            prior_action = decision.action.value
            episode = self.store.recovery_episode(episode_id) or {}
            deadline = (episode["retry_deadline"] if prior_action == RecoveryAction.WAIT_RETRY.value
                        else episode["wait_deadline"])
            if not deadline:
                raise RunnerError("recovery_deadline_missing", "persisted recovery wait has no deadline")
            parsed = datetime.fromisoformat(str(deadline).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            if parsed <= datetime.now(timezone.utc):
                return None
            self.store.fail_run(self.run.run_id, operation_id, state=str(prior_action))
            return RecoveryTransition(decision=decision, state=str(prior_action))
        if decision.action == RecoveryAction.RESUME_SAME_THREAD:
            state = decision.action.value
        else:
            state = "blocked"
            if self.run.state != state:
                self.store.set_run_state(self.run.run_id, state)
        return RecoveryTransition(decision=decision, state=state)

    @staticmethod
    def episode_identity(*, run_id: str, operation_kind: str, stage: str, generation: int = 0) -> str:
        return RecoveryEpisode.episode_identity(
            run_id=run_id, operation_kind=operation_kind, stage=stage, generation=generation,
        )

    @staticmethod
    def episode(*, run: RunRecord, store: Store) -> RecoveryEpisode:
        return RecoveryEpisode(run=run, store=store)

    @staticmethod
    def record_failure(*, run: RunRecord, store: Store, operation_id: str,
                       error: RunnerError) -> RecoveryDecision:
        return RecoveryEpisode(run=run, store=store).record_failure(
            operation_id=operation_id, error=error,
        )

    @staticmethod
    def waits(*, run: RunRecord, store: Store) -> bool:
        return RecoveryEpisode(run=run, store=store).waits()

    @staticmethod
    def wait_record(*, store: Store, run_id: str) -> tuple[str, str] | None:
        run = store.find_by_run_id(run_id)
        if run is None:
            return None
        return RecoveryEpisode(run=run, store=store).wait_record()
