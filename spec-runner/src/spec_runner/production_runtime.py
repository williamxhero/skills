"""Production delivery orchestration behind the public Runner seam.

This module owns queue selection and cleanup replay.  Git, GitHub, Store and
worker implementations remain adapters supplied by ``workflow`` so this
module only coordinates their existing contracts.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Mapping

from .config import RunnerConfig
from .errors import RunnerError
from .models import RunContext
from .plans import digest, validate_spec_plan, validate_ticket_plan
from .recovery import RecoveryAction
from .recovery_runtime import RecoveryEpisode
from .store import RunRecord, Store
from .tracker import PlanSnapshot, publish_local, read_local
from .takeover_discovery import build_adopted_plans, validate_snapshot


JsonLoader = Callable[[Path], dict[str, object]]
JsonWriter = Callable[[Path, dict[str, object]], None]
ArtifactDirectory = Callable[[Path, RunnerConfig, str], Path]
TicketRunner = Callable[..., RunRecord]
PlanningRunner = Callable[..., RunRecord]
ImplementationRunner = Callable[..., dict[str, object]]
CleanupRunner = Callable[..., dict[str, object]]
TicketCloser = Callable[..., dict[str, object]]
GitHubDeliveryRunner = Callable[..., dict[str, object]]
GitHubRecoveryRunner = Callable[..., dict[str, object]]
ReviewedDeliveryRunner = Callable[..., dict[str, object]]
ReviewRunner = Callable[..., dict[str, object]]
FailedChecks = Callable[..., bool]


@dataclass(frozen=True)
class ProductionPorts:
    """Adapters used by production orchestration at its external seam."""

    artifact_directory: ArtifactDirectory
    load_json: JsonLoader
    execute_planning: PlanningRunner
    write_json_atomic: JsonWriter
    execute_tickets: TicketRunner
    execute_implementation: ImplementationRunner
    cleanup_workspace: CleanupRunner
    close_ticket_plan: TicketCloser
    execute_github_delivery: GitHubDeliveryRunner | None = None
    recover_github_candidate: GitHubRecoveryRunner | None = None
    resume_reviewed_delivery: ReviewedDeliveryRunner | None = None
    execute_review: ReviewRunner | None = None
    definitive_failed_checks: FailedChecks | None = None
    reconcile_local_delivery: Callable[..., dict[str, object]] | None = None
    base_revision: Callable[..., str] | None = None


@dataclass(frozen=True)
class ProductionWorkflow:
    """Drive production SPECs and replay their durable cleanup evidence."""

    context: RunContext
    ports: ProductionPorts

    @property
    def control_root(self) -> Path:
        return self.context.control_root

    @property
    def config(self) -> RunnerConfig:
        return self.context.config

    @property
    def brief_digest(self) -> str:
        return self.context.brief_digest

    @property
    def run(self) -> RunRecord:
        return self.context.run

    @property
    def store(self) -> Store:
        return self.context.store

    def _artifact(self) -> Path:
        return self.ports.artifact_directory(self.control_root, self.config, self.run.run_id)

    def artifact_directory(self) -> Path:
        """Return the durable artifact directory for this production run."""
        return self._artifact()

    def intake(self) -> RunRecord:
        """Adopt an explicit local tracker frontier into this run.

        The tracker is an input here: its records are read, validated, and
        rebound to the current run through ordinary publication receipts.
        No model planner or ticket planner is invoked for records that are
        complete in the supplied frontier.
        """
        root = self.config.intake_root
        entry_key = self.config.intake_entry
        if root is None or not entry_key:
            raise RunnerError("intake_config_missing", "tracker intake requires a root and entry key")
        if not self.config.acceptance_ids:
            raise RunnerError("intake_acceptance_missing", "tracker intake requires explicit acceptance IDs")
        snapshot = read_local(root)
        records = {record.key: record for record in snapshot.records}
        if entry_key not in records:
            raise RunnerError("unknown_plan_entry", f"entry key does not exist: {entry_key}")
        direct_specs = [
            record for record in snapshot.records
            if record.parent == entry_key and record.kind.casefold() in {"spec", "issue"}
        ]
        selected_specs = direct_specs or [records[entry_key]]
        if any(record.kind.casefold() not in {"spec", "issue"} for record in selected_specs):
            raise RunnerError("invalid_plan_entry", "tracker intake entry must identify a SPEC or an umbrella with SPEC children")
        selected_keys = {record.key for record in selected_specs}
        by_key = {record.key: record for record in selected_specs}
        ordered: list[object] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise RunnerError("plan_cycle", f"tracker intake dependency cycle contains {key}")
            if key in visited:
                return
            visiting.add(key)
            record = by_key[key]
            for dependency in record.blocked_by:
                if dependency not in selected_keys:
                    raise RunnerError("unknown_plan_dependency", f"tracker intake references unknown SPEC dependency: {dependency}")
                visit(dependency)
            visiting.remove(key)
            visited.add(key)
            ordered.append(record)

        for record in selected_specs:
            visit(record.key)

        requirements = list(self.config.acceptance_ids)
        specs: list[dict[str, object]] = []
        ticket_documents: list[tuple[str, dict[str, object], set[str]]] = []
        for selected in ordered:
            record = selected
            children = [
                candidate for candidate in snapshot.records
                if candidate.parent == record.key and candidate.kind.casefold() in {"ticket", "issue"}
            ]
            child_keys = {candidate.key for candidate in children}
            if any(dependency not in child_keys for candidate in children for dependency in candidate.blocked_by):
                raise RunnerError("unknown_plan_dependency", f"tracker intake ticket dependency escapes SPEC {record.key}")
            specs.append({
                "key": record.key,
                "title": record.title,
                "body": record.body,
                "blocked_by": list(record.blocked_by),
                "covers": requirements,
                "route": {"model": "adopted", "effort": "none", "reason": "validated local tracker intake"},
            })
            if children:
                ticket_documents.append((record.key, {
                    "schema_version": "spec-runner-ticket-plan/v1",
                    "outcome": "planned",
                    "questions": [],
                    "spec_plan_digest": "",
                    "spec_key": record.key,
                    "spec_title": record.title,
                    "spec_body": record.body,
                    "tickets": [{
                        "key": child.key,
                        "title": child.title,
                        "body": child.body,
                        "blocked_by": list(child.blocked_by),
                        "acceptance": requirements,
                    } for child in children],
                }, {record.key, *child_keys}))
        plan = validate_spec_plan({
            "schema_version": "spec-runner-spec-plan/v1",
            "outcome": "planned",
            "questions": [],
            "requirement_digest": self.brief_digest,
            "requirements": requirements,
            "specs": specs,
        })
        artifact = self._artifact()
        artifact.mkdir(parents=True, exist_ok=True)
        plan_path = artifact / "spec-plan.json"
        if plan_path.is_file() and self.ports.load_json(plan_path) != plan:
            raise RunnerError("intake_plan_conflict", "tracker intake conflicts with the persisted SpecPlan")
        self.ports.write_json_atomic(plan_path, plan)
        current = self.store.complete_adopted_stage(
            self.run.run_id, f"start:{self.run.run_id}", step_name="tracker_intake",
            worker_id=f"tracker_intake:{self.run.run_id}", state="planned",
            source_digest=str(plan["digest"]),
        )
        for spec_key, document, keys in ticket_documents:
            document["spec_plan_digest"] = plan["digest"]
            base_revision = self.ports.base_revision
            if base_revision is None:
                raise RunnerError("intake_git_missing", "tracker intake has no Git revision adapter")
            document["base_sha"] = base_revision(
                repository=self.config.repository_path,
                ref=self.config.target_ref,
                timeout_seconds=self.config.git_timeout_seconds,
            )
            ticket = validate_ticket_plan(document, expected_spec_key=spec_key, expected_base_sha=str(document["base_sha"]))
            ticket_path = artifact / f"ticket-plan-{spec_key}.json"
            if ticket_path.is_file() and self.ports.load_json(ticket_path) != ticket:
                raise RunnerError("intake_ticket_conflict", f"tracker intake conflicts with TicketPlan {spec_key}")
            self.ports.write_json_atomic(ticket_path, ticket)
            selected_records = tuple(record for record in snapshot.records if record.key in keys)
            adopted = PlanSnapshot(
                schema_version=snapshot.schema_version,
                source=snapshot.source,
                root=snapshot.root,
                relation_mode=snapshot.relation_mode,
                records=selected_records,
                digest=hashlib.sha256(
                    "\n".join(f"{record.key}:{record.digest}" for record in selected_records).encode("utf-8")
                ).hexdigest(),
            )
            publish_local(
                adopted, root, operation_id=f"tickets:{self.run.run_id}:{spec_key}",
                adopt_matching_revision=True,
            )
            operation_id = f"tickets:{self.run.run_id}:{spec_key}"
            worker_id = f"tracker_intake:{self.run.run_id}:codex_ticket_planning:{spec_key}"
            self.store.begin_stage(
                self.run.run_id, step_name="codex_ticket_planning", operation_id=operation_id,
                backend_kind="tracker_intake", worker_id=worker_id,
            )
            current = self.store.complete_adopted_stage(
                self.run.run_id, operation_id, step_name="codex_ticket_planning",
                worker_id=worker_id, state="tickets_ready", source_digest=str(ticket["digest"]),
            )
        return current

    def plan(self, *, thread_id: str | None = None) -> RunRecord:
        """Run the production planning stage through the production seam.

        The compatibility workflow supplies the SDK adapter as a port.  This
        keeps the lifecycle entry focused on leases and recovery while this
        module owns the transition from a production brief to a persisted
        SpecPlan.
        """
        run, store = self.run, self.store
        takeover_context = self._takeover_context()
        if takeover_context is not None:
            return self.adopt_takeover(takeover_context)
        if getattr(self.config, "prepared_spec_plan", None) is not None:
            path = self._artifact() / "spec-plan.json"
            source_path = self.config.prepared_spec_plan
            source = self.ports.load_json(source_path if source_path.is_file() else path)
            if (source.get("outcome") != "planned"
                    or source.get("questions") not in (None, [])
                    or source.get("requirement_digest") != self.brief_digest):
                raise RunnerError("prepared_plan_invalid", "prepared SpecPlan must match the current brief and have no open questions")
            validated = validate_spec_plan(source)
            known_specs = {str(spec["key"]) for spec in validated["specs"]}
            unknown_tickets = set(dict(self.config.prepared_ticket_plans)) - known_specs
            if unknown_tickets:
                raise RunnerError("prepared_plan_invalid", "prepared TicketPlan references an unknown SPEC",
                                  details={"spec_keys": sorted(unknown_tickets)})
            if path.is_file() and self.ports.load_json(path) != validated:
                raise RunnerError("prepared_plan_conflict", "prepared SpecPlan conflicts with the persisted run")
            self.ports.write_json_atomic(path, validated)
            return store.complete_prepared_stage(
                run.run_id, f"start:{run.run_id}",
                step_name="prepared_planning",
                worker_id=f"prepared_plan:{run.run_id}",
                source_digest=str(validated["digest"]),
            )
        return self.ports.execute_planning(
            control_root=self.control_root,
            config=self.config,
            brief=self.context.brief,
            brief_digest=self.brief_digest,
            run=run,
            store=store,
            thread_id=thread_id,
        )

    def _takeover_context(self) -> dict[str, object] | None:
        path = self._artifact() / "takeover-context.json"
        if not path.is_file():
            return None
        context = self.ports.load_json(path)
        if context.get("schema_version") != "spec-runner-takeover-context/v1":
            raise RunnerError("takeover_context_invalid", "takeover continuation context has an unexpected schema")
        if context.get("run_id") != self.run.run_id:
            raise RunnerError("takeover_context_invalid", "takeover continuation context belongs to another run")
        record = context.get("record")
        if not isinstance(record, dict) or not isinstance(record.get("report"), dict):
            raise RunnerError("takeover_context_invalid", "takeover continuation context has no takeover record")
        return record

    def adopt_takeover(self, takeover_record: dict[str, object]) -> RunRecord:
        """Materialize one immutable discovery snapshot into this Runner run."""
        report = takeover_record.get("report")
        if not isinstance(report, dict):
            raise RunnerError("takeover_context_invalid", "takeover record has no report")
        if report.get("next_state") in {"blocked", "waiting_handover"}:
            raise RunnerError("takeover_blocked", "takeover discovery contains an unresolved blocker")
        facts = report.get("historical_facts")
        snapshot = facts.get("takeover_snapshot") if isinstance(facts, dict) else None
        if not isinstance(snapshot, dict):
            raise RunnerError("takeover_discovery_missing", "takeover record has no discovery snapshot")
        validate_snapshot(snapshot)
        snapshot_digest = str(snapshot["digest"])
        artifact = self._artifact()
        artifact.mkdir(parents=True, exist_ok=True)
        spec_path = artifact / "spec-plan.json"
        graph = snapshot.get("graph")
        if not isinstance(graph, dict):
            raise RunnerError("takeover_discovery_missing", "takeover snapshot has no issue graph")
        local = graph.get("local")
        base_sha = str(local.get("target_sha") or "") if isinstance(local, dict) else ""
        if len(base_sha) < 7:
            raise RunnerError("takeover_target_readback_missing", "takeover snapshot has no target SHA")
        if spec_path.is_file():
            spec_plan = validate_spec_plan(self.ports.load_json(spec_path))
            if spec_plan.get("takeover_snapshot_digest") != snapshot_digest:
                raise RunnerError("takeover_source_changed", "persisted SpecPlan belongs to another discovery snapshot")
        else:
            adopted = build_adopted_plans(
                snapshot, model=self.config.model_name, effort=self.config.effort, base_sha=base_sha,
            )
            spec_plan = validate_spec_plan({
                **adopted["spec_plan"],
                "takeover_snapshot_digest": snapshot_digest,
                "umbrella_issue": (graph.get("umbrella", {}).get("number")
                                    if isinstance(graph.get("umbrella"), dict) else None),
            })
            self.ports.write_json_atomic(spec_path, spec_plan)
            for key, ticket_plan in adopted["ticket_plans"].items():
                self.ports.write_json_atomic(
                    artifact / f"ticket-plan-{key}.json",
                    validate_ticket_plan(
                        {**ticket_plan, "takeover_snapshot_digest": snapshot_digest},
                        expected_spec_key=key,
                    ),
                )

        discovered = {
            str(item.get("key")): item
            for item in graph.get("specs", [])
            if isinstance(item, dict) and isinstance(item.get("key"), str)
        }
        normalized_specs: list[dict[str, object]] = []
        completed_keys: list[str] = []
        for spec in spec_plan.get("specs", []):
            if not isinstance(spec, dict):
                raise RunnerError("invalid_spec_plan", "adopted SpecPlan contains a non-object SPEC")
            key = str(spec["key"])
            source = discovered.get(key)
            if not isinstance(source, dict):
                raise RunnerError("takeover_spec_identity_missing", f"discovery snapshot has no SPEC {key}")
            source_value = {
                "frontier": source.get("frontier"),
                "candidate_sha": source.get("candidate_sha"),
                "branch": source.get("branch"),
                "workspace": source.get("workspace"),
                "pull_request": source.get("pull_request"),
                "checks": source.get("checks"),
                "candidate_receipt": source.get("candidate_receipt"),
                "review_receipt": source.get("review_receipt"),
                "github_issue": source.get("github_issue"),
                "delivery": source.get("delivery"),
            }
            spec["takeover_source"] = source_value
            normalized_specs.append(spec)

            ticket_path = artifact / f"ticket-plan-{key}.json"
            if ticket_path.is_file() and self.config.github_repository:
                self._adopt_github_publication(
                    validate_ticket_plan(self.ports.load_json(ticket_path), expected_spec_key=key),
                )

            frontier = source.get("frontier")
            complete = frontier.get("complete_evidence") if isinstance(frontier, dict) else None
            delivery = source.get("delivery")
            if not (isinstance(complete, dict) and complete.get("complete") is True and isinstance(delivery, dict)):
                continue
            delivery_path = artifact / f"delivery-{key}.json"
            adopted_delivery = dict(delivery)
            adopted_delivery.update({
                "schema_version": "spec-runner-production-delivery/v1",
                "run_id": self.run.run_id,
                "spec_key": key,
                "plan_digest": spec_plan.get("digest"),
                "discovery_snapshot_digest": snapshot_digest,
                "adopted": True,
            })
            if ticket_path.is_file():
                ticket_plan = validate_ticket_plan(self.ports.load_json(ticket_path), expected_spec_key=key)
                adopted_delivery["ticket_plan_digest"] = ticket_plan.get("digest")
            if delivery_path.is_file():
                existing = self.ports.load_json(delivery_path)
                if existing.get("discovery_snapshot_digest") != snapshot_digest:
                    raise RunnerError("takeover_source_changed", f"delivery evidence for {key} belongs to another snapshot")
                adopted_delivery = existing
            else:
                self.ports.write_json_atomic(delivery_path, adopted_delivery)
            completed_keys.append(key)

        spec_plan = validate_spec_plan({**spec_plan, "specs": normalized_specs})
        self.ports.write_json_atomic(spec_path, spec_plan)
        for key in completed_keys:
            delivery_path = artifact / f"delivery-{key}.json"
            delivery = self.ports.load_json(delivery_path)
            delivery["plan_digest"] = spec_plan.get("digest")
            self.ports.write_json_atomic(delivery_path, delivery)
            self.record_spec(spec_key=key, plan_digest=str(spec_plan.get("digest") or ""))
        worker_id = f"{self.run.backend_kind}:{self.run.run_id}"
        return self.store.complete_adopted_stage(
            self.run.run_id,
            f"start:{self.run.run_id}",
            step_name="codex_planning",
            worker_id=worker_id,
            state="planned",
            source_digest=digest({"snapshot": snapshot_digest, "plan": spec_plan.get("digest")}),
        )

    def _adopt_github_publication(self, ticket_plan: dict[str, object]) -> None:
        identities = ticket_plan.get("github_issues")
        tickets = ticket_plan.get("tickets")
        operation_id = str(ticket_plan.get("github_operation_id") or "")
        if (not isinstance(identities, list) or not isinstance(tickets, list)
                or len(identities) != len(tickets) + 1 or not operation_id):
            raise RunnerError("takeover_issue_identity_missing", "adopted TicketPlan has incomplete GitHub identities")
        issues = []
        for item in identities:
            if (not isinstance(item, dict) or not isinstance(item.get("key"), str)
                    or not isinstance(item.get("number"), int)
                    or (item.get("marker") is not None and not isinstance(item.get("marker"), str))):
                raise RunnerError("takeover_issue_identity_missing", "adopted Issue identity is incomplete")
            issues.append({"key": item["key"], "number": item["number"], "marker": item["marker"], "adopted": True})
        receipt = {
            "operation_id": operation_id,
            "draft_digest": digest(self.ports.load_json(self._artifact() / f"ticket-plan-{ticket_plan['spec_key']}.json")),
            "repository": self.config.github_repository,
            "issues": issues,
            "relation_evidence": {"mode": "native", "native": True, "adopted": True},
            "complete": True,
        }
        existing = self.store.external_operation(operation_id)
        if existing and existing.get("state") == "completed":
            if existing.get("receipt") != receipt:
                raise RunnerError("takeover_publication_conflict", "adopted Issue receipt changed")
            return
        self.store.prepare_external_operation(
            operation_id=operation_id,
            run_id=self.run.run_id,
            operation_kind="github_issue_publication",
            repository=str(self.config.github_repository),
            input_digest=str(receipt["draft_digest"]),
        )
        self.store.complete_external_operation(operation_id=operation_id, receipt=receipt)

    def completed_specs(self) -> set[str]:
        """Read Store completion receipts; validate the JSON projection only."""
        target_run_id = self.run.run_id
        store = self.store
        persisted = store.production_completed_specs(target_run_id)
        path = self._artifact() / "completed-specs.json"
        if not path.exists():
            return persisted
        document = self.ports.load_json(path)
        values = document.get("specs", [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise RunnerError("completed_specs_corrupt", "completed SPEC record is invalid")
        # The file is a human-readable projection. Only transactional Store
        # receipts may advance the production queue.
        return persisted

    def record_spec(self, *, spec_key: str, plan_digest: str) -> None:
        target_run_id = self.run.run_id
        store = self.store
        directory = self._artifact()
        completed = self.completed_specs()
        completed.add(spec_key)
        receipt = self.ports.load_json(directory / f"delivery-{spec_key}.json")
        delivery_digest = hashlib.sha256(
            json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        store.complete_production_spec(
            run_id=target_run_id,
            spec_key=spec_key,
            plan_digest=plan_digest,
            delivery_digest=delivery_digest,
        )
        path = directory / "completed-specs.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "schema_version": "spec-runner-completed-specs/v1",
                    "plan_digest": plan_digest,
                    "specs": sorted(completed),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(path)

    def persist_delivery_evidence(self, *, spec_key: str, delivery: dict[str, object]) -> None:
        target_run_id = self.run.run_id
        artifact = self._artifact()
        plan = self.ports.load_json(artifact / "spec-plan.json")
        ticket = self.ports.load_json(artifact / f"ticket-plan-{spec_key}.json")
        record = {
            "schema_version": "spec-runner-production-delivery/v1",
            "run_id": target_run_id,
            "spec_key": spec_key,
            "plan_digest": plan.get("digest"),
            "ticket_plan_digest": ticket.get("digest"),
            **delivery,
        }
        self.ports.write_json_atomic(artifact / f"delivery-{spec_key}.json", record)

    def continue_after_spec(self, result: Mapping[str, object]) -> dict[str, object]:
        """Continue the durable production queue after one SPEC completes.

        Delivery recovery paths may finish one SPEC while the process is
        already inside a persisted run.  Reloading the run and its plan here
        keeps that transition in one module instead of making every stage
        route know how to resume the queue.
        """
        if result.get("state") != "spec_completed":
            return dict(result)
        run, store = self.run, self.store
        current = store.find_by_run_id(run.run_id)
        if current is None:
            raise RunnerError("run_status_missing", "completed production SPEC lost its durable run")
        plan_path = self._artifact() / "spec-plan.json"
        if not plan_path.is_file():
            raise RunnerError("spec_plan_missing", "completed production SPEC has no persisted SpecPlan")
        plan = self.ports.load_json(plan_path)
        spec_key = result.get("spec_key")
        if isinstance(spec_key, str) and spec_key not in self.completed_specs():
            if not any(isinstance(spec, dict) and spec.get("key") == spec_key for spec in plan.get("specs", [])):
                raise RunnerError("production_spec_mismatch", "completed delivery belongs to another plan")
            if self.config.github_repository is not None:
                self.record_spec(spec_key=spec_key, plan_digest=str(plan.get("digest", "")))
        return replace(self, context=replace(self.context, run=current)).run_queue(plan)

    def resume_reviewed_delivery(self) -> dict[str, object]:
        """Resume the delivery frontier after an independent review was approved."""
        if self.ports.resume_reviewed_delivery is None:
            raise RunnerError("review_runtime_missing", "production review recovery adapter is not configured")
        return self.ports.resume_reviewed_delivery(
            control_root=self.control_root,
            config=self.config,
            run=self.run,
            brief_digest=self.brief_digest,
            store=self.store,
        )

    def _control_boundary(self) -> dict[str, object] | None:
        """Stop production before a new side effect when control is pending."""
        run, store = self.run, self.store
        control = store.control_for_run(run.run_id)
        requested = str(control.get("requested_state")) if control else ""
        if requested not in {"pause_requested", "cancel_requested"}:
            return None
        stopped_state = "paused" if requested == "pause_requested" else "cancelled"
        if run.state != stopped_state:
            store.set_run_state(run.run_id, stopped_state)
        generation = control.get("generation") if control else None
        store.append_event(
            run_id=run.run_id,
            event_key=f"control:{run.run_id}:{generation}:applied",
            event_type="control_applied",
            payload={"requested_state": requested, "generation": generation, "during": "production"},
        )
        return {"state": stopped_state, **store.public_status(run.run_id)}

    def start_queue(self, spec_plan: dict[str, object]) -> dict[str, object]:
        """Run the initial production queue and durably close a failed transition."""
        try:
            return self.run_queue(spec_plan)
        except RunnerError as exc:
            run, store = self.run, self.store
            current = store.find_by_run_id(run.run_id) or run
            decision = RecoveryEpisode(run=current, store=store).record_failure(
                operation_id=f"start:{run.run_id}", error=exc,
            )
            state = decision.action.value if decision.action in {
                RecoveryAction.WAIT_RETRY,
                RecoveryAction.SERVICE_WAIT,
                RecoveryAction.WAIT_FOR_CONFIG,
            } else "failed"
            try:
                store.fail_run(run.run_id, f"start:{run.run_id}", state=state)
            except RunnerError as state_error:
                raise state_error from exc
            raise

    def run_queue(self, spec_plan: dict[str, object]) -> dict[str, object]:
        """Select and complete dependency-ready SPECs in plan order."""
        run, store = self.run, self.store
        if (
            self.ports.execute_github_delivery is None
            or self.ports.recover_github_candidate is None
            or self.ports.definitive_failed_checks is None
        ):
            raise RunnerError("github_runtime_missing", "production GitHub recovery adapters are not configured")
        specs = spec_plan.get("specs")
        if not isinstance(specs, list) or any(not isinstance(item, dict) for item in specs):
            raise RunnerError("invalid_spec_plan", "production queue requires keyed SPEC objects")
        stopped = self._control_boundary()
        if stopped is not None:
            return stopped
        completed = self.completed_specs()
        if run.state == "spec_completed" and self.config.github_repository is None:
            for spec in specs:
                spec_key = str(spec.get("key"))
                receipt_path = self._artifact() / f"delivery-{spec_key}.json"
                if not receipt_path.is_file():
                    continue
                if self.ports.reconcile_local_delivery is None:
                    raise RunnerError("production_recovery_missing", "local delivery reconciliation is not configured")
                receipt = self.ports.reconcile_local_delivery(
                    control_root=self.control_root, config=self.config, run=run,
                    store=store, spec_key=spec_key,
                )
                try:
                    receipt["issue_closure"] = self.ports.close_ticket_plan(
                        config=self.config,
                        plan=self.ports.load_json(self._artifact() / f"ticket-plan-{spec_key}.json"),
                        run_id=run.run_id, store=store, delivery_evidence=receipt,
                    )
                except RunnerError as exc:
                    store.mark_cleanup_pending(run.run_id)
                    return {"state": "cleanup_pending", "spec_key": spec_key,
                            "issue_closure": {"state": "pending", "error_code": exc.code}}
                if spec_key not in completed:
                    self.ports.write_json_atomic(receipt_path, receipt)
                    self.record_spec(spec_key=spec_key, plan_digest=str(spec_plan.get("digest", "")))
                    completed.add(spec_key)
        while len(completed) < len(specs):
            stopped = self._control_boundary()
            if stopped is not None:
                return stopped
            ready = [
                item for item in specs
                if str(item.get("key")) not in completed
                and set(item.get("blocked_by", [])) <= completed
            ]
            if not ready:
                raise RunnerError("production_queue_blocked", "no dependency-ready SPEC remains")
            spec = ready[0]
            spec_key = str(spec["key"])
            takeover_delivery = self._resume_takeover_delivery(spec=spec, spec_plan=spec_plan)
            if takeover_delivery is not None:
                if takeover_delivery.get("state") != "spec_completed":
                    return takeover_delivery
                completed.add(spec_key)
                run = store.find_by_run_id(run.run_id)
                if run is None:
                    raise RunnerError("run_missing", "production queue run disappeared during takeover reconciliation")
                continue
            ticket_files = sorted(self._artifact().glob(f"ticket-plan-{spec_key}.json"))
            adopted_ticket = bool(ticket_files) and (
                run.state == "tickets_ready"
                or (self.config.intake_root is not None and run.state in {"planned", "spec_completed"})
                or isinstance(spec.get("takeover_source"), dict)
            )
            if adopted_ticket:
                ticketed = run
            else:
                ticketed = self.ports.execute_tickets(
                    control_root=self.control_root,
                    config=self.config,
                    brief_digest=self.brief_digest,
                    run=run,
                    store=store,
                    spec_plan={**spec_plan, "specs": [spec]},
                )
            if ticketed.state != "tickets_ready" and not adopted_ticket:
                current = store.find_by_run_id(run.run_id) or run
                return {"state": current.state, **store.public_status(run.run_id)}
            stopped = self._control_boundary()
            if stopped is not None:
                return stopped
            ticket_files = sorted(self._artifact().glob(f"ticket-plan-{spec_key}.json"))
            if not ticket_files:
                raise RunnerError("ticket_plan_missing", f"SPEC {spec_key} has no persisted TicketPlan")
            ticket_plan = self.ports.load_json(ticket_files[-1])
            if ticket_plan.get("spec_key") != spec_key:
                raise RunnerError("ticket_plan_mismatch", "persisted TicketPlan belongs to another SPEC")
            delivered = self.ports.execute_implementation(
                control_root=self.control_root,
                config=self.config,
                brief_digest=self.brief_digest,
                run=ticketed,
                store=store,
                ticket_plan=ticket_plan,
                finalize_run=False,
            )
            if delivered.get("state") in {"waiting_ci", "waiting_merge_queue", "cleanup_pending"}:
                return delivered
            if delivered.get("state") != "spec_completed":
                return delivered
            if delivered.get("spec_key") != spec_key:
                raise RunnerError("production_spec_mismatch", "completed delivery belongs to another SPEC")
            self.record_spec(spec_key=spec_key, plan_digest=str(spec_plan.get("digest", "")))
            completed.add(spec_key)
            run = store.find_by_run_id(run.run_id)
            if run is None:
                raise RunnerError("run_missing", "production queue run disappeared during continuation")
        stopped = self._control_boundary()
        if stopped is not None:
            return stopped
        store.mark_archived(run.run_id, state="completed")
        return {"state": "completed", **store.public_status(run.run_id)}

    def _resume_takeover_delivery(
        self, *, spec: dict[str, object], spec_plan: dict[str, object],
    ) -> dict[str, object] | None:
        """Reconcile a discovered candidate frontier before new implementation."""
        source = spec.get("takeover_source")
        if not isinstance(source, dict):
            return None
        frontier = source.get("frontier")
        if not isinstance(frontier, dict) or str(frontier.get("state")) not in {
            "candidate_ready", "checks_pending", "review_pending", "merge_pending", "closure_pending",
        }:
            return None
        candidate = source.get("candidate_receipt")
        review = source.get("review_receipt")
        checks = source.get("checks")
        pull_request = source.get("pull_request")
        branch = source.get("branch")
        candidate_sha = source.get("candidate_sha")
        if (not isinstance(candidate, dict) or candidate.get("outcome") != "verified"
                or not isinstance(candidate_sha, str) or candidate.get("candidate_sha") != candidate_sha):
            raise RunnerError("takeover_candidate_evidence_missing", f"SPEC {spec.get('key')} has no verified candidate receipt")
        if not isinstance(review, dict):
            if self.ports.execute_review is None:
                raise RunnerError("takeover_review_evidence_missing", f"SPEC {spec.get('key')} has no independent review receipt")
            ticket_path = self._artifact() / f"ticket-plan-{spec['key']}.json"
            workspace = source.get("workspace")
            if not ticket_path.is_file() or not isinstance(workspace, dict):
                raise RunnerError("takeover_review_evidence_missing", f"SPEC {spec.get('key')} has no reviewable candidate workspace")
            review = self.ports.execute_review(
                control_root=self.control_root,
                config=self.config,
                run=self.run,
                spec_key=str(spec["key"]),
                candidate_sha=candidate_sha,
                candidate_receipt=candidate,
                workspace=workspace,
                ticket_plan=self.ports.load_json(ticket_path),
                store=self.store,
            )
            if review.get("state") in {"paused", "cancelled"}:
                return review
        if review.get("approved") is not True or review.get("candidate_sha") != candidate_sha:
            raise RunnerError("takeover_review_evidence_missing", f"SPEC {spec.get('key')} has no independent review receipt")
        if not isinstance(branch, str) or not branch:
            raise RunnerError("takeover_branch_identity_missing", f"SPEC {spec.get('key')} has no candidate branch")
        if isinstance(pull_request, dict) and pull_request.get("head_sha") not in {None, candidate_sha}:
            raise RunnerError("takeover_pull_request_mismatch", f"SPEC {spec.get('key')} PR does not match its candidate SHA")
        if isinstance(checks, dict) and checks.get("candidate_sha") != candidate_sha:
            raise RunnerError("takeover_checks_evidence_missing", f"SPEC {spec.get('key')} has no checks readback")
        if self.ports.execute_github_delivery is None:
            raise RunnerError("github_runtime_missing", "takeover delivery reconciliation is not configured")
        result = self.ports.execute_github_delivery(
            control_root=self.control_root,
            config=self.config,
            run=self.run,
            spec_key=str(spec["key"]),
            candidate_sha=candidate_sha,
            branch=branch,
            candidate_receipt=candidate,
            review=review,
            store=self.store,
            push=False,
        )
        if result.get("state") != "github_completed":
            return result
        merge = result.get("merge")
        if not isinstance(merge, dict) or merge.get("merged") is not True:
            raise RunnerError("github_merge_unconfirmed", "takeover delivery has no confirmed merge")
        prior_delivery = source.get("delivery")
        cleanup = prior_delivery.get("cleanup") if isinstance(prior_delivery, dict) else None
        if not isinstance(cleanup, dict) or cleanup.get("outcome") != "cleaned":
            workspace = source.get("workspace")
            if not isinstance(workspace, dict):
                raise RunnerError("takeover_cleanup_evidence_missing", f"SPEC {spec.get('key')} has no verified cleanup receipt")
            workspace_path = Path(str(workspace.get("workspace") or "")).resolve()
            manifest_path = Path(str(workspace.get("manifest") or "")).resolve()
            workspace_root = (self.control_root / "delivery-workspaces").resolve()
            if (
                not workspace_path.is_relative_to(workspace_root)
                or not manifest_path.is_relative_to(workspace_root)
                or workspace.get("repository") != os.fspath(self.config.repository_path.resolve())
            ):
                raise RunnerError("takeover_cleanup_scope_invalid", f"SPEC {spec.get('key')} cleanup workspace is outside the managed root")
            cleanup = self.ports.cleanup_workspace(
                repository=self.config.repository_path,
                workspace_root=workspace_root,
                workspace=workspace_path,
                manifest=manifest_path,
                preserve_manifest=True,
            )
            if cleanup.get("outcome") != "cleaned":
                return {"state": "cleanup_pending", "spec_key": spec.get("key"), "cleanup": cleanup}
        result["cleanup"] = cleanup
        self.persist_delivery_evidence(spec_key=str(spec["key"]), delivery=result)
        ticket_path = self._artifact() / f"ticket-plan-{spec['key']}.json"
        ticket_plan = validate_ticket_plan(self.ports.load_json(ticket_path), expected_spec_key=str(spec["key"]))
        result["issue_closure"] = self.ports.close_ticket_plan(
            config=self.config, plan=ticket_plan, run_id=self.run.run_id, store=self.store,
            delivery_evidence=result,
        )
        self.persist_delivery_evidence(spec_key=str(spec["key"]), delivery=result)
        if not isinstance(result.get("issue_closure"), dict) or result["issue_closure"].get("complete") is not True:
            raise RunnerError("github_close_unconfirmed", f"SPEC {spec.get('key')} Issue closure was not confirmed")
        self.record_spec(spec_key=str(spec["key"]), plan_digest=str(spec_plan.get("digest") or ""))
        result["state"] = "spec_completed"
        return result

    def resume_waiting_github(self, *, finalize_run: bool = True) -> dict[str, object]:
        """Reconcile one persisted CI wait, then finish delivery and cleanup."""
        run, store = self.run, self.store
        stopped = self._control_boundary()
        if stopped is not None:
            return stopped
        artifact = self._artifact()
        github_files = sorted(artifact.glob("github-*.json"))
        manifests = []
        for path in (self.control_root / "delivery-workspaces").glob("*.manifest.json"):
            try:
                document = self.ports.load_json(path)
            except RunnerError:
                continue
            if document.get("run_id") == run.run_id:
                manifests.append((path, document))
        completed_specs = self.completed_specs()
        waiting = [
            document
            for path in github_files
            for document in [self.ports.load_json(path)]
            if document.get("state") in {"waiting_ci", "waiting_merge_queue"}
            and document.get("spec_key") not in completed_specs
        ]
        if len(waiting) != 1:
            raise RunnerError("github_waiting_evidence_missing", "waiting GitHub run needs one unambiguous SPEC delivery receipt")
        github = waiting[0]
        spec_key = github.get("spec_key")
        if not isinstance(spec_key, str) or not spec_key:
            raise RunnerError("github_waiting_evidence_missing", "waiting GitHub receipt has no SPEC identity")
        candidate_path = artifact / f"candidate-{spec_key}.json"
        if not candidate_path.is_file():
            raise RunnerError("github_waiting_evidence_missing", "waiting GitHub run has no candidate receipt for its SPEC")
        candidate = self.ports.load_json(candidate_path)
        candidate_sha = candidate.get("candidate_sha")
        github_candidate = github.get("candidate")
        if (
            candidate.get("outcome") != "verified"
            or not isinstance(candidate_sha, str)
            or len(candidate_sha) != 40
            or not isinstance(github_candidate, dict)
            or github_candidate.get("candidate_sha") != candidate_sha
        ):
            raise RunnerError("github_waiting_evidence_invalid", "waiting GitHub receipt does not match its verified candidate")
        review_path = artifact / f"review-{spec_key}-{candidate_sha[:12]}.json"
        if not review_path.is_file():
            raise RunnerError("github_waiting_evidence_missing", "waiting GitHub run has no validated review for its candidate")
        review = self.ports.load_json(review_path)
        github_review = github.get("review")
        review_projection = {
            key: review.get(key)
            for key in ("approved", "blocking", "candidate_sha", "findings", "review_digest")
        }
        legacy_review_receipt = isinstance(github_review, dict) and github_review == review
        if (
            review.get("approved") is not True
            or review.get("candidate_sha") != candidate_sha
            or not isinstance(review.get("review_digest"), str)
            or not review["review_digest"].strip()
            or not isinstance(github_review, dict)
            or (github_review != review_projection and not legacy_review_receipt)
        ):
            raise RunnerError("github_waiting_evidence_invalid", "waiting GitHub receipt does not match its validated independent review")
        github_branch = github.get("branch")
        matching_manifests = [
            item for item in manifests
            if item[1].get("spec_key") == spec_key
            and (github_branch is None or item[1].get("branch") == github_branch)
        ]
        if len(matching_manifests) != 1:
            raise RunnerError("github_waiting_evidence_missing", "waiting GitHub run needs one workspace manifest for its SPEC")
        manifest_path, manifest = matching_manifests[0]
        stopped = self._control_boundary()
        if stopped is not None:
            return stopped
        result = self.ports.execute_github_delivery(
            control_root=self.control_root,
            config=self.config,
            run=run,
            spec_key=spec_key,
            candidate_sha=candidate_sha,
            branch=str(manifest["branch"]),
            candidate_receipt=candidate,
            review=review_projection,
            push=False,
            queue_entry=(github.get("merge", {}).get("queue")
                         if isinstance(github.get("merge"), dict)
                         and isinstance(github.get("merge", {}).get("queue"), dict)
                         else None),
        )
        if self.ports.definitive_failed_checks(checks=result.get("checks"), candidate_sha=candidate_sha):
            result = self.ports.recover_github_candidate(
                control_root=self.control_root,
                config=self.config,
                run=run,
                store=store,
                spec_key=spec_key,
                candidate=candidate,
                review=review_projection,
                github={**github, **result},
                failed_checks=result["checks"],
                manifest_path=manifest_path,
                manifest=manifest,
            )
            recovery_manifest = result.pop("_workspace_manifest", None)
            if not isinstance(recovery_manifest, str) or not recovery_manifest:
                raise RunnerError("github_recovery_evidence_invalid", "recovered delivery has no cleanup manifest")
            manifest_path = Path(recovery_manifest)
            manifest = self.ports.load_json(manifest_path)
            manifests.append((manifest_path, manifest))
        if result["state"] != "github_completed":
            if result.get("state") in {"waiting_ci", "waiting_merge_queue"}:
                self.ports.write_json_atomic(artifact / f"github-{spec_key}.json", result)
                store.set_run_state(run.run_id, str(result["state"]))
            return result
        merge = result.get("merge")
        if not isinstance(merge, dict) or merge.get("merged") is not True:
            raise RunnerError("github_merge_unconfirmed", "GitHub delivery cannot complete without a confirmed merge receipt")
        self.persist_delivery_evidence(spec_key=str(result["spec_key"]), delivery=result)
        spec_manifests = [item for item in manifests if item[1].get("spec_key") == spec_key]
        cleanups = [
            self.ports.cleanup_workspace(
                repository=self.config.repository_path,
                workspace_root=self.control_root / "delivery-workspaces",
                workspace=Path(str(document["workspace"])),
                manifest=path,
                preserve_manifest=True,
            )
            for path, document in spec_manifests
        ]
        cleanup = cleanups[0] if len(cleanups) == 1 else {
            "outcome": "cleaned" if cleanups and all(item.get("outcome") == "cleaned" for item in cleanups) else "pending",
            "workspaces": cleanups,
        }
        result["cleanup"] = cleanup
        if cleanup["outcome"] != "cleaned":
            store.mark_cleanup_pending(run.run_id)
            result["state"] = "cleanup_pending"
        else:
            ticket_path = artifact / f"ticket-plan-{spec_key}.json"
            if not ticket_path.is_file():
                raise RunnerError("ticket_plan_missing", f"SPEC {spec_key} has no persisted TicketPlan")
            ticket_plan = self.ports.load_json(ticket_path)
            self.persist_delivery_evidence(spec_key=spec_key, delivery=result)
            try:
                result["issue_closure"] = self.ports.close_ticket_plan(
                    config=self.config, plan=ticket_plan, run_id=run.run_id, store=store,
                )
            except RunnerError as exc:
                result["issue_closure"] = {"state": "pending", "error_code": exc.code}
                self.persist_delivery_evidence(spec_key=spec_key, delivery=result)
                store.mark_cleanup_pending(run.run_id)
                result["state"] = "cleanup_pending"
                return result
            self.persist_delivery_evidence(spec_key=spec_key, delivery=result)
            finalized_cleanups = [
                self.ports.cleanup_workspace(
                    repository=self.config.repository_path,
                    workspace_root=self.control_root / "delivery-workspaces",
                    workspace=Path(str(document["workspace"])),
                    manifest=path,
                )
                for path, document in spec_manifests
            ]
            if any(item.get("outcome") != "cleaned" for item in finalized_cleanups):
                store.mark_cleanup_pending(run.run_id)
                result["manifest_cleanup"] = finalized_cleanups
                result["state"] = "cleanup_pending"
                return result
            if finalize_run:
                store.mark_archived(run.run_id, state="completed")
            else:
                plan = self.ports.load_json(artifact / "spec-plan.json")
                self.record_spec(spec_key=str(result["spec_key"]), plan_digest=str(plan.get("digest", "")))
                store.set_run_state(run.run_id, "spec_completed")
        if not finalize_run and result.get("state") == "github_completed":
            result["state"] = "spec_completed"
        return result

    def retry_cleanup(self) -> dict[str, object]:
        """Replay only cleanup and issue closure after a production exit."""
        if self.config.github_repository is None:
            return self._retry_local_cleanup()
        run, store = self.run, self.store
        root = self.control_root / "delivery-workspaces"
        manifests: list[Path] = []
        for manifest in root.glob("*.manifest.json"):
            try:
                document = self.ports.load_json(manifest)
            except RunnerError:
                continue
            if document.get("run_id") == run.run_id:
                manifests.append(manifest)
        artifact = self._artifact()
        plan_path = artifact / "spec-plan.json"
        if not plan_path.is_file():
            raise RunnerError("production_cleanup_evidence_missing", "cleanup retry requires persisted delivery, plan and ticket evidence")
        plan = self.ports.load_json(plan_path)
        manifest_documents = [(manifest, self.ports.load_json(manifest)) for manifest in manifests]
        manifest_spec_keys = [document.get("spec_key") for _, document in manifest_documents]
        if any(not isinstance(spec_key, str) or not spec_key for spec_key in manifest_spec_keys):
            raise RunnerError("production_cleanup_evidence_missing", "workspace manifest has no SPEC identity")
        spec_keys = set(manifest_spec_keys)
        for receipt_path in artifact.glob("delivery-*.json"):
            receipt = self.ports.load_json(receipt_path)
            if receipt.get("run_id") == run.run_id and isinstance(receipt.get("spec_key"), str):
                spec_keys.add(str(receipt["spec_key"]))
        if not spec_keys:
            raise RunnerError("production_cleanup_evidence_missing", "cleanup_pending run has no delivery or workspace evidence")
        if not manifests and self.config.github_repository is None:
            raise RunnerError("production_cleanup_evidence_missing", "cleanup_pending run has no owned workspace manifest")
        for spec_key in spec_keys:
            receipt_path = artifact / f"delivery-{spec_key}.json"
            ticket_path = artifact / f"ticket-plan-{spec_key}.json"
            if not receipt_path.is_file() or not ticket_path.is_file():
                raise RunnerError("production_cleanup_evidence_missing", "cleanup retry requires persisted delivery, plan and ticket evidence")
            receipt, ticket = self.ports.load_json(receipt_path), self.ports.load_json(ticket_path)
            if (
                receipt.get("spec_key") != spec_key
                or receipt.get("run_id") != run.run_id
                or receipt.get("plan_digest") != plan.get("digest")
                or receipt.get("ticket_plan_digest") != ticket.get("digest")
                or not isinstance(receipt.get("candidate"), dict)
                or not isinstance(receipt.get("review"), dict)
                or receipt.get("review", {}).get("approved") is not True
                or not isinstance(receipt.get("merge"), dict)
                or receipt.get("merge", {}).get("merged") is not True
            ):
                raise RunnerError("production_cleanup_evidence_invalid", "persisted delivery evidence does not prove this SPEC was reviewed and merged")
            if spec_key not in set(manifest_spec_keys) and self.config.github_repository is not None:
                if (
                    not isinstance(receipt.get("cleanup"), dict)
                    or receipt["cleanup"].get("outcome") != "cleaned"
                    or not isinstance(receipt.get("issue_closure"), dict)
                    or receipt["issue_closure"].get("complete") is not True
                ):
                    raise RunnerError("production_cleanup_evidence_invalid", "manifest-free SPEC lacks durable cleanup and issue closure readbacks")
        results = [
            self.ports.cleanup_workspace(
                repository=self.config.repository_path,
                workspace_root=root,
                workspace=Path(str(document["workspace"])),
                manifest=manifest,
                preserve_manifest=True,
            )
            for manifest, document in manifest_documents
        ]
        if any(item.get("outcome") != "cleaned" for item in results):
            return {"state": "cleanup_pending", "cleanup": results}
        closures = []
        closures_by_spec: dict[str, dict[str, object] | None] = {}
        for spec_key in sorted(spec_keys):
            ticket_plan = self.ports.load_json(artifact / f"ticket-plan-{spec_key}.json")
            try:
                closure = self.ports.close_ticket_plan(
                    config=self.config, plan=ticket_plan, run_id=run.run_id, store=store,
                )
                closures.append(closure)
                closures_by_spec[spec_key] = closure
            except RunnerError as exc:
                return {
                    "state": "cleanup_pending",
                    "cleanup": results,
                    "issue_closure": {"spec_key": spec_key, "error_code": exc.code},
                }
        finalized = [
            self.ports.cleanup_workspace(
                repository=self.config.repository_path,
                workspace_root=root,
                workspace=Path(str(document["workspace"])),
                manifest=manifest,
            )
            for manifest, document in manifest_documents
        ]
        if any(item.get("outcome") != "cleaned" for item in finalized):
            return {"state": "cleanup_pending", "cleanup": results, "manifest_cleanup": finalized}
        for spec_key, closure in closures_by_spec.items():
            delivery_path = artifact / f"delivery-{spec_key}.json"
            delivery = self.ports.load_json(delivery_path)
            delivery["issue_closure"] = closure
            if spec_key in set(manifest_spec_keys):
                delivery["cleanup"] = {"outcome": "cleaned", "recovered": True}
            self.ports.write_json_atomic(delivery_path, delivery)
        for spec_key in sorted(spec_keys):
            self.record_spec(spec_key=str(spec_key), plan_digest=str(plan["digest"]))
        store.set_run_state(run.run_id, "spec_completed")
        completed: dict[str, object] = {"state": "spec_completed", "cleanup": results, "issue_closures": closures}
        if len(spec_keys) == 1:
            completed["spec_key"] = next(iter(spec_keys))
        else:
            completed["spec_keys"] = sorted(spec_keys)
        return completed

    def _retry_local_cleanup(self) -> dict[str, object]:
        run, store = self.run, self.store
        artifact = self._artifact()
        plan_path = artifact / "spec-plan.json"
        if not plan_path.is_file():
            raise RunnerError("production_cleanup_evidence_missing", "cleanup retry requires persisted delivery, plan and ticket evidence")
        plan = self.ports.load_json(plan_path)
        paths = sorted(artifact.glob("delivery-*.json"))
        if not paths or self.ports.reconcile_local_delivery is None:
            raise RunnerError("production_cleanup_evidence_missing", "local cleanup has no durable delivery evidence")
        known = {str(spec["key"]) for spec in plan["specs"]}
        reconciled: dict[str, dict[str, object]] = {}
        for path in paths:
            receipt = self.ports.load_json(path)
            spec_key = receipt.get("spec_key")
            if receipt.get("run_id") != run.run_id or spec_key not in known:
                raise RunnerError("production_cleanup_evidence_invalid", "local cleanup delivery belongs to another plan")
            if not (artifact / f"ticket-plan-{spec_key}.json").is_file():
                raise RunnerError("production_cleanup_evidence_missing", "cleanup retry requires persisted delivery, plan and ticket evidence")
            try:
                reconciled[str(spec_key)] = self.ports.reconcile_local_delivery(
                    control_root=self.control_root, config=self.config, run=run,
                    store=store, spec_key=str(spec_key),
                )
            except RunnerError as exc:
                if exc.code != "production_cleanup_pending":
                    raise
                store.mark_cleanup_pending(run.run_id)
                return {"state": "cleanup_pending", "spec_key": str(spec_key),
                        "cleanup": {"outcome": "pending", "error_code": exc.code}}
        completed = self.completed_specs()
        closures = []
        for spec_key, receipt in reconciled.items():
            try:
                closure = self.ports.close_ticket_plan(config=self.config,
                    plan=self.ports.load_json(artifact / f"ticket-plan-{spec_key}.json"),
                    run_id=run.run_id, store=store, delivery_evidence=receipt)
            except RunnerError as exc:
                store.mark_cleanup_pending(run.run_id)
                return {"state": "cleanup_pending", "issue_closure": {"spec_key": spec_key, "error_code": exc.code}}
            closures.append(closure)
            if spec_key not in completed:
                self.ports.write_json_atomic(artifact / f"delivery-{spec_key}.json", {**receipt, "issue_closure": closure})
                self.record_spec(spec_key=spec_key, plan_digest=str(plan["digest"]))
        store.set_run_state(run.run_id, "spec_completed")
        return {"state": "spec_completed", "spec_keys": sorted(reconciled), "issue_closures": closures}
