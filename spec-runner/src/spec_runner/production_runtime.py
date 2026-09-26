"""Production delivery orchestration behind the public Runner seam.

This module owns queue selection and cleanup replay.  Git, GitHub, Store and
worker implementations remain adapters supplied by ``workflow`` so this
module only coordinates their existing contracts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import RunnerConfig
from .errors import RunnerError
from .store import RunRecord, Store


JsonLoader = Callable[[Path], dict[str, object]]
JsonWriter = Callable[[Path, dict[str, object]], None]
ArtifactDirectory = Callable[[Path, RunnerConfig, str], Path]
TicketRunner = Callable[..., RunRecord]
ImplementationRunner = Callable[..., dict[str, object]]
CleanupRunner = Callable[..., dict[str, object]]
TicketCloser = Callable[..., dict[str, object]]
GitHubDeliveryRunner = Callable[..., dict[str, object]]
GitHubRecoveryRunner = Callable[..., dict[str, object]]
FailedChecks = Callable[..., bool]


@dataclass(frozen=True)
class ProductionWorkflow:
    """Drive production SPECs and replay their durable cleanup evidence."""

    control_root: Path
    config: RunnerConfig
    brief_digest: str
    run: RunRecord | None
    store: Store | None
    artifact_directory: ArtifactDirectory
    load_json: JsonLoader
    write_json_atomic: JsonWriter
    execute_tickets: TicketRunner
    execute_implementation: ImplementationRunner
    cleanup_workspace: CleanupRunner
    close_ticket_plan: TicketCloser
    execute_github_delivery: GitHubDeliveryRunner | None = None
    recover_github_candidate: GitHubRecoveryRunner | None = None
    definitive_failed_checks: FailedChecks | None = None

    def _artifact(self, run_id: str | None = None) -> Path:
        target_run_id = run_id or (self.run.run_id if self.run is not None else None)
        if target_run_id is None:
            raise RunnerError("run_missing", "production workflow requires its durable run")
        return self.artifact_directory(self.control_root, self.config, target_run_id)

    def _durable(self) -> tuple[RunRecord, Store]:
        if self.run is None or self.store is None:
            raise RunnerError("run_missing", "production workflow requires its durable run and Store")
        return self.run, self.store

    def completed_specs(self, run_id: str | None = None) -> set[str]:
        """Read Store completion receipts; validate the JSON projection only."""
        target_run_id = run_id or (self.run.run_id if self.run is not None else None)
        if target_run_id is None:
            raise RunnerError("run_missing", "production workflow requires its durable run")
        _, store = self._durable()
        persisted = store.production_completed_specs(target_run_id)
        path = self._artifact(target_run_id) / "completed-specs.json"
        if not path.exists():
            return persisted
        document = self.load_json(path)
        values = document.get("specs", [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise RunnerError("completed_specs_corrupt", "completed SPEC record is invalid")
        # The file is a human-readable projection. Only transactional Store
        # receipts may advance the production queue.
        return persisted

    def record_spec(self, *, spec_key: str, plan_digest: str, run_id: str | None = None) -> None:
        target_run_id = run_id or (self.run.run_id if self.run is not None else None)
        if target_run_id is None:
            raise RunnerError("run_missing", "production workflow requires its durable run")
        _, store = self._durable()
        directory = self._artifact(target_run_id)
        completed = self.completed_specs(target_run_id)
        completed.add(spec_key)
        receipt = self.load_json(directory / f"delivery-{spec_key}.json")
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

    def persist_delivery_evidence(self, *, spec_key: str, delivery: dict[str, object], run_id: str | None = None) -> None:
        target_run_id = run_id or (self.run.run_id if self.run is not None else None)
        if target_run_id is None:
            raise RunnerError("run_missing", "production workflow requires its durable run")
        artifact = self._artifact(target_run_id)
        plan = self.load_json(artifact / "spec-plan.json")
        ticket = self.load_json(artifact / f"ticket-plan-{spec_key}.json")
        record = {
            "schema_version": "spec-runner-production-delivery/v1",
            "run_id": target_run_id,
            "spec_key": spec_key,
            "plan_digest": plan.get("digest"),
            "ticket_plan_digest": ticket.get("digest"),
            **delivery,
        }
        self.write_json_atomic(artifact / f"delivery-{spec_key}.json", record)

    def run_queue(self, spec_plan: dict[str, object]) -> dict[str, object]:
        """Select and complete dependency-ready SPECs in plan order."""
        run, store = self._durable()
        if (
            self.execute_github_delivery is None
            or self.recover_github_candidate is None
            or self.definitive_failed_checks is None
        ):
            raise RunnerError("github_runtime_missing", "production GitHub recovery adapters are not configured")
        specs = spec_plan.get("specs")
        if not isinstance(specs, list) or any(not isinstance(item, dict) for item in specs):
            raise RunnerError("invalid_spec_plan", "production queue requires keyed SPEC objects")
        completed = self.completed_specs()
        while len(completed) < len(specs):
            ready = [
                item for item in specs
                if str(item.get("key")) not in completed
                and set(item.get("blocked_by", [])) <= completed
            ]
            if not ready:
                raise RunnerError("production_queue_blocked", "no dependency-ready SPEC remains")
            spec = ready[0]
            spec_key = str(spec["key"])
            ticket_files = sorted(self._artifact(run.run_id).glob(f"ticket-plan-{spec_key}.json"))
            if ticket_files and run.state == "tickets_ready":
                ticketed = run
            else:
                ticketed = self.execute_tickets(
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
            ticket_files = sorted(self._artifact(run.run_id).glob(f"ticket-plan-{spec_key}.json"))
            if not ticket_files:
                raise RunnerError("ticket_plan_missing", f"SPEC {spec_key} has no persisted TicketPlan")
            delivered = self.execute_implementation(
                control_root=self.control_root,
                config=self.config,
                brief_digest=self.brief_digest,
                run=ticketed,
                store=store,
                ticket_plan=self.load_json(ticket_files[-1]),
                finalize_run=False,
            )
            if delivered.get("state") in {"waiting_ci", "waiting_merge_queue", "cleanup_pending"}:
                return delivered
            if delivered.get("state") == "spec_completed" and str(delivered.get("spec_key")) == spec_key:
                self.record_spec(spec_key=spec_key, plan_digest=str(spec_plan.get("digest", "")))
                completed.add(spec_key)
                run = store.find_by_run_id(run.run_id)
                if run is None:
                    raise RunnerError("run_missing", "production queue run disappeared during continuation")
                continue
            if delivered.get("state") != "spec_completed":
                return delivered
            self.record_spec(spec_key=spec_key, plan_digest=str(spec_plan.get("digest", "")))
            completed.add(spec_key)
            run = store.find_by_run_id(run.run_id)
            if run is None:
                raise RunnerError("run_missing", "production queue run disappeared during continuation")
        store.mark_archived(run.run_id, state="completed")
        return {"state": "completed", **store.public_status(run.run_id)}

    def resume_waiting_github(self, *, finalize_run: bool = True) -> dict[str, object]:
        """Reconcile one persisted CI wait, then finish delivery and cleanup."""
        run, store = self._durable()
        artifact = self._artifact()
        github_files = sorted(artifact.glob("github-*.json"))
        manifests = []
        for path in (self.control_root / "delivery-workspaces").glob("*.manifest.json"):
            try:
                document = self.load_json(path)
            except RunnerError:
                continue
            if document.get("run_id") == run.run_id:
                manifests.append((path, document))
        completed_specs = self.completed_specs()
        waiting = [
            document
            for path in github_files
            for document in [self.load_json(path)]
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
        candidate = self.load_json(candidate_path)
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
        review = self.load_json(review_path)
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
        result = self.execute_github_delivery(
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
        if self.definitive_failed_checks(checks=result.get("checks"), candidate_sha=candidate_sha):
            result = self.recover_github_candidate(
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
            manifest = self.load_json(manifest_path)
            manifests.append((manifest_path, manifest))
        if result["state"] != "github_completed":
            if result.get("state") in {"waiting_ci", "waiting_merge_queue"}:
                self.write_json_atomic(artifact / f"github-{spec_key}.json", result)
                store.set_run_state(run.run_id, str(result["state"]))
            return result
        merge = result.get("merge")
        if not isinstance(merge, dict) or merge.get("merged") is not True:
            raise RunnerError("github_merge_unconfirmed", "GitHub delivery cannot complete without a confirmed merge receipt")
        self.persist_delivery_evidence(
            spec_key=str(result["spec_key"]), delivery=result,
        )
        spec_manifests = [item for item in manifests if item[1].get("spec_key") == spec_key]
        cleanups = [
            self.cleanup_workspace(
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
            ticket_plan = self.load_json(ticket_path)
            self.persist_delivery_evidence(spec_key=spec_key, delivery=result)
            try:
                result["issue_closure"] = self.close_ticket_plan(
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
                self.cleanup_workspace(
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
                plan = self.load_json(artifact / "spec-plan.json")
                self.record_spec(spec_key=str(result["spec_key"]), plan_digest=str(plan.get("digest", "")))
                store.set_run_state(run.run_id, "spec_completed")
        if not finalize_run and result.get("state") == "github_completed":
            result["state"] = "spec_completed"
        return result

    def retry_cleanup(self) -> dict[str, object]:
        """Replay only cleanup and issue closure after a production exit."""
        run, store = self._durable()
        root = self.control_root / "delivery-workspaces"
        manifests: list[Path] = []
        for manifest in root.glob("*.manifest.json"):
            try:
                document = self.load_json(manifest)
            except RunnerError:
                continue
            if document.get("run_id") == run.run_id:
                manifests.append(manifest)
        artifact = self._artifact()
        plan_path = artifact / "spec-plan.json"
        if not plan_path.is_file():
            raise RunnerError("production_cleanup_evidence_missing", "cleanup retry requires persisted delivery, plan and ticket evidence")
        plan = self.load_json(plan_path)
        manifest_documents = [(manifest, self.load_json(manifest)) for manifest in manifests]
        manifest_spec_keys = [document.get("spec_key") for _, document in manifest_documents]
        if any(not isinstance(spec_key, str) or not spec_key for spec_key in manifest_spec_keys):
            raise RunnerError("production_cleanup_evidence_missing", "workspace manifest has no SPEC identity")
        spec_keys = set(manifest_spec_keys)
        for receipt_path in artifact.glob("delivery-*.json"):
            receipt = self.load_json(receipt_path)
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
            receipt, ticket = self.load_json(receipt_path), self.load_json(ticket_path)
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
            self.cleanup_workspace(
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
            ticket_plan = self.load_json(artifact / f"ticket-plan-{spec_key}.json")
            try:
                closure = self.close_ticket_plan(
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
            self.cleanup_workspace(
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
            delivery = self.load_json(delivery_path)
            delivery["issue_closure"] = closure
            if spec_key in set(manifest_spec_keys):
                delivery["cleanup"] = {"outcome": "cleaned", "recovered": True}
            self.write_json_atomic(delivery_path, delivery)
        for spec_key in sorted(spec_keys):
            self.record_spec(spec_key=str(spec_key), plan_digest=str(plan["digest"]))
        store.set_run_state(run.run_id, "spec_completed")
        completed: dict[str, object] = {"state": "spec_completed", "cleanup": results, "issue_closures": closures}
        if len(spec_keys) == 1:
            completed["spec_key"] = next(iter(spec_keys))
        else:
            completed["spec_keys"] = sorted(spec_keys)
        return completed
