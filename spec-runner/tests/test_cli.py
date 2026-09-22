from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PACKAGE_ROOT / "src"


def digest_tree(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class SpecRunnerCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="spec runner 中文 ")
        self.root = Path(self.temporary_directory.name)
        self.repository = self.root / "仓库 with spaces"
        self.repository.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repository)], check=True)
        self.brief = self.root / "brief.md"
        self.brief.write_text("# 中文 brief\n交付隔离交接。\n", encoding="utf-8")
        self.config = self.root / "runner.json"
        self.write_config()
        self.control_root = self.root / "控制 root"

    def tearDown(self) -> None:
        # On Windows the detached child can finish its final JSON write just
        # after the run reaches `completed`; wait for inherited log handles to
        # close before removing the fixture directory.
        for _ in range(40):
            try:
                self.temporary_directory.cleanup()
                return
            except PermissionError:
                time.sleep(0.05)
        self.temporary_directory.cleanup()

    def write_config(self, **overrides: object) -> None:
        payload: dict[str, object] = {
            "schema_version": "spec-runner-config/v1",
            "repository_path": str(self.repository),
            "target_ref": "HEAD",
            "artifact_root": "artifacts",
            "execution_backend": "deterministic_test",
            "allowed_stages": ["example"],
            "model": {"name": "deterministic-test", "effort": "none"},
            "authorization": {"artifact_roots": ["artifacts"]},
        }
        payload.update(overrides)
        self.config.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def invoke(self, *arguments: str, cwd: Path | None = None) -> tuple[int, dict[str, object]]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(SOURCE_ROOT)
        process = subprocess.run(
            [sys.executable, "-m", "spec_runner.cli", *arguments],
            cwd=cwd or self.root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertTrue(process.stdout, process.stderr)
        return process.returncode, json.loads(process.stdout)

    def start(self, launch_key: str = "launch-001") -> tuple[int, dict[str, object]]:
        return self.invoke(
            "start", "--brief", str(self.brief), "--config", str(self.config), "--control-root", str(self.control_root), "--launch-key", launch_key
        )

    def test_start_is_replayable_and_generates_a_clearly_deterministic_artifact(self) -> None:
        code, first = self.start()
        self.assertEqual(code, 0)
        self.assertTrue(first["created"])
        run = first["run"]
        self.assertEqual(run["backend_kind"], "deterministic_test")
        self.assertEqual(run["state"], "completed")
        self.assertEqual(first["step"]["state"], "archived")
        self.assertEqual(len(first["verification"]), 2)
        self.assertEqual(first["verification"][-1]["stage"], "deterministic_second")
        self.assertEqual(first["verification"][-1]["outcome"], "verified")
        event_types = [event["event_type"] for event in first["events"]]
        for expected in ("run_created", "step_completed", "step_verified", "cleanup_readback"):
            self.assertIn(expected, event_types)
        self.assertIn("next_stage_started", event_types)
        self.assertTrue((self.control_root / "artifacts" / run["run_id"] / "final.json").is_file())
        artifact = self.control_root / "artifacts" / run["run_id"] / "handoff.json"
        self.assertTrue(artifact.is_file())
        self.assertEqual(json.loads(artifact.read_text(encoding="utf-8"))["backend_kind"], "deterministic_test")

        code, second = self.start()
        self.assertEqual(code, 0)
        self.assertFalse(second["created"])
        self.assertEqual(second["run"]["run_id"], run["run_id"])

    def test_same_launch_key_with_changed_input_is_rejected_and_new_key_is_allowed(self) -> None:
        self.assertEqual(self.start()[0], 0)
        self.brief.write_text("changed input", encoding="utf-8")
        code, result = self.start()
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "launch_key_input_conflict")
        code, result = self.start("launch-002")
        self.assertEqual(code, 0)
        self.assertTrue(result["created"])

    def test_answer_is_idempotent_and_cannot_overwrite_a_prior_answer(self) -> None:
        code, started = self.start("answer-001")
        self.assertEqual(code, 0)
        run_id = started["run"]["run_id"]
        arguments = ("answer", "--control-root", str(self.control_root), "--run-id", run_id, "--question-id", "Q1", "--value", "yes")
        code, first = self.invoke(*arguments)
        self.assertEqual(code, 0)
        self.assertTrue(first["accepted"])
        code, second = self.invoke(*arguments)
        self.assertEqual(code, 0)
        self.assertEqual(second["answer"]["value_digest"], first["answer"]["value_digest"])
        code, conflict = self.invoke("answer", "--control-root", str(self.control_root), "--run-id", run_id, "--question-id", "Q1", "--value", "no")
        self.assertEqual(code, 2)
        self.assertEqual(conflict["error"]["code"], "answer_conflict")

    def test_cleanup_only_takeover_does_not_create_an_implementation_run(self) -> None:
        inventory = self.root / "cleanup-takeover.json"
        inventory.write_text(json.dumps({
            "schema_version": "spec-runner-takeover-input/v1",
            "repository_path": str(self.repository),
            "source_threads": [],
            "artifacts": [],
            "facts": {"merged": True, "verification_receipt": {"candidate_sha": "known"}},
        }), encoding="utf-8")
        code, result = self.invoke(
            "takeover", "apply", "--file", str(inventory), "--control-root", str(self.control_root),
            "--takeover-key", "cleanup-only", "--brief", str(self.brief), "--config", str(self.config),
        )
        self.assertEqual(code, 0)
        self.assertEqual(result["action"]["state"], "cleanup_pending")
        self.assertNotIn("runner", result)
        code, status = self.invoke("status", "--control-root", str(self.control_root))
        self.assertEqual(code, 0)
        self.assertEqual(status["runs"], [])

    def test_invalid_inputs_do_not_create_control_resources(self) -> None:
        self.write_config(repository_path=str(self.root / "not-a-repository"))
        code, result = self.start()
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "invalid_repository")
        self.assertFalse(self.control_root.exists())

    def test_status_and_doctor_are_read_only(self) -> None:
        before = digest_tree(self.root)
        code, result = self.invoke("doctor", "--control-root", str(self.control_root))
        self.assertEqual(code, 0)
        self.assertTrue(result["read_only"])
        self.assertEqual(before, digest_tree(self.root))
        code, result = self.invoke("status", "--control-root", str(self.control_root), "--run-id", "missing")
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "unknown_control_root")
        self.assertEqual(before, digest_tree(self.root))

    def test_detached_launch_returns_only_after_runtime_handshake(self) -> None:
        code, result = self.invoke(
            "launch",
            "--brief", str(self.brief),
            "--config", str(self.config),
            "--control-root", str(self.control_root),
            "--launch-key", "detached-001",
        )
        self.assertEqual(code, 0)
        self.assertTrue(result["started"])
        self.assertGreater(result["pid"], 0)
        self.assertEqual(result["run"]["runtime"]["pid"], result["pid"])
        for _ in range(100):
            status_code, status_result = self.invoke(
                "status", "--control-root", str(self.control_root), "--run-id", result["run_id"]
            )
            if status_code == 0 and status_result["run"]["state"] == "completed":
                break
            time.sleep(0.05)
        else:
            self.fail("detached deterministic runner did not complete")

    def test_pause_at_stage_boundary_and_resume_reuses_the_same_run(self) -> None:
        run_id = "12345678-1234-1234-1234-123456789012"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(SOURCE_ROOT)
        environment["SPEC_RUNNER_FAULT_POINT"] = "after_first_artifact"
        child = subprocess.Popen(
            [
                sys.executable, "-m", "spec_runner.cli", "start",
                "--brief", str(self.brief), "--config", str(self.config),
                "--control-root", str(self.control_root), "--launch-key", "pause-001", "--run-id", run_id,
            ],
            cwd=self.root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        ready = self.control_root / "faults" / f"{run_id}.after_first_artifact.ready"
        for _ in range(100):
            if ready.is_file():
                break
            time.sleep(0.05)
        else:
            child.kill()
            self.fail("fault boundary was not reached")
        code, paused_request = self.invoke("pause", "--control-root", str(self.control_root), "--run-id", run_id)
        self.assertEqual(code, 0)
        self.assertTrue(paused_request["accepted"])
        (self.control_root / "faults" / f"{run_id}.after_first_artifact.continue").write_text("continue\n", encoding="utf-8")
        child.communicate(timeout=10)
        code, paused = self.invoke("status", "--control-root", str(self.control_root), "--run-id", run_id)
        self.assertEqual(code, 0)
        self.assertEqual(paused["run"]["state"], "paused")
        code, resumed = self.invoke("resume", "--brief", str(self.brief), "--config", str(self.config), "--control-root", str(self.control_root), "--launch-key", "pause-001")
        self.assertEqual(code, 0)
        self.assertFalse(resumed["created"])
        self.assertEqual(resumed["run"]["run_id"], run_id)
        self.assertEqual(resumed["run"]["state"], "completed")

    def test_invalid_config_and_artifact_escape_are_structured_errors(self) -> None:
        self.config.write_text("{not json", encoding="utf-8")
        code, result = self.start()
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "invalid_config")
        self.assertFalse(self.control_root.exists())
        self.write_config(artifact_root="../outside")
        code, result = self.start()
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "invalid_config")
        self.assertFalse(self.control_root.exists())


if __name__ == "__main__":
    unittest.main()
