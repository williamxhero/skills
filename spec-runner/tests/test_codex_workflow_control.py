from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from spec_runner import workflow
from spec_runner.codex_adapter import CodexWorkerResult
from spec_runner.config import RunnerConfig, read_brief
from spec_runner.errors import RunnerError
from spec_runner.store import RunRecord, Store, now


class FakeResumableAdapter:
    calls = 0
    resumed_thread_ids: list[str | None] = []

    def run(self, *, prompt: str, repository_path: Path, model: str, effort: str, thread_id: str | None = None, **kwargs: object) -> CodexWorkerResult:
        type(self).resumed_thread_ids.append(thread_id)
        type(self).calls += 1
        run_id = prompt.split("spec-runner-output/", 1)[1].split("/", 1)[0]
        stage = "second" if "second bounded" in prompt else "first"
        if type(self).calls == 1:
            return CodexWorkerResult(
                thread_id="thread-1",
                turn_id="turn-interrupted",
                status="interrupted",
                error=None,
                final_response=json.dumps({"outcome": "interrupted", "artifacts": [], "blockers": []}),
                item_count=0,
                started_at=1,
                completed_at=2,
            )
        output = repository_path / "spec-runner-output" / run_id
        output.mkdir(parents=True, exist_ok=True)
        artifact = output / ("handoff.md" if stage == "first" else "final.md")
        artifact.write_text(stage + "\n", encoding="utf-8")
        return CodexWorkerResult(
            thread_id="thread-1",
            turn_id=f"turn-{type(self).calls}",
            status="completed",
            error=None,
            final_response=json.dumps(
                {
                    "outcome": "completed",
                    "artifacts": [f"spec-runner-output/{run_id}/{artifact.name}"],
                    "blockers": [],
                }
            ),
            item_count=1,
            started_at=1,
            completed_at=2,
        )

    def archive_and_readback(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
        return {"thread_id": thread_id, "archived": True, "pages_read": 1}


class CodexWorkflowControlTests(unittest.TestCase):
    def test_planning_prompt_carries_durable_takeover_frontier_as_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spec-runner-takeover-prompt-") as temporary:
            root = Path(temporary)
            repository = root / "repo"
            repository.mkdir()
            subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
            config = RunnerConfig(
                repository, "HEAD", Path("artifacts"), "codex_sdk", ("planning",),
                "fake", "high", (Path("artifacts"),), None, None, (), "test", "config",
            )
            run_id = "22222222-2222-2222-2222-222222222222"
            timestamp = now()
            run = RunRecord(
                run_id=run_id, launch_key="takeover-prompt", input_digest="brief",
                config_digest="config", repository_path=str(repository), target_ref="HEAD",
                artifact_root="artifacts", backend_kind="codex_sdk", state="starting",
                current_step="codex_planning", log_path="logs/run.jsonl",
                created_at=timestamp, updated_at=timestamp,
            )
            store = Store.open(root / "control", create=True)
            try:
                store.create_run(run, f"start:{run_id}")
                artifact = root / "control" / "artifacts" / run_id
                artifact.mkdir(parents=True)
                (artifact / "takeover-context.json").write_text(json.dumps({
                    "schema_version": "spec-runner-takeover-context/v1",
                    "run_id": run_id,
                    "takeover_key": "takeover-prompt",
                    "record": {
                        "takeover_key": "takeover-prompt",
                        "report": {"digest": "report-digest"},
                        "frontier": {"state": "planned", "steps": [{"target": "remaining-SF-04"}]},
                    },
                }), encoding="utf-8")
                captured: dict[str, object] = {}

                def capture(**kwargs: object) -> CodexWorkerResult:
                    captured.update(kwargs)
                    return CodexWorkerResult("thread", "turn", "completed", None, "{}", 0, 1, 2)

                with patch.object(workflow, "_run_worker", side_effect=capture), patch.object(
                    workflow, "_planning_response", return_value=run
                ):
                    workflow._execute_codex_planning(
                        control_root=root / "control", config=config, brief="continue", brief_digest="brief",
                        run=run, store=store,
                    )
                prompt = str(captured["prompt"])
                self.assertIn("takeover-prompt", prompt)
                self.assertIn("remaining-SF-04", prompt)
                self.assertIn("evidence only", prompt)
                self.assertIn("reverify", prompt)
            finally:
                store.close()

    def test_production_acceptance_upgrade_preserves_existing_run_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repo"
            repository.mkdir()
            subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
            config_file = root / "runner.json"
            config_file.write_text(json.dumps({
                "schema_version": "spec-runner-config/v1",
                "repository_path": str(repository),
                "target_ref": "HEAD",
                "artifact_root": "artifacts",
                "execution_backend": "codex_sdk",
                "allowed_stages": ["implement"],
                "model": {"name": "fake", "effort": "high"},
                "authorization": {"artifact_roots": ["artifacts"]},
                "workflow": {"mode": "production", "acceptance": {
                    "ids": ["PROJECT_TESTS"],
                    "write_scope": ["fixture-app/run-1"],
                    "checks": [{"command": [sys.executable, "-c", "pass"], "acceptance": ["PROJECT_TESTS"]}],
                }},
            }), encoding="utf-8")
            config = RunnerConfig.from_file(config_file, root / "control")
            self.assertTrue(config.acceptance_ids)
            self.assertNotEqual(config.digest, config.legacy_acceptance_digest)
            existing = RunRecord(
                run_id="44444444-4444-4444-4444-444444444444",
                launch_key="legacy",
                input_digest="brief",
                config_digest=config.legacy_acceptance_digest,
                repository_path=str(repository),
                target_ref="HEAD",
                artifact_root="artifacts",
                backend_kind="codex_sdk",
                state="failed",
                current_step="codex_ticket_planning",
                log_path="logs/run.jsonl",
                created_at=now(),
                updated_at=now(),
            )
            self.assertTrue(workflow._acceptance_upgrade_compatible(existing, config))
            self.assertFalse(workflow._acceptance_upgrade_compatible(existing, replace(config, acceptance_ids=(), acceptance_checks=())))

            original_config = json.loads(config_file.read_text(encoding="utf-8"))
            timed_config = json.loads(json.dumps(original_config))
            timed_config["workflow"]["acceptance"]["checks"][0]["timeout_seconds"] = 300
            timed_config_file = root / "runner-timeout.json"
            timed_config_file.write_text(json.dumps(timed_config), encoding="utf-8")
            timed = RunnerConfig.from_file(timed_config_file, root / "control")
            existing_with_acceptance = replace(existing, config_digest=config.digest)
            self.assertTrue(workflow._acceptance_upgrade_compatible(existing_with_acceptance, timed))

            incompatible_config = json.loads(json.dumps(timed_config))
            incompatible_config["model"]["name"] = "different-model"
            incompatible_file = root / "runner-incompatible.json"
            incompatible_file.write_text(json.dumps(incompatible_config), encoding="utf-8")
            incompatible = RunnerConfig.from_file(incompatible_file, root / "control")
            self.assertFalse(workflow._acceptance_upgrade_compatible(existing_with_acceptance, incompatible))

    def _failed_sdk_run(self, root: Path) -> tuple[RunnerConfig, RunRecord, Store]:
        repository = root / "repo"
        repository.mkdir()
        config = RunnerConfig(
            repository, "HEAD", Path("artifacts"), "codex_sdk", ("production",),
            "fake", "high", (Path("artifacts"),), None, None, (), "production", "config",
        )
        timestamp = now()
        run = RunRecord(
            run_id="33333333-3333-3333-3333-333333333333",
            launch_key="recovery-failed",
            input_digest="brief",
            config_digest="config",
            repository_path=str(repository),
            target_ref="HEAD",
            artifact_root="artifacts",
            backend_kind="codex_sdk",
            state="starting",
            current_step="codex_planning",
            log_path="logs/run.jsonl",
            created_at=timestamp,
            updated_at=timestamp,
        )
        store = Store.open(root / "control", create=True)
        store.create_run(run, f"start:{run.run_id}")
        operation_id = f"planning:{run.run_id}"
        worker_id = f"codex_sdk:{run.run_id}:codex_planning"
        store.begin_stage(
            run.run_id,
            step_name="codex_planning",
            operation_id=operation_id,
            backend_kind="codex_sdk",
            worker_id=worker_id,
        )
        store.record_codex_turn_started(
            run.run_id,
            operation_id,
            thread_id="thread-failed",
            turn_id="turn-failed",
            step_name="codex_planning",
            worker_id=worker_id,
        )
        store.fail_run(run.run_id, operation_id)
        failed = store.find_by_run_id(run.run_id)
        assert failed is not None
        return config, failed, store

    @staticmethod
    def _failed_turn_inspection(**overrides: object) -> dict[str, object]:
        return {
            "schema_version": "spec-runner-sdk-thread-inspection/v1",
            "thread_id": "thread-failed",
            "thread_status": "idle",
            "active_flags": [],
            "started_turn": False,
            "turn_count": 1,
            "turns": [{"turn_id": "turn-failed", "status": "failed"}],
            **overrides,
        }

    def test_recovery_retries_completed_review_with_invalid_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spec-runner-invalid-review-") as temp:
            root = Path(temp)
            repository = root / "repo"
            repository.mkdir()
            subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
            (repository / "README.md").write_text("baseline\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "baseline"],
                check=True,
            )
            base_sha = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
            config = RunnerConfig(
                repository, "HEAD", Path("artifacts"), "codex_sdk", ("production",),
                "fake", "high", (Path("artifacts"),), None, None, (), "production", "config",
                acceptance_checks=({"command": [sys.executable, "-c", "pass"], "acceptance": ["PROJECT_TESTS"]},),
                acceptance_ids=("PROJECT_TESTS",),
            )
            run_id = "12121212-1212-1212-1212-121212121212"
            run = RunRecord(
                run_id=run_id,
                launch_key="invalid-review",
                input_digest="brief",
                config_digest="config",
                repository_path=str(repository),
                target_ref="HEAD",
                artifact_root="artifacts",
                backend_kind="codex_sdk",
                state="starting",
                current_step="codex_review",
                log_path="logs/run.jsonl",
                created_at=now(),
                updated_at=now(),
            )
            store = Store.open(root / "control", create=True)
            try:
                store.create_run(run, f"start:{run_id}")
                workspace_info = workflow.prepare_workspace(
                    repository=repository,
                    workspace_root=root / "control" / "delivery-workspaces",
                    run_id=run_id,
                    spec_key="SPEC-95",
                    base_ref="HEAD",
                )
                workspace = Path(str(workspace_info["workspace"]))
                (workspace / "implemented.txt").write_text("candidate\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(workspace), "add", "implemented.txt"], check=True)
                subprocess.run(
                    ["git", "-C", str(workspace), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "candidate"],
                    check=True,
                )
                candidate_sha = subprocess.check_output(["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True).strip()
                implementation_operation = f"implementation:{run_id}:SPEC-95"
                implementation_worker = f"codex_sdk:{run_id}:codex_implementation:SPEC-95"
                store.begin_stage(
                    run_id,
                    step_name="codex_implementation",
                    operation_id=implementation_operation,
                    backend_kind="codex_sdk",
                    worker_id=implementation_worker,
                )
                store.record_codex_turn_started(
                    run_id,
                    implementation_operation,
                    thread_id="implementation-thread",
                    turn_id="implementation-turn",
                    step_name="codex_implementation",
                    worker_id=implementation_worker,
                )
                store.complete_codex_stage(
                    run_id,
                    implementation_operation,
                    thread_id="implementation-thread",
                    turn_id="implementation-turn",
                    state="verified_candidate",
                    step_name="codex_implementation",
                    worker_id=implementation_worker,
                )
                review_operation = f"review:{run_id}:SPEC-95:{candidate_sha}"
                review_worker = f"codex_sdk:{run_id}:codex_review:SPEC-95:{candidate_sha[:12]}"
                store.begin_stage(
                    run_id,
                    step_name="codex_review",
                    operation_id=review_operation,
                    backend_kind="codex_sdk",
                    worker_id=review_worker,
                )
                store.record_codex_turn_started(
                    run_id,
                    review_operation,
                    thread_id="review-thread",
                    turn_id="review-turn",
                    step_name="codex_review",
                    worker_id=review_worker,
                )
                store.set_run_state(run_id, "blocked")
                artifact = root / "control" / "artifacts" / run_id
                artifact.mkdir(parents=True)
                (artifact / "ticket-plan-SPEC-95.json").write_text(
                    json.dumps({
                        "schema_version": "spec-runner-ticket-plan/v1",
                        "spec_key": "SPEC-95",
                        "base_sha": base_sha,
                        "digest": "ticket-digest",
                        "tickets": [{"key": "SPEC-95.1", "body": "Review the candidate.", "blocked_by": [], "acceptance": ["PROJECT_TESTS"]}],
                    }),
                    encoding="utf-8",
                )
                (artifact / f"candidate-SPEC-95.json").write_text(
                    json.dumps({"candidate_sha": candidate_sha, "acceptance_version": "ticket-digest", "outcome": "verified"}),
                    encoding="utf-8",
                )
                (artifact / f"implementation-SPEC-95.json").write_text(
                    json.dumps({
                        "thread_id": "implementation-thread",
                        "turn_id": "implementation-turn",
                        "status": "completed",
                        "error": None,
                        "final_response": json.dumps({"outcome": "completed", "artifacts": ["implemented.txt"], "blockers": [], "questions": []}),
                        "item_count": 1,
                        "started_at": 1,
                        "completed_at": 2,
                    }),
                    encoding="utf-8",
                )
                (artifact / f"review-worker-SPEC-95-{candidate_sha[:12]}.json").write_text(
                    json.dumps({
                        "thread_id": "review-thread",
                        "turn_id": "review-turn",
                        "status": "completed",
                        "error": None,
                        "final_response": json.dumps({
                            "schema_version": "spec-runner-review-result/v1",
                            "candidate_sha": candidate_sha,
                            "acceptance_version": "truncated",
                            "findings": [],
                        }),
                        "item_count": 1,
                        "started_at": 1,
                        "completed_at": 2,
                    }),
                    encoding="utf-8",
                )

                class ReadOnlyAdapter:
                    def read_thread(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
                        self.read = (thread_id, repository_path)
                        return {
                            "schema_version": "spec-runner-sdk-thread-inspection/v1",
                            "thread_id": "review-thread",
                            "thread_status": "idle",
                            "active_flags": [],
                            "started_turn": False,
                            "turn_count": 1,
                            "turns": [{"turn_id": "review-turn", "status": "completed"}],
                        }

                    def archive_and_readback(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
                        return {"thread_id": thread_id, "archived": True, "pages_read": 1}

                replacement_review = {"candidate_sha": candidate_sha, "approved": True, "blocking": [], "findings": []}
                finished = {"state": "spec_completed", "spec_key": "SPEC-95"}
                def retry_review(**kwargs: object) -> tuple[dict[str, object], dict[str, object]]:
                    replacement_worker = f"codex_sdk:{run_id}:codex_review:SPEC-95:{candidate_sha[:12]}"
                    store.begin_stage(
                        run_id,
                        step_name="codex_review",
                        operation_id=review_operation,
                        backend_kind="codex_sdk",
                        worker_id=replacement_worker,
                    )
                    store.complete_codex_stage(
                        run_id,
                        review_operation,
                        thread_id="replacement-review-thread",
                        turn_id="replacement-review-turn",
                        state="reviewed",
                        step_name="codex_review",
                        worker_id=replacement_worker,
                    )
                    return replacement_review, {}
                with (
                    patch.object(workflow, "CodexAdapter", ReadOnlyAdapter),
                    patch.object(workflow, "_execute_independent_review", side_effect=retry_review) as retry_review_call,
                    patch.object(workflow, "_finish_codex_implementation", return_value=finished) as finish,
                ):
                    recovered = workflow._recover_after_process_exit(
                        control_root=root / "control",
                        config=config,
                        run=store.find_by_run_id(run_id),
                        brief="brief",
                        brief_digest="brief",
                        store=store,
                    )

                self.assertEqual(recovered, {"created": False, **finished})
                retry_review_call.assert_called_once()
                finish.assert_called_once()
                self.assertEqual(store.operations_for_run(run_id)[-1]["state"], "reviewed")
            finally:
                store.close()

    def test_recovery_replaces_failed_or_interrupted_review_without_replaying_implementation(self) -> None:
        for terminal_status in ("failed", "interrupted"):
            with self.subTest(terminal_status=terminal_status), tempfile.TemporaryDirectory(
                prefix=f"spec-runner-{terminal_status}-review-recovery-"
            ) as temp:
                root = Path(temp)
                repository = root / "repo"
                repository.mkdir()
                subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
                (repository / "README.md").write_text("baseline\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
                subprocess.run(
                    ["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "baseline"],
                    check=True,
                )
                base_sha = subprocess.check_output(
                    ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True,
                ).strip()
                config = RunnerConfig(
                    repository, "HEAD", Path("artifacts"), "codex_sdk", ("production",),
                    "fake", "high", (Path("artifacts"),), None, None, (), "production", "config",
                )
                run_id = f"{terminal_status}-review-recovery-run"
                run = RunRecord(
                    run_id=run_id,
                    launch_key=run_id,
                    input_digest="brief",
                    config_digest="config",
                    repository_path=str(repository),
                    target_ref="HEAD",
                    artifact_root="artifacts",
                    backend_kind="codex_sdk",
                    state="starting",
                    current_step="codex_review",
                    log_path="logs/run.jsonl",
                    created_at=now(),
                    updated_at=now(),
                )
                store = Store.open(root / "control", create=True)
                try:
                    store.create_run(run, f"start:{run_id}")
                    workspace_info = workflow.prepare_workspace(
                        repository=repository,
                        workspace_root=root / "control" / "delivery-workspaces",
                        run_id=run_id,
                        spec_key="SPEC-95",
                        base_ref="HEAD",
                    )
                    review_operation = f"review:{run_id}:SPEC-95:{base_sha}"
                    review_worker = f"codex_sdk:{run_id}:codex_review:SPEC-95:{base_sha[:12]}"
                    implementation_operation = f"implementation:{run_id}:SPEC-95"
                    implementation_worker = f"codex_sdk:{run_id}:codex_implementation:SPEC-95"
                    store.begin_stage(
                        run_id,
                        step_name="codex_implementation",
                        operation_id=implementation_operation,
                        backend_kind="codex_sdk",
                        worker_id=implementation_worker,
                    )
                    store.record_codex_turn_started(
                        run_id,
                        implementation_operation,
                        thread_id="implementation-thread",
                        turn_id="implementation-turn",
                        step_name="codex_implementation",
                        worker_id=implementation_worker,
                    )
                    store.begin_stage(
                        run_id,
                        step_name="codex_review",
                        operation_id=review_operation,
                        backend_kind="codex_sdk",
                        worker_id=review_worker,
                    )
                    store.record_codex_turn_started(
                        run_id,
                        review_operation,
                        thread_id="review-thread",
                        turn_id="review-turn",
                        step_name="codex_review",
                        worker_id=review_worker,
                    )
                    store.set_run_state(run_id, "blocked")
                    artifact = root / "control" / "artifacts" / run_id
                    artifact.mkdir(parents=True)
                    (artifact / "ticket-plan-SPEC-95.json").write_text(
                        json.dumps({
                            "schema_version": "spec-runner-ticket-plan/v1",
                            "spec_key": "SPEC-95",
                            "base_sha": base_sha,
                            "digest": "ticket-digest",
                            "tickets": [{"key": "SPEC-95.1", "body": "Review the candidate.", "blocked_by": [], "acceptance": ["PROJECT_TESTS"]}],
                        }),
                        encoding="utf-8",
                    )
                    (artifact / "candidate-SPEC-95.json").write_text(
                        json.dumps({
                            "candidate_sha": base_sha,
                            "acceptance_version": "ticket-digest",
                            "outcome": "verified",
                        }),
                        encoding="utf-8",
                    )
                    (artifact / "implementation-SPEC-95.json").write_text(
                        json.dumps({
                            "thread_id": "implementation-thread",
                            "turn_id": "implementation-turn",
                            "status": "completed",
                            "error": None,
                            "final_response": json.dumps({"outcome": "completed", "artifacts": [], "blockers": [], "questions": []}),
                            "item_count": 1,
                            "started_at": 1,
                            "completed_at": 2,
                        }),
                        encoding="utf-8",
                    )

                    class ReadOnlyAdapter:
                        def read_thread(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
                            assert thread_id == "review-thread"
                            assert repository_path == Path(str(workspace_info["workspace"]))
                            return {
                                "schema_version": "spec-runner-sdk-thread-inspection/v1",
                                "thread_id": thread_id,
                                "thread_status": "idle",
                                "active_flags": [],
                                "started_turn": False,
                                "turn_count": 1,
                                "turns": [{"turn_id": "review-turn", "status": terminal_status}],
                            }

                        def archive_and_readback(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
                            assert thread_id == "review-thread"
                            assert repository_path == Path(str(workspace_info["workspace"]))
                            return {"thread_id": thread_id, "archived": True, "pages_read": 1}

                    replacement_review = {"candidate_sha": base_sha, "approved": True, "blocking": [], "findings": []}
                    finished = {"state": "spec_completed", "spec_key": "SPEC-95"}
                    with (
                        patch.object(workflow, "CodexAdapter", ReadOnlyAdapter),
                        patch.object(workflow, "_execute_independent_review", return_value=(replacement_review, {})) as retry_review,
                        patch.object(workflow, "_finish_codex_implementation", return_value=finished) as finish,
                    ):
                        recovered = workflow._recover_after_process_exit(
                            control_root=root / "control",
                            config=config,
                            run=store.find_by_run_id(run_id),
                            brief="brief",
                            brief_digest="brief",
                            store=store,
                        )

                    self.assertEqual(recovered, {"created": False, **finished})
                    retry_review.assert_called_once()
                    finish.assert_called_once()
                    review_worker_record = next(
                        item for item in store.workers_for_run(run_id) if item["worker_id"] == review_worker
                    )
                    self.assertEqual(review_worker_record["state"], "rejected")
                    self.assertEqual(
                        store.operations_for_run(run_id)[-1]["state"],
                        "rejected",
                    )
                    self.assertIn(
                        f"{terminal_status}_review_turn_reconciled",
                        [event["event_type"] for event in store.events_for_run(run_id)],
                    )
                    self.assertEqual(finish.call_args.kwargs["result"].thread_id, "implementation-thread")
                finally:
                    store.close()

    def test_recovery_accepts_completed_blocking_review_worker(self) -> None:
        """A validated-but-blocking review is a repair frontier, not a missing worker."""
        with tempfile.TemporaryDirectory(prefix="spec-runner-blocking-review-") as temp:
            root = Path(temp)
            repository = root / "repo"
            repository.mkdir()
            config = RunnerConfig(
                repository, "HEAD", Path("artifacts"), "codex_sdk", ("production",),
                "fake", "high", (Path("artifacts"),), None, None, (), "production", "config",
            )
            run_id = "13131313-1313-1313-1313-131313131313"
            timestamp = now()
            run = RunRecord(
                run_id=run_id,
                launch_key="blocking-review",
                input_digest="brief",
                config_digest="config",
                repository_path=str(repository),
                target_ref="HEAD",
                artifact_root="artifacts",
                backend_kind="codex_sdk",
                state="starting",
                current_step="codex_review",
                log_path="logs/run.jsonl",
                created_at=timestamp,
                updated_at=timestamp,
            )
            store = Store.open(root / "control", create=True)
            try:
                store.create_run(run, f"start:{run_id}")
                operation_id = f"review:{run_id}:SPEC-95:candidate123456"
                worker_id = f"codex_sdk:{run_id}:codex_review:SPEC-95:candidate123456"
                store.begin_stage(
                    run_id, step_name="codex_review", operation_id=operation_id,
                    backend_kind="codex_sdk", worker_id=worker_id,
                )
                store.record_codex_turn_started(
                    run_id, operation_id, thread_id="review-thread", turn_id="review-turn",
                    step_name="codex_review", worker_id=worker_id,
                )
                store.complete_codex_stage(
                    run_id, operation_id, thread_id="review-thread", turn_id="review-turn",
                    state="reviewed", step_name="codex_review", worker_id=worker_id,
                )
                store.set_run_state(run_id, "blocked")
                with patch.object(
                    workflow, "_reconcile_blocked_review",
                    return_value={"state": "repair_started"},
                    create=True,
                ) as reconcile:
                    recovered = workflow._recover_after_process_exit(
                        control_root=root / "control", config=config,
                        run=store.find_by_run_id(run_id), brief="brief", brief_digest="brief", store=store,
                    )
                self.assertEqual(recovered, {"created": False, "state": "repair_started"})
                reconcile.assert_called_once()
            finally:
                store.close()

    def test_recovery_reuses_approved_review_without_replaying_workers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spec-runner-approved-review-") as temp:
            root = Path(temp)
            repository = root / "repo"
            repository.mkdir()
            subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
            (repository / "README.md").write_text("baseline\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "baseline"],
                check=True,
            )
            base_sha = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
            config = RunnerConfig(
                repository, "HEAD", Path("artifacts"), "codex_sdk", ("production",),
                "fake", "high", (Path("artifacts"),), None, None, (), "production", "config",
            )
            run_id = "14141414-1414-1414-1414-141414141414"
            timestamp = now()
            run = RunRecord(
                run_id=run_id, launch_key="approved-review", input_digest="brief",
                config_digest="config", repository_path=str(repository), target_ref="HEAD",
                artifact_root="artifacts", backend_kind="codex_sdk", state="starting",
                current_step="codex_review", log_path="logs/run.jsonl",
                created_at=timestamp, updated_at=timestamp,
            )
            store = Store.open(root / "control", create=True)
            try:
                store.create_run(run, f"start:{run_id}")
                workspace_info = workflow.prepare_workspace(
                    repository=repository, workspace_root=root / "control" / "delivery-workspaces",
                    run_id=run_id, spec_key="SPEC-95", base_ref="HEAD",
                )
                workspace = Path(str(workspace_info["workspace"]))
                (workspace / "implemented.txt").write_text("candidate\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(workspace), "add", "implemented.txt"], check=True)
                subprocess.run(
                    ["git", "-C", str(workspace), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "candidate"],
                    check=True,
                )
                candidate_sha = workflow.git_sha(workspace)
                ticket_plan = workflow.validate_ticket_plan({
                    "schema_version": "spec-runner-ticket-plan/v1", "spec_key": "SPEC-95",
                    "base_sha": base_sha,
                    "tickets": [{"key": "SPEC-95.1", "body": "Implement the fixture.", "blocked_by": [], "acceptance": ["A1"]}],
                }, expected_spec_key="SPEC-95", expected_base_sha=base_sha)
                implementation_operation = f"implementation:{run_id}:SPEC-95"
                implementation_worker = f"codex_sdk:{run_id}:codex_implementation:SPEC-95"
                store.begin_stage(run_id, step_name="codex_implementation", operation_id=implementation_operation,
                                  backend_kind="codex_sdk", worker_id=implementation_worker)
                store.record_codex_turn_started(run_id, implementation_operation, thread_id="implementation-thread",
                                                turn_id="implementation-turn", step_name="codex_implementation",
                                                worker_id=implementation_worker)
                store.complete_codex_stage(run_id, implementation_operation, thread_id="implementation-thread",
                                           turn_id="implementation-turn", state="verified_candidate",
                                           step_name="codex_implementation", worker_id=implementation_worker)
                review_operation = f"review:{run_id}:SPEC-95:{candidate_sha}"
                review_worker = f"codex_sdk:{run_id}:codex_review:SPEC-95:{candidate_sha[:12]}"
                store.begin_stage(run_id, step_name="codex_review", operation_id=review_operation,
                                  backend_kind="codex_sdk", worker_id=review_worker)
                store.record_codex_turn_started(run_id, review_operation, thread_id="review-thread",
                                                turn_id="review-turn", step_name="codex_review",
                                                worker_id=review_worker)
                store.complete_codex_stage(run_id, review_operation, thread_id="review-thread",
                                           turn_id="review-turn", state="reviewed", step_name="codex_review",
                                           worker_id=review_worker)
                store.set_run_state(run_id, "blocked")
                artifact = root / "control" / "artifacts" / run_id
                artifact.mkdir(parents=True)
                (artifact / "ticket-plan-SPEC-95.json").write_text(json.dumps(ticket_plan), encoding="utf-8")
                (artifact / "candidate-SPEC-95.json").write_text(json.dumps({
                    "candidate_sha": candidate_sha, "acceptance_version": ticket_plan["digest"],
                    "outcome": "verified",
                }), encoding="utf-8")
                implementation_document = {
                    "thread_id": "implementation-thread", "turn_id": "implementation-turn",
                    "status": "completed", "error": None,
                    "final_response": json.dumps({"outcome": "completed", "artifacts": ["implemented.txt"], "blockers": [], "questions": []}),
                    "item_count": 1, "started_at": 1, "completed_at": 2,
                }
                (artifact / "implementation-SPEC-95.json").write_text(json.dumps(implementation_document), encoding="utf-8")
                review_result = CodexWorkerResult(
                    thread_id="review-thread", turn_id="review-turn", status="completed", error=None,
                    final_response=json.dumps({
                        "schema_version": "spec-runner-review-result/v1", "candidate_sha": candidate_sha,
                        "acceptance_version": ticket_plan["digest"], "findings": [],
                    }), item_count=1, started_at=1, completed_at=2,
                )
                validated_review = workflow.independent_review(
                    review_result, implementation_thread="implementation-thread", candidate_sha=candidate_sha,
                    acceptance_version=str(ticket_plan["digest"]),
                )
                (artifact / f"review-SPEC-95-{candidate_sha[:12]}.json").write_text(
                    json.dumps({**validated_review, "worker": review_result.public()}), encoding="utf-8",
                )
                (artifact / f"review-worker-SPEC-95-{candidate_sha[:12]}.json").write_text(
                    json.dumps(review_result.public()), encoding="utf-8",
                )

                with (
                    patch.object(workflow, "_execute_independent_review", side_effect=AssertionError("review replayed")),
                    patch.object(workflow, "_repair_candidate", side_effect=AssertionError("implementation replayed")),
                    patch.object(workflow, "_finish_codex_implementation", side_effect=RunnerError("target_ref_changed", "target moved")) as finish,
                ):
                    tampered_review = {**validated_review, "worker": {**review_result.public(), "thread_id": "other-thread"}}
                    (artifact / f"review-SPEC-95-{candidate_sha[:12]}.json").write_text(
                        json.dumps(tampered_review), encoding="utf-8",
                    )
                    with self.assertRaisesRegex(RunnerError, "approved review worker receipt changed"):
                        workflow._recover_after_process_exit(
                            control_root=root / "control", config=config,
                            run=store.find_by_run_id(run_id), brief="brief", brief_digest="brief", store=store,
                        )
                    finish.assert_not_called()
                    (artifact / f"review-SPEC-95-{candidate_sha[:12]}.json").write_text(
                        json.dumps({**validated_review, "worker": review_result.public()}), encoding="utf-8",
                    )
                    with self.assertRaisesRegex(RunnerError, "target moved"):
                        workflow._recover_after_process_exit(
                            control_root=root / "control", config=config,
                            run=store.find_by_run_id(run_id), brief="brief", brief_digest="brief", store=store,
                        )

                finish.assert_called_once()
                self.assertEqual(finish.call_args.kwargs["validated_review"]["review_digest"], validated_review["review_digest"])
                self.assertEqual(finish.call_args.kwargs["validated_review"]["candidate_sha"], candidate_sha)
                self.assertEqual(finish.call_args.kwargs["result"].turn_id, "implementation-turn")
            finally:
                store.close()

    def test_recovery_retries_only_a_confirmed_terminal_failed_turn_at_its_stage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spec-runner-codex-failed-recovery-") as temp:
            root = Path(temp)
            config, run, store = self._failed_sdk_run(root)
            retry_calls: list[dict[str, object]] = []

            class ReadOnlyAdapter:
                def read_thread(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
                    if thread_id != "thread-failed" or repository_path != config.repository_path:
                        raise AssertionError("recovery inspected a different SDK thread or repository")
                    return CodexWorkflowControlTests._failed_turn_inspection()

            def retry_planning(**kwargs: object) -> RunRecord:
                retry_calls.append(kwargs)
                return replace(run, state="planned")

            try:
                with (
                    patch.object(workflow, "CodexAdapter", ReadOnlyAdapter),
                    patch.object(workflow, "_execute_codex_planning", side_effect=retry_planning),
                    patch.object(workflow, "load_json", return_value={"specs": [{"key": "SPEC-1"}]}),
                    patch.object(workflow, "_execute_codex_tickets", return_value=run),
                ):
                    workflow._recover_after_process_exit(
                        control_root=root / "control",
                        config=config,
                        run=run,
                        brief="brief",
                        brief_digest="brief",
                        store=store,
                    )
                self.assertEqual(len(retry_calls), 1)
                self.assertEqual(retry_calls[0]["run"].current_step, "codex_planning")
                self.assertEqual(retry_calls[0]["thread_id"], "thread-failed")
                self.assertIn("failed_sdk_turn_reconciled", [event["event_type"] for event in store.events_for_run(run.run_id)])
            finally:
                store.close()

    def test_recovery_retries_a_completed_planning_turn_with_invalid_spec_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spec-runner-invalid-planning-recovery-") as temp:
            root = Path(temp)
            config, failed, store = self._failed_sdk_run(root)
            artifact = root / "control" / "artifacts" / failed.run_id
            artifact.mkdir(parents=True, exist_ok=True)
            invalid_plan = {
                "outcome": "planned",
                "requirements": ["R1", "R2"],
                "specs": [{
                    "key": "S1",
                    "title": "One",
                    "body": "body",
                    "blocked_by": [],
                    "covers": ["R1"],
                    "route": {"model": "m", "effort": "high", "reason": "fit"},
                }],
                "questions": [],
            }
            result = CodexWorkerResult(
                thread_id="thread-failed",
                turn_id="turn-failed",
                status="completed",
                error=None,
                final_response=json.dumps(invalid_plan),
                item_count=1,
                started_at=1,
                completed_at=2,
            )
            (artifact / f"codex_planning-{hashlib.sha256(b'turn-failed').hexdigest()}.json").write_text(
                json.dumps(result.public()), encoding="utf-8",
            )
            retry_calls: list[dict[str, object]] = []

            class ReadOnlyAdapter:
                def read_thread(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
                    assert thread_id == "thread-failed"
                    assert repository_path == config.repository_path
                    return self._inspection()

                @staticmethod
                def _inspection() -> dict[str, object]:
                    return CodexWorkflowControlTests._failed_turn_inspection(
                        turns=[{"turn_id": "turn-failed", "status": "completed"}],
                    )

            def retry_planning(**kwargs: object) -> dict[str, object]:
                retry_calls.append(kwargs)
                return {"run": {"state": "running"}}

            try:
                current = store.find_by_run_id(failed.run_id)
                assert current is not None
                with (
                    patch.object(workflow, "CodexAdapter", ReadOnlyAdapter),
                    patch.object(workflow, "_resume_codex_stage", side_effect=retry_planning),
                ):
                    recovered = workflow._recover_after_process_exit(
                        control_root=root / "control",
                        config=config,
                        run=current,
                        brief="brief",
                        brief_digest="brief",
                        store=store,
                    )
                assert recovered == {"created": False, "run": {"state": "running"}}
                assert len(retry_calls) == 1
                assert retry_calls[0]["thread_id"] == "thread-failed"
                assert any(
                    event["event_type"] == "invalid_planning_turn_reconciled"
                    for event in store.events_for_run(failed.run_id)
                )
            finally:
                store.close()

    def test_completed_planning_receipt_from_another_thread_is_not_replayed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spec-runner-planning-identity-") as temp:
            root = Path(temp)
            config, failed, store = self._failed_sdk_run(root)
            artifact = root / "control" / "artifacts" / failed.run_id
            artifact.mkdir(parents=True, exist_ok=True)
            receipt = CodexWorkerResult(
                thread_id="different-thread", turn_id="turn-failed", status="completed",
                error=None, final_response=json.dumps({"outcome": "planned"}),
                item_count=1, started_at=1, completed_at=2,
            )
            (artifact / f"codex_planning-{hashlib.sha256(b'turn-failed').hexdigest()}.json").write_text(
                json.dumps(receipt.public()), encoding="utf-8",
            )
            try:
                self.assertIsNone(workflow._blocked_planning_retry_thread(
                    control_root=root / "control", config=config, run=failed,
                    store=store, brief_digest="brief",
                ))
            finally:
                store.close()

    def test_recovery_blocks_unknown_or_in_flight_sdk_results(self) -> None:
        rejected_inspections = [
            None,
            self._failed_turn_inspection(turns=[{"turn_id": "turn-other", "status": "failed"}]),
            self._failed_turn_inspection(turns=[{"turn_id": "turn-failed", "status": "running"}]),
            self._failed_turn_inspection(turns=[{"turn_id": "turn-failed", "status": "completed"}]),
            self._failed_turn_inspection(thread_status="busy"),
            self._failed_turn_inspection(active_flags=["turn_running"]),
            self._failed_turn_inspection(turn_count=2),
        ]
        for index, inspection in enumerate(rejected_inspections):
            with self.subTest(inspection=inspection), tempfile.TemporaryDirectory(
                prefix=f"spec-runner-codex-unknown-recovery-{index}-"
            ) as temp:
                root = Path(temp)
                config, run, store = self._failed_sdk_run(root)

                class ReadOnlyAdapter:
                    def read_thread(self, *, thread_id: str, repository_path: Path) -> object:
                        return inspection

                try:
                    with (
                        patch.object(workflow, "CodexAdapter", ReadOnlyAdapter),
                        patch.object(workflow, "_execute_codex_planning") as retry,
                    ):
                        with self.assertRaises(RunnerError):
                            workflow._recover_after_process_exit(
                                control_root=root / "control",
                                config=config,
                                run=run,
                                brief="brief",
                                brief_digest="brief",
                                store=store,
                            )
                    retry.assert_not_called()
                finally:
                    store.close()

    def test_ticket_recovery_scopes_the_plan_to_the_failed_spec(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spec-runner-codex-ticket-recovery-") as temp:
            root = Path(temp)
            config, run, store = self._failed_sdk_run(root)
            config = replace(config, workflow_mode="example")
            store.close()
            store = Store.open(root / "control", create=False)
            try:
                store.connection.execute("DELETE FROM workers WHERE run_id = ?", (run.run_id,))
                store.connection.commit()
                store.begin_stage(
                    run.run_id,
                    step_name="codex_ticket_planning",
                    operation_id=f"tickets:{run.run_id}:SPEC-2",
                    backend_kind="codex_sdk",
                    worker_id=f"codex_sdk:{run.run_id}:codex_ticket_planning:SPEC-2",
                )
                store.record_codex_turn_started(
                    run.run_id,
                    f"tickets:{run.run_id}:SPEC-2",
                    thread_id="thread-failed",
                    turn_id="turn-failed",
                    step_name="codex_ticket_planning",
                    worker_id=f"codex_sdk:{run.run_id}:codex_ticket_planning:SPEC-2",
                )
                store.fail_run(run.run_id, f"tickets:{run.run_id}:SPEC-2")
                failed = store.find_by_run_id(run.run_id)
                assert failed is not None
                (root / "control" / "artifacts" / run.run_id).mkdir(parents=True)
                (root / "control" / "artifacts" / run.run_id / "spec-plan.json").write_text(
                    json.dumps({"specs": [{"key": "SPEC-1"}, {"key": "SPEC-2"}]}), encoding="utf-8"
                )
                selected: list[dict[str, object]] = []

                class ReadOnlyAdapter:
                    def read_thread(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
                        return CodexWorkflowControlTests._failed_turn_inspection()

                def retry_tickets(**kwargs: object) -> RunRecord:
                    selected.extend(kwargs["spec_plan"]["specs"])
                    return replace(failed, state="tickets_ready")

                with (
                    patch.object(workflow, "CodexAdapter", ReadOnlyAdapter),
                    patch.object(workflow, "_execute_codex_tickets", side_effect=retry_tickets),
                ):
                    workflow._recover_after_process_exit(
                        control_root=root / "control",
                        config=config,
                        run=failed,
                        brief="brief",
                        brief_digest="brief",
                        store=store,
                    )
                self.assertEqual(selected, [{"key": "SPEC-2"}])
            finally:
                store.close()

    def test_process_exit_in_running_sdk_stage_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spec-runner-codex-recovery-") as temp:
            root = Path(temp)
            repository = root / "repo"
            repository.mkdir()
            config = RunnerConfig(
                repository, "HEAD", Path("artifacts"), "codex_sdk", ("production",),
                "fake", "high", (Path("artifacts"),), None, None, (), "production", "config",
            )
            timestamp = now()
            run = RunRecord(
                run_id="22222222-2222-2222-2222-222222222222",
                launch_key="recovery-running",
                input_digest="brief",
                config_digest="config",
                repository_path=str(repository),
                target_ref="HEAD",
                artifact_root="artifacts",
                backend_kind="codex_sdk",
                state="running",
                current_step="codex_grill",
                log_path="logs/run.jsonl",
                created_at=timestamp,
                updated_at=timestamp,
            )
            store = Store.open(root / "control", create=True)
            try:
                store.create_run(run, "start:recovery-running")
                with self.assertRaisesRegex(RunnerError, "no uniquely recoverable external result"):
                    workflow._recover_after_process_exit(
                        control_root=root / "control",
                        config=config,
                        run=run,
                        brief="brief",
                        brief_digest="brief",
                        store=store,
                    )
            finally:
                store.close()

    def test_process_exit_reconciles_completed_running_implementation_turn(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spec-runner-codex-running-implementation-recovery-") as temp:
            root = Path(temp)
            repository = root / "repo"
            repository.mkdir()
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            (repository / "README.md").write_text("baseline\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "baseline"],
                check=True,
            )
            config = RunnerConfig(
                repository, "HEAD", Path("artifacts"), "codex_sdk", ("production",),
                "fake", "high", (Path("artifacts"),), None, None, (), "production", "config",
            )
            run_id = "66666666-6666-6666-6666-666666666666"
            timestamp = now()
            run = RunRecord(
                run_id=run_id,
                launch_key="running-implementation-recovery",
                input_digest="brief",
                config_digest="config",
                repository_path=str(repository),
                target_ref="HEAD",
                artifact_root="artifacts",
                backend_kind="codex_sdk",
                state="starting",
                current_step="codex_implementation",
                log_path="logs/run.jsonl",
                created_at=timestamp,
                updated_at=timestamp,
            )
            store = Store.open(root / "control", create=True)
            try:
                store.create_run(run, f"start:{run_id}")
                operation_id = f"implementation:{run_id}:SPEC-95"
                worker_id = f"codex_sdk:{run_id}:codex_implementation:SPEC-95"
                store.begin_stage(
                    run_id, step_name="codex_implementation", operation_id=operation_id,
                    backend_kind="codex_sdk", worker_id=worker_id,
                )
                store.record_codex_turn_started(
                    run_id, operation_id, thread_id="implementation-thread",
                    turn_id="implementation-turn", step_name="codex_implementation",
                    worker_id=worker_id,
                )
                running = store.find_by_run_id(run_id)
                assert running is not None
                store.set_run_state(run_id, "blocked")
                running = store.find_by_run_id(run_id)
                assert running is not None
                workflow.prepare_workspace(
                    repository=repository, workspace_root=root / "control" / "delivery-workspaces",
                    run_id=run_id, spec_key="SPEC-95", base_ref="HEAD",
                )
                reconciled = {"run": {"state": "needs_input"}}

                class ReadOnlyAdapter:
                    def read_thread(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
                        self_thread = thread_id
                        self_path = repository_path
                        expected_path = (root / "control" / "delivery-workspaces" / "SPEC-95-66666666").resolve()
                        if self_thread != "implementation-thread" or self_path.resolve() != expected_path:
                            raise AssertionError("recovery inspected the wrong SDK thread or workspace")
                        return {
                            "schema_version": "spec-runner-sdk-thread-inspection/v1",
                            "thread_id": "implementation-thread",
                            "thread_status": "idle",
                            "active_flags": [],
                            "started_turn": False,
                            "turn_count": 1,
                            "turns": [{"turn_id": "implementation-turn", "status": "completed"}],
                        }

                with (
                    patch.object(workflow, "CodexAdapter", ReadOnlyAdapter),
                    patch.object(workflow, "_reconcile_completed_implementation_turn", return_value=reconciled) as reconcile,
                    patch.object(workflow, "_execute_codex_implementation", side_effect=AssertionError("must not replay a completed turn")),
                ):
                    recovered = workflow._recover_after_process_exit(
                        control_root=root / "control",
                        config=config,
                        run=running,
                        brief="brief",
                        brief_digest="brief",
                        store=store,
                    )

                self.assertEqual(recovered, {"created": False, **reconciled})
                reconcile.assert_called_once()
                self.assertEqual(reconcile.call_args.kwargs["thread_id"], "implementation-thread")
                self.assertEqual(reconcile.call_args.kwargs["turn_id"], "implementation-turn")
            finally:
                store.close()

    def test_finish_implementation_reuses_clean_existing_candidate_commit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spec-runner-clean-candidate-") as temp:
            root = Path(temp)
            repository = root / "repo"
            repository.mkdir()
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            (repository / "README.md").write_text("baseline\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "baseline"],
                check=True,
            )
            base_sha = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
            config = RunnerConfig(
                repository, "HEAD", Path("artifacts"), "codex_sdk", ("production",),
                "fake", "high", (Path("artifacts"),), None, None, (), "production", "config",
                acceptance_checks=({"command": [sys.executable, "-c", "pass"], "acceptance": ["PROJECT_TESTS"]},),
                acceptance_ids=("PROJECT_TESTS",),
                acceptance_paths=("implemented",),
            )
            run_id = "77777777-7777-7777-7777-777777777777"
            run = RunRecord(
                run_id=run_id,
                launch_key="clean-candidate",
                input_digest="brief",
                config_digest="config",
                repository_path=str(repository),
                target_ref="HEAD",
                artifact_root="artifacts",
                backend_kind="codex_sdk",
                state="blocked",
                current_step="codex_implementation",
                log_path="logs/run.jsonl",
                created_at=now(),
                updated_at=now(),
            )
            store = Store.open(root / "control", create=True)
            try:
                store.create_run(run, f"start:{run_id}")
                workspace_info = workflow.prepare_workspace(
                    repository=repository, workspace_root=root / "control" / "delivery-workspaces",
                    run_id=run_id, spec_key="SPEC-95", base_ref="HEAD",
                )
                workspace = Path(str(workspace_info["workspace"]))
                artifact_path = workspace / "implemented" / "implemented.txt"
                artifact_path.parent.mkdir(parents=True)
                artifact_path.write_text("already committed\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(workspace), "add", "implemented/implemented.txt"], check=True)
                subprocess.run(
                    ["git", "-C", str(workspace), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "implementation"],
                    check=True,
                )
                existing_candidate_sha = subprocess.check_output(["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True).strip()
                result = CodexWorkerResult(
                    thread_id="implementation-thread",
                    turn_id="implementation-turn",
                    status="completed",
                    error=None,
                    final_response=json.dumps({
                        "outcome": "completed",
                        "artifacts": ["implemented.txt"],
                        "blockers": [],
                        "questions": [],
                    }),
                    item_count=1,
                    started_at=1,
                    completed_at=2,
                )
                ticket_plan = {"spec_key": "SPEC-95", "digest": "ticket-digest", "base_sha": base_sha}
                candidate_receipt = {"candidate_sha": existing_candidate_sha, "passed": True}
                review = {"approved": True, "blocking": [], "candidate_sha": existing_candidate_sha}

                class ReadOnlyAdapter:
                    def archive_and_readback(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
                        return {"thread_id": thread_id, "archived": True, "pages_read": 1}

                with (
                    patch.object(workflow, "verify_candidate", return_value=candidate_receipt) as verify,
                    patch.object(store, "complete_codex_stage"),
                    patch.object(workflow, "_execute_independent_review", return_value=(review, {})),
                    patch.object(workflow, "CodexAdapter", ReadOnlyAdapter),
                    patch.object(workflow, "merge_local", return_value={"candidate_sha": existing_candidate_sha}),
                    patch.object(workflow, "_persist_delivery_evidence"),
                    patch.object(workflow, "cleanup_managed_workspace", return_value={"outcome": "cleaned"}),
                ):
                    with self.assertRaisesRegex(RunnerError, "persisted review does not approve"):
                        workflow._finish_codex_implementation(
                            control_root=root / "control",
                            config=config,
                            brief_digest="brief",
                            run=run,
                            store=store,
                            ticket_plan=ticket_plan,
                            workspace_info=workspace_info,
                            result=result,
                            finalize_run=False,
                            validated_review={"approved": True, "candidate_sha": "different-candidate"},
                        )
                    finished = workflow._finish_codex_implementation(
                        control_root=root / "control",
                        config=config,
                        brief_digest="brief",
                        run=run,
                        store=store,
                        ticket_plan=ticket_plan,
                        workspace_info=workspace_info,
                        result=result,
                        finalize_run=False,
                    )

                self.assertEqual(finished["state"], "spec_completed")
                self.assertEqual(verify.call_args.kwargs["candidate_sha"], existing_candidate_sha)
                self.assertEqual(
                    subprocess.check_output(["git", "-C", str(workspace), "rev-list", "--count", "HEAD"], text=True).strip(),
                    "2",
                )
                self.assertNotEqual(existing_candidate_sha, base_sha)
            finally:
                store.close()

    def test_finish_implementation_routes_initial_candidate_failure_to_repair(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spec-runner-initial-candidate-failure-") as temp:
            root = Path(temp)
            repository = root / "repo"
            repository.mkdir()
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            (repository / "README.md").write_text("baseline\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "baseline"],
                check=True,
            )
            base_sha = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
            config = RunnerConfig(
                repository, "HEAD", Path("artifacts"), "codex_sdk", ("production",),
                "fake", "high", (Path("artifacts"),), None, None, (), "production", "config",
                acceptance_checks=({"command": [sys.executable, "-c", "pass"], "acceptance": ["PROJECT_TESTS"]},),
                acceptance_ids=("PROJECT_TESTS",), acceptance_paths=("implemented",),
            )
            run_id = "78787878-7878-7878-7878-787878787878"
            run = RunRecord(
                run_id=run_id, launch_key="initial-candidate-failure", input_digest="brief",
                config_digest="config", repository_path=str(repository), target_ref="HEAD",
                artifact_root="artifacts", backend_kind="codex_sdk", state="running",
                current_step="codex_implementation", log_path="logs/run.jsonl",
                created_at=now(), updated_at=now(),
            )
            store = Store.open(root / "control", create=True)
            try:
                store.create_run(run, f"start:{run_id}")
                operation_id = f"implementation:{run_id}:SPEC-95"
                worker_id = f"codex_sdk:{run_id}:codex_implementation:SPEC-95"
                store.begin_stage(run_id, step_name="codex_implementation", operation_id=operation_id,
                                  backend_kind="codex_sdk", worker_id=worker_id)
                store.record_codex_turn_started(
                    run_id, operation_id, thread_id="implementation-thread", turn_id="implementation-turn",
                    step_name="codex_implementation", worker_id=worker_id,
                )
                workspace_info = workflow.prepare_workspace(
                    repository=repository, workspace_root=root / "control" / "delivery-workspaces",
                    run_id=run_id, spec_key="SPEC-95", base_ref="HEAD",
                )
                workspace = Path(str(workspace_info["workspace"]))
                artifact = workspace / "implemented" / "implemented.txt"
                artifact.parent.mkdir(parents=True)
                artifact.write_text("candidate\n", encoding="utf-8")
                result = CodexWorkerResult(
                    thread_id="implementation-thread", turn_id="implementation-turn", status="completed",
                    error=None, final_response=json.dumps({
                        "outcome": "completed", "artifacts": ["implemented.txt"],
                        "blockers": [], "questions": [],
                    }), item_count=1, started_at=1, completed_at=2,
                )
                failure = RunnerError(
                    "candidate_verification_failed", "required check failed",
                    details={"checks": [{"command": ["pytest"], "passed": False}]},
                )
                with (
                    patch.object(workflow, "verify_candidate", side_effect=failure) as verify,
                    patch.object(workflow, "_repair_candidate", return_value=("repaired-sha", {"candidate_sha": "repaired-sha"})) as repair,
                    patch.object(workflow, "_resume_after_repair_candidate", return_value={"state": "repaired"}) as resume_repair,
                ):
                    recovered = workflow._finish_codex_implementation(
                        control_root=root / "control", config=config, brief_digest="brief",
                        run=run, store=store,
                        ticket_plan={"spec_key": "SPEC-95", "digest": "ticket-digest", "base_sha": base_sha},
                        workspace_info=workspace_info, result=result, finalize_run=False,
                    )
                self.assertEqual(recovered, {"state": "repaired"})
                verify.assert_called_once()
                repair.assert_called_once()
                self.assertEqual(repair.call_args.kwargs["implementation_thread"], "implementation-thread")
                self.assertEqual(repair.call_args.kwargs["findings"][0]["verification_error"], "candidate_verification_failed")
                resume_repair.assert_called_once()
            finally:
                store.close()

    def test_repair_blocker_with_workspace_changes_reaches_acceptance(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spec-runner-repair-blocker-") as temp:
            root = Path(temp)
            repository = root / "repo"
            repository.mkdir()
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            (repository / "README.md").write_text("baseline\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "baseline"],
                check=True,
            )
            base_sha = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
            config = RunnerConfig(
                repository, "HEAD", Path("artifacts"), "codex_sdk", ("production",),
                "fake", "high", (Path("artifacts"),), None, None, (), "production", "config",
                acceptance_checks=({"command": [sys.executable, "-c", "pass"], "acceptance": ["PROJECT_TESTS"]},),
                acceptance_ids=("PROJECT_TESTS",),
                acceptance_paths=("scope",),
            )
            run_id = "99999999-9999-9999-9999-999999999999"
            run = RunRecord(
                run_id=run_id,
                launch_key="repair-blocker",
                input_digest="brief",
                config_digest="config",
                repository_path=str(repository),
                target_ref="HEAD",
                artifact_root="artifacts",
                backend_kind="codex_sdk",
                state="blocked",
                current_step="codex_repair",
                log_path="logs/run.jsonl",
                created_at=now(),
                updated_at=now(),
            )
            store = Store.open(root / "control", create=True)
            try:
                store.create_run(run, f"start:{run_id}")
                operation = f"repair:{run_id}:SPEC-95:1"
                worker = f"codex_sdk:{run_id}:codex_repair:SPEC-95"
                store.begin_stage(run_id, step_name="codex_repair", operation_id=operation, backend_kind="codex_sdk", worker_id=worker)
                workspace_info = workflow.prepare_workspace(
                    repository=repository, workspace_root=root / "control" / "delivery-workspaces",
                    run_id=run_id, spec_key="SPEC-95", base_ref="HEAD",
                )
                workspace = Path(str(workspace_info["workspace"]))
                (workspace / "scope").mkdir()
                (workspace / "scope" / "repaired.txt").write_text("repair\n", encoding="utf-8")
                result = CodexWorkerResult(
                    thread_id="implementation-thread",
                    turn_id="repair-turn",
                    status="completed",
                    error=None,
                    final_response=json.dumps({
                        "outcome": "completed",
                        "artifacts": ["repaired.txt"],
                        "blockers": ["broader test environment unavailable"],
                        "questions": [],
                    }),
                    item_count=1,
                    started_at=1,
                    completed_at=2,
                )
                artifact_directory = root / "control" / "artifacts" / run_id
                artifact_directory.mkdir(parents=True)
                ticket_plan = {"spec_key": "SPEC-95", "digest": "ticket-digest", "base_sha": base_sha}
                with (
                    patch.object(workflow, "_run_worker", return_value=result),
                    patch.object(workflow.CodexAdapter, "unarchive_and_readback", return_value={"thread_id": "implementation-thread", "archived": False, "resumed": True}),
                    patch.object(workflow, "verify_candidate", return_value={"candidate_sha": "candidate", "passed": True}) as verify,
                ):
                    candidate_sha, receipt = workflow._repair_candidate(
                        control_root=root / "control", config=config, brief_digest="brief", run=run,
                        store=store, ticket_plan=ticket_plan, workspace=workspace,
                        findings=[{"severity": "high", "description": "repair this"}],
                        implementation_thread="implementation-thread", artifact_directory=artifact_directory,
                    )

                self.assertNotEqual(candidate_sha, base_sha)
                self.assertEqual(receipt["passed"], True)
                verify.assert_called_once()
                self.assertEqual(subprocess.check_output(["git", "-C", str(workspace), "rev-list", "--count", "HEAD"], text=True).strip(), "2")
            finally:
                store.close()

    def test_process_exit_recovers_failed_repair_turn_on_original_thread(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spec-runner-codex-repair-recovery-") as temp:
            root = Path(temp)
            repository = root / "repo"
            repository.mkdir()
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            (repository / "README.md").write_text("baseline\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "baseline"],
                check=True,
            )
            base_sha = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
            config = RunnerConfig(
                repository, "HEAD", Path("artifacts"), "codex_sdk", ("production",),
                "fake", "high", (Path("artifacts"),), None, None, (), "production", "config",
            )
            run_id = "88888888-8888-8888-8888-888888888888"
            run = RunRecord(
                run_id=run_id,
                launch_key="repair-recovery",
                input_digest="brief",
                config_digest="config",
                repository_path=str(repository),
                target_ref="HEAD",
                artifact_root="artifacts",
                backend_kind="codex_sdk",
                state="starting",
                current_step="codex_repair",
                log_path="logs/run.jsonl",
                created_at=now(),
                updated_at=now(),
            )
            store = Store.open(root / "control", create=True)
            try:
                store.create_run(run, f"start:{run_id}")
                implementation_operation = f"implementation:{run_id}:SPEC-95"
                implementation_worker = f"codex_sdk:{run_id}:codex_implementation:SPEC-95"
                store.begin_stage(
                    run_id, step_name="codex_implementation", operation_id=implementation_operation,
                    backend_kind="codex_sdk", worker_id=implementation_worker,
                )
                store.record_codex_turn_started(
                    run_id, implementation_operation, thread_id="implementation-thread",
                    turn_id="implementation-turn", step_name="codex_implementation",
                    worker_id=implementation_worker,
                )
                operation_id = f"repair:{run_id}:SPEC-95:6"
                worker_id = f"codex_sdk:{run_id}:codex_repair:SPEC-95"
                store.begin_stage(
                    run_id, step_name="codex_repair", operation_id=operation_id,
                    backend_kind="codex_sdk", worker_id=worker_id,
                )
                store.record_codex_turn_started(
                    run_id, operation_id, thread_id="implementation-thread",
                    turn_id="repair-turn", step_name="codex_repair", worker_id=worker_id,
                )
                store.set_run_state(run_id, "blocked")
                blocked = store.find_by_run_id(run_id)
                assert blocked is not None
                workflow.prepare_workspace(
                    repository=repository, workspace_root=root / "control" / "delivery-workspaces",
                    run_id=run_id, spec_key="SPEC-95", base_ref="HEAD",
                )
                artifact = root / "control" / "artifacts" / run_id
                artifact.mkdir(parents=True)
                (artifact / "ticket-plan-SPEC-95.json").write_text(
                    json.dumps({
                        "schema_version": "spec-runner-ticket-plan/v1",
                        "spec_key": "SPEC-95",
                        "base_sha": base_sha,
                        "tickets": [{"key": "SPEC-95.1", "body": "Repair the coordinator.", "blocked_by": [], "acceptance": ["PROJECT_TESTS"]}],
                    }),
                    encoding="utf-8",
                )
                (artifact / "review-SPEC-95-5fd4820bf202.json").write_text(
                    json.dumps({"blocking": [{"severity": "high", "status": "open", "description": "repair this"}]}),
                    encoding="utf-8",
                )

                class ReadOnlyAdapter:
                    def read_thread(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
                        self.assertion = (thread_id, repository_path)
                        return {
                            "schema_version": "spec-runner-sdk-thread-inspection/v1",
                            "thread_id": "implementation-thread",
                            "thread_status": "idle",
                            "active_flags": [],
                            "started_turn": False,
                            "turn_count": 1,
                            "turns": [{"turn_id": "repair-turn", "status": "failed"}],
                        }

                reconciled = {"state": "spec_completed"}
                with (
                    patch.object(workflow, "CodexAdapter", ReadOnlyAdapter),
                    patch.object(workflow, "_repair_candidate", return_value=("new-candidate", {"candidate_sha": "new-candidate"})) as repair,
                    patch.object(workflow, "_reconcile_completed_implementation_turn", return_value=reconciled) as reconcile,
                ):
                    recovered = workflow._recover_after_process_exit(
                        control_root=root / "control", config=config, run=blocked,
                        brief="brief", brief_digest="brief", store=store,
                    )

                self.assertEqual(recovered, {"created": False, **reconciled})
                repair.assert_called_once()
                self.assertEqual(repair.call_args.kwargs["implementation_thread"], "implementation-thread")
                reconcile.assert_called_once()
            finally:
                store.close()

    def test_process_exit_reconciles_completed_ticket_turn(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spec-runner-codex-completed-ticket-recovery-") as temp:
            root = Path(temp)
            config, failed, store = self._failed_sdk_run(root)
            try:
                store.connection.execute("DELETE FROM workers WHERE run_id = ?", (failed.run_id,))
                store.connection.commit()
                operation_id = f"tickets:{failed.run_id}:SPEC-2"
                worker_id = f"codex_sdk:{failed.run_id}:codex_ticket_planning:SPEC-2"
                store.begin_stage(
                    failed.run_id,
                    step_name="codex_ticket_planning",
                    operation_id=operation_id,
                    backend_kind="codex_sdk",
                    worker_id=worker_id,
                )
                store.record_codex_turn_started(
                    failed.run_id,
                    operation_id,
                    thread_id="thread-completed",
                    turn_id="turn-completed",
                    step_name="codex_ticket_planning",
                    worker_id=worker_id,
                )
                running = store.find_by_run_id(failed.run_id)
                assert running is not None
                config = replace(config, workflow_mode="test")
                artifact = root / "control" / "artifacts" / running.run_id
                artifact.mkdir(parents=True)
                (artifact / "spec-plan.json").write_text(
                    json.dumps({"specs": [{"key": "SPEC-2", "title": "Coordinator", "body": "Coordinator"}]}),
                    encoding="utf-8",
                )
                worker_result = CodexWorkerResult(
                    thread_id="thread-completed",
                    turn_id="turn-completed",
                    status="completed",
                    error=None,
                    final_response=json.dumps({
                        "outcome": "generated a planning draft without publishing or modifying files",
                        "tickets": [{
                            "key": "SPEC-2.1",
                            "title": "Implement the coordinator slice",
                            "body": "Make the coordinator slice verifiable.",
                            "blocked_by": [],
                            "acceptance": ["The coordinator slice is verifiable."],
                        }],
                        "questions": [],
                    }),
                    item_count=1,
                    started_at=1,
                    completed_at=2,
                )
                (artifact / "codex_ticket_planning-recovered.json").write_text(
                    json.dumps(worker_result.public()), encoding="utf-8"
                )
                class ReadOnlyAdapter:
                    def read_thread(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
                        return {
                            **CodexWorkflowControlTests._failed_turn_inspection(
                                thread_id="thread-completed",
                                turns=[{"turn_id": "turn-completed", "status": "completed"}],
                            ),
                            "thread_status": "idle",
                        }

                with (
                    patch.object(workflow, "CodexAdapter", ReadOnlyAdapter),
                    patch.object(workflow, "git_sha", return_value="base-sha"),
                ):
                    recovered = workflow._recover_after_process_exit(
                        control_root=root / "control",
                        config=config,
                        run=running,
                        brief="brief",
                        brief_digest="brief",
                        store=store,
                    )

                assert recovered is not None
                self.assertEqual(recovered["run"]["state"], "tickets_ready")
                self.assertTrue((artifact / "ticket-plan-SPEC-2.json").is_file())
                self.assertEqual(
                    len(list(artifact.glob("codex_ticket_planning-*.json"))),
                    1,
                )
                self.assertIn(
                    "completed_sdk_turn_reconciled",
                    [event["event_type"] for event in store.events_for_run(running.run_id)],
                )
            finally:
                store.close()

    def test_process_exit_reconciles_completed_implementation_input_gate_without_replay(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spec-runner-codex-implementation-recovery-") as temp:
            root = Path(temp)
            repository = root / "repo"
            repository.mkdir()
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            (repository / "README.md").write_text("baseline\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "baseline"],
                check=True,
            )
            base_sha = subprocess.check_output(
                ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
            ).strip()
            config = RunnerConfig(
                repository, "HEAD", Path("artifacts"), "codex_sdk", ("production",),
                "fake", "high", (Path("artifacts"),), None, None, (), "production", "config",
                acceptance_checks=({"command": [sys.executable, "-c", "pass"], "acceptance": ["PROJECT_TESTS"]},),
                acceptance_ids=("PROJECT_TESTS",),
            )
            run_id = "55555555-5555-5555-5555-555555555555"
            timestamp = now()
            run = RunRecord(
                run_id=run_id,
                launch_key="implementation-recovery",
                input_digest="brief",
                config_digest="config",
                repository_path=str(repository),
                target_ref="HEAD",
                artifact_root="artifacts",
                backend_kind="codex_sdk",
                state="starting",
                current_step="codex_implementation",
                log_path="logs/run.jsonl",
                created_at=timestamp,
                updated_at=timestamp,
            )
            store = Store.open(root / "control", create=True)
            try:
                store.create_run(run, f"start:{run_id}")
                operation_id = f"implementation:{run_id}:SPEC-95"
                worker_id = f"codex_sdk:{run_id}:codex_implementation:SPEC-95"
                store.begin_stage(
                    run_id, step_name="codex_implementation", operation_id=operation_id,
                    backend_kind="codex_sdk", worker_id=worker_id,
                )
                store.record_codex_turn_started(
                    run_id, operation_id, thread_id="implementation-thread",
                    turn_id="implementation-turn", step_name="codex_implementation",
                    worker_id=worker_id,
                )
                store.fail_run(run_id, operation_id)
                failed = store.find_by_run_id(run_id)
                assert failed is not None
                workspace_info = workflow.prepare_workspace(
                    repository=repository, workspace_root=root / "control" / "delivery-workspaces",
                    run_id=run_id, spec_key="SPEC-95", base_ref="HEAD",
                )
                artifact = root / "control" / "artifacts" / run_id
                artifact.mkdir(parents=True)
                (artifact / "ticket-plan-SPEC-95.json").write_text(
                    json.dumps({
                        "schema_version": "spec-runner-ticket-plan/v1",
                        "spec_key": "SPEC-95",
                        "base_sha": base_sha,
                        "tickets": [{
                            "key": "SPEC-95.1",
                            "body": "Implement CoordinatorSpec.",
                            "blocked_by": [],
                            "acceptance": ["PROJECT_TESTS"],
                        }],
                    }),
                    encoding="utf-8",
                )
                persisted = CodexWorkerResult(
                    thread_id="implementation-thread",
                    turn_id="implementation-turn",
                    status="completed",
                    error=None,
                    final_response=json.dumps({
                        "outcome": "needs_input",
                        "artifacts": [],
                        "blockers": [],
                        "questions": [{
                            "id": "implementation_target",
                            "question": "Confirm the implementation boundary.",
                            "options": ["Use the existing product entry point.", "Leave delivery controls to Runner."],
                        }],
                    }),
                    item_count=1,
                    started_at=1,
                    completed_at=2,
                )
                (artifact / "implementation-SPEC-95.json").write_text(
                    json.dumps(persisted.public()), encoding="utf-8"
                )

                class ReadOnlyAdapter:
                    def read_thread(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
                        self_thread = thread_id
                        self_path = repository_path
                        if self_thread != "implementation-thread" or self_path != Path(str(workspace_info["workspace"])):
                            raise AssertionError("implementation recovery inspected the wrong thread or workspace")
                        return {
                            "schema_version": "spec-runner-sdk-thread-inspection/v1",
                            "thread_id": "implementation-thread",
                            "thread_status": "idle",
                            "active_flags": [],
                            "started_turn": False,
                            "turn_count": 1,
                            "turns": [{"turn_id": "implementation-turn", "status": "completed"}],
                        }

                with (
                    patch.object(workflow, "CodexAdapter", ReadOnlyAdapter),
                    patch.object(workflow, "_execute_codex_implementation", side_effect=AssertionError("must not replay a completed turn")),
                ):
                    recovered = workflow._recover_after_process_exit(
                        control_root=root / "control", config=config, run=failed,
                        brief="brief", brief_digest="brief", store=store,
                    )

                assert recovered is not None
                self.assertEqual(recovered["run"]["state"], "needs_input")
                self.assertEqual(recovered["run"]["current_step"], "codex_implementation")
                self.assertEqual(recovered["answers"], [])
                self.assertEqual(recovered["workers"][-1]["external_turn_id"], "implementation-turn")
                self.assertEqual(recovered["events"][-1]["event_type"], "completed_sdk_turn_reconciled")
                self.assertTrue((artifact / "worker-result.json").is_file())
            finally:
                store.close()

    def test_pause_resume_reuses_persisted_thread_and_finishes_second_stage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spec-runner-codex-control-") as temp:
            root = Path(temp)
            repository = root / "repo"
            repository.mkdir()
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            brief_file = root / "brief.md"
            brief_file.write_text("bounded requirement\n", encoding="utf-8")
            config_file = root / "runner.json"
            config_file.write_text(
                json.dumps(
                    {
                        "schema_version": "spec-runner-config/v1",
                        "repository_path": str(repository),
                        "target_ref": "HEAD",
                        "artifact_root": "artifacts",
                        "execution_backend": "codex_sdk",
                        "allowed_stages": ["example"],
                        "model": {"name": "fake", "effort": "none"},
                        "authorization": {"artifact_roots": ["artifacts"]},
                    }
                ),
                encoding="utf-8",
            )
            control_root = root / "control"
            config = RunnerConfig.from_file(config_file, control_root)
            brief, digest = read_brief(brief_file)
            run_id = "11111111-1111-1111-1111-111111111111"
            timestamp = now()
            run = RunRecord(
                run_id=run_id,
                launch_key="codex-control",
                input_digest=digest,
                config_digest=config.digest,
                repository_path=str(repository),
                target_ref="HEAD",
                artifact_root="artifacts",
                backend_kind="codex_sdk",
                state="starting",
                current_step="codex_example",
                log_path=f"logs/{run_id}.jsonl",
                created_at=timestamp,
                updated_at=timestamp,
            )
            store = Store.open(control_root, create=True)
            try:
                store.create_run(run, f"start:{run_id}")
                store.request_control(run_id, "pause_requested")
                FakeResumableAdapter.calls = 0
                FakeResumableAdapter.resumed_thread_ids = []
                with patch.object(workflow, "CodexAdapter", FakeResumableAdapter):
                    paused = workflow._execute_codex_example(
                        control_root=control_root,
                        config=config,
                        brief=brief,
                        brief_digest=digest,
                        run=run,
                        store=store,
                    )
                    self.assertEqual(paused.state, "paused")
                    self.assertEqual(store.workers_for_run(run_id)[0]["external_thread_id"], "thread-1")
                    store.clear_control(run_id)
                    current = store.find_by_run_id(run_id)
                    assert current is not None
                    completed = workflow._resume_codex_stage(
                        control_root=control_root,
                        config=config,
                        run=current,
                        brief=brief,
                        brief_digest=digest,
                        store=store,
                    )
                self.assertEqual(completed["run"]["state"], "completed")
                self.assertEqual(FakeResumableAdapter.resumed_thread_ids[:3], [None, "thread-1", None])
                self.assertIn("control_applied", [event["event_type"] for event in completed["events"]])
                self.assertEqual(len(completed["verification"]), 2)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
