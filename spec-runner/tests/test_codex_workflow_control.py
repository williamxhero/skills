from __future__ import annotations

import json
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

    def test_recovery_blocks_unknown_or_in_flight_sdk_results(self) -> None:
        rejected_inspections = [
            self._failed_turn_inspection(turns=[{"turn_id": "turn-other", "status": "failed"}]),
            self._failed_turn_inspection(turns=[{"turn_id": "turn-failed", "status": "running"}]),
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
                    def read_thread(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
                        return inspection

                try:
                    with (
                        patch.object(workflow, "CodexAdapter", ReadOnlyAdapter),
                        patch.object(workflow, "_execute_codex_planning") as retry,
                    ):
                        with self.assertRaisesRegex(RunnerError, "no uniquely recoverable external result"):
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
