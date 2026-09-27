"""Production delivery orchestration behind the public Runner seam.

This module owns queue selection and cleanup replay.  Git, GitHub, Store and
worker implementations remain adapters supplied by ``workflow`` so this
module only coordinates their existing contracts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Mapping

from .config import RunnerConfig
from .errors import RunnerError
from .models import RunContext
from .plans import validate_spec_plan
from .recovery import RecoveryAction
from .recovery_runtime import RecoveryEpisode
from .store import RunRecord, Store


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
    definitive_failed_checks: FailedChecks | None = None
    reconcile_local_delivery: Callable[..., dict[str, object]] | None = None


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

    def plan(self, *, thread_id: str | None = None) -> RunRecord:
        """Run the production planning stage through the production seam.

        The compatibility workflow supplies the SDK adapter as a port.  This
        keeps the lifecycle entry focused on leases and recovery while this
        module owns the transition from a production brief to a persisted
        SpecPlan.
        """
        run, store = self.run, self.store
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
                if spec_key in completed or not receipt_path.is_file():
                    continue
                if self.ports.reconcile_local_delivery is None:
                    raise RunnerError("production_recovery_missing", "local delivery reconciliation is not configured")
                receipt = self.ports.reconcile_local_delivery(
                    control_root=self.control_root, config=self.config, run=run,
                    store=store, spec_key=spec_key,
                )
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
            ticket_files = sorted(self._artifact().glob(f"ticket-plan-{spec_key}.json"))
            if ticket_files and run.state == "tickets_ready":
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
            if ticketed.state != "tickets_ready":
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
