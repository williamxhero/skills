from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from spec_runner.errors import RunnerError
from spec_runner.store import RunRecord
from spec_runner.verification import verify_run


class VerificationTests(unittest.TestCase):
    def test_declared_workspace_artifact_is_digest_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_dir = root / "artifacts" / "run-1"
            workspace_artifact = root / "spec-runner-output" / "run-1" / "handoff.md"
            artifact_dir.mkdir(parents=True)
            workspace_artifact.parent.mkdir(parents=True)
            workspace_artifact.write_text("handoff", encoding="utf-8")
            (artifact_dir / "worker-result.json").write_text(
                json.dumps(
                    {
                        "schema_version": "spec-runner-worker-result/v1",
                        "input_digest": "input",
                        "thread_id": "thread",
                        "turn_id": "turn",
                        "status": "completed",
                        "artifacts": ["spec-runner-output/run-1/handoff.md"],
                    }
                ),
                encoding="utf-8",
            )
            run = RunRecord(
                run_id="run-1", launch_key="launch", input_digest="input", config_digest="config",
                repository_path=str(root), target_ref="HEAD", artifact_root="artifacts", backend_kind="codex_sdk",
                state="turn_completed", current_step="codex_example", log_path="logs/run-1.jsonl", created_at="now", updated_at="now",
            )
            receipt = verify_run(
                control_root=root, run=run, worker={"external_thread_id": "thread", "external_turn_id": "turn"}
            )
            self.assertEqual(receipt["outcome"], "verified")
            self.assertIn("workspace:spec-runner-output/run-1/handoff.md", receipt["artifact_digests"])

    def test_nonempty_codex_response_without_artifact_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_dir = root / "artifacts" / "run-1"
            artifact_dir.mkdir(parents=True)
            (artifact_dir / "worker-result.json").write_text(
                json.dumps(
                    {
                        "schema_version": "spec-runner-worker-result/v1",
                        "input_digest": "input",
                        "thread_id": "thread",
                        "turn_id": "turn",
                        "status": "completed",
                        "final_response": "I did nothing but answer.",
                        "artifacts": [],
                    }
                ),
                encoding="utf-8",
            )
            run = RunRecord(
                run_id="run-1",
                launch_key="launch",
                input_digest="input",
                config_digest="config",
                repository_path=str(root),
                target_ref="HEAD",
                artifact_root="artifacts",
                backend_kind="codex_sdk",
                state="turn_completed",
                current_step="codex_example",
                log_path="logs/run-1.jsonl",
                created_at="now",
                updated_at="now",
            )
            with self.assertRaisesRegex(RunnerError, "non-empty worker text"):
                verify_run(
                    control_root=root,
                    run=run,
                    worker={"external_thread_id": "thread", "external_turn_id": "turn"},
                )

    def test_wrong_digest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_dir = root / "artifacts" / "run-1"
            artifact_dir.mkdir(parents=True)
            (artifact_dir / "brief.md").write_text("wrong", encoding="utf-8")
            (artifact_dir / "handoff.json").write_text(
                json.dumps({"run_id": "run-1", "brief_digest": "expected", "backend_kind": "deterministic_test"}),
                encoding="utf-8",
            )
            run = RunRecord(
                run_id="run-1",
                launch_key="launch",
                input_digest="expected",
                config_digest="config",
                repository_path=str(root),
                target_ref="HEAD",
                artifact_root="artifacts",
                backend_kind="deterministic_test",
                state="starting",
                current_step="deterministic_example",
                log_path="logs/run-1.jsonl",
                created_at="now",
                updated_at="now",
            )
            with self.assertRaisesRegex(RunnerError, "brief.md digest"):
                verify_run(control_root=root, run=run, worker={})


if __name__ == "__main__":
    unittest.main()
