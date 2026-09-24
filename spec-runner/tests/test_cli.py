from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PACKAGE_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))


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
        self.assertTrue(first["operations"])
        self.assertTrue(first["steps"])
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

        code, status = self.invoke("status", "--control-root", str(self.control_root), "--run-id", run["run_id"])
        self.assertEqual(code, 0)
        self.assertEqual(status["run"]["state"], "completed")
        self.assertEqual(status["verification"], first["verification"])

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
        self.assertEqual(result["action"]["state"], "reverify_delivery")
        self.assertNotIn("runner", result)
        code, status = self.invoke("status", "--control-root", str(self.control_root))
        self.assertEqual(code, 0)
        self.assertEqual(status["runs"], [])

    def test_resume_takeover_binds_runner_to_takeover_identity_and_replays_same_run(self) -> None:
        inventory = self.root / "resume-takeover.json"
        inventory.write_text(json.dumps({
            "schema_version": "spec-runner-takeover-input/v1",
            "repository_path": str(self.repository),
            "source_threads": [],
            "artifacts": [],
            "facts": {"requirements": ["continue delivery"], "tracker": True, "partial_code": True},
        }), encoding="utf-8")

        arguments = (
            "takeover", "apply", "--file", str(inventory), "--control-root", str(self.control_root),
            "--takeover-key", "resume-identity", "--brief", str(self.brief), "--config", str(self.config),
        )
        code, first = self.invoke(*arguments)
        self.assertEqual(code, 0, first)
        self.assertEqual(first["action"]["state"], "resume_delivery")
        run_id = first["runner"]["run"]["run_id"]
        context_path = self.control_root / "artifacts" / run_id / "takeover-context.json"
        self.assertTrue(context_path.is_file())
        context = json.loads(context_path.read_text(encoding="utf-8"))
        self.assertEqual(context["schema_version"], "spec-runner-takeover-context/v1")
        self.assertEqual(context["run_id"], run_id)
        self.assertEqual(context["takeover_key"], "resume-identity")
        self.assertEqual(context["record"]["takeover_key"], "resume-identity")

        code, second = self.invoke(*arguments)
        self.assertEqual(code, 0, second)
        self.assertFalse(second["created"])
        self.assertEqual(second["runner"]["run"]["run_id"], run_id)
        code, status = self.invoke("status", "--control-root", str(self.control_root))
        self.assertEqual(code, 0)
        self.assertEqual([item["run_id"] for item in status["runs"]], [run_id])

    def test_start_with_unknown_takeover_record_fails_closed(self) -> None:
        from spec_runner.workflow import start
        from spec_runner.errors import RunnerError

        with self.assertRaises(RunnerError) as raised:
            start(
                brief_file=self.brief,
                config_file=self.config,
                control_root=self.control_root,
                launch_key="missing-takeover",
                takeover_key="does-not-exist",
            )
        self.assertEqual(raised.exception.code, "takeover_record_missing")
        code, status = self.invoke("status", "--control-root", str(self.control_root))
        self.assertEqual(code, 0)
        self.assertEqual(status["runs"], [])

    def test_thread_takeover_reobserves_after_a_durable_handover(self) -> None:
        import spec_runner.cli as cli

        class HandoverAdapter:
            interrupt_calls = 0

            def read_thread(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
                return {
                    "schema_version": "spec-runner-sdk-thread-inspection/v1",
                    "thread_id": thread_id,
                    "thread_status": "active",
                    "active_flags": ["turn"],
                    "thread": {"forked_from_id": None},
                    "business_items": [{"turn_id": "turn-1", "item": {"type": "userMessage", "id": "item-1", "content": [{"type": "text", "text": "finish"}]}}],
                    "completeness": {"state": "complete", "reasons": []},
                }

            def interrupt_thread(self, *, thread_id: str, repository_path: Path) -> dict[str, object]:
                HandoverAdapter.interrupt_calls += 1
                return {
                    "schema_version": "spec-runner-sdk-thread-interrupt/v1",
                    "thread_id": thread_id,
                    "accepted": True,
                    "source_writer_state": "stopped",
                    "dispatcher_state": "quiesced",
                    "readback": {"source_thread_id": thread_id, "observed_status": "completed"},
                    "evidence_limits": {
                        "source_stop_confirmed": True,
                        "dispatcher_quiesced": True,
                        "ownership_transferred": True,
                    },
                }

        output = io.StringIO()
        with patch.object(cli, "CodexAdapter", HandoverAdapter), redirect_stdout(output):
            code = cli.main([
                "takeover", "apply", "--thread-id", "source-thread", "--repository", str(self.repository),
                "--scope", ".", "--handover-policy", "interrupt_then_takeover",
                "--control-root", str(self.control_root), "--takeover-key", "handover-reobserve",
            ])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["action"]["state"], "resume_delivery")
        self.assertEqual(result["frontier"]["state"], "planned")
        self.assertEqual(result["report"]["next_state"], "adopted_ready")
        self.assertEqual(result["report"]["adopted_threads"][0]["state"], "released")
        self.assertTrue(any("handover:reobserved:" in item["event_key"] for item in result["transitions"]))

        second_output = io.StringIO()
        with patch.object(cli, "CodexAdapter", HandoverAdapter), redirect_stdout(second_output):
            second_code = cli.main([
                "takeover", "apply", "--thread-id", "source-thread", "--repository", str(self.repository),
                "--scope", ".", "--handover-policy", "interrupt_then_takeover",
                "--control-root", str(self.control_root), "--takeover-key", "handover-reobserve",
            ])
        self.assertEqual(second_code, 0)
        self.assertEqual(HandoverAdapter.interrupt_calls, 1)
        self.assertNotIn("handover", json.loads(second_output.getvalue()))

    def test_thread_takeover_requires_an_explicit_scope_before_sdk_access(self) -> None:
        code, result = self.invoke(
            "takeover", "inspect", "--thread-id", "source-thread", "--repository", str(self.repository)
        )
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "takeover_scope_required")

    def test_release_build_writes_a_valid_report_from_evidence_files(self) -> None:
        subject = self.root / "release-subject.json"
        subject.write_text(json.dumps({
            "runner_version": "0.1.0",
            "build_digest": "build-1",
            "config_contract": "spec-runner-config/v1",
            "sdk_runtime": {"package": "openai-codex", "version": "0.155.1"},
            "matt_lock_digest": "matt-lock",
            "contract_digests": {"prompt_templates": "prompts", "schemas": "schemas", "validators": "validators"},
            "os": "windows-11",
            "trust_mode": "deny_all",
            "scenario_version": "sr-07/v1",
        }), encoding="utf-8")
        deterministic = self.root / "deterministic-evidence.json"
        deterministic.write_text(json.dumps({
            "evidence_kind": "deterministic",
            "verified": True,
            "outcome": "passed",
            "report_digest": "det-1",
        }), encoding="utf-8")
        local_git = self.root / "local-git-evidence.json"
        local_git.write_text(json.dumps({
            "evidence_kind": "local_git",
            "verified": True,
            "outcome": "passed",
            "merge_sha": "merge-1",
        }), encoding="utf-8")
        output = self.root / "release-report.json"
        code, result = self.invoke(
            "diagnose", "release-build", "--subject", str(subject), "--evidence", str(deterministic),
            "--evidence", str(local_git), "--output", str(output),
        )
        self.assertEqual(code, 0)
        self.assertTrue(result["eligible"])
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["schema_version"], "spec-runner-release-report/v1")
        code, validated = self.invoke("diagnose", "release-report", "--file", str(output))
        self.assertEqual(code, 0)
        self.assertTrue(validated["eligible"])

    def test_release_build_marks_required_unverified_evidence_ineligible(self) -> None:
        subject = self.root / "release-subject.json"
        subject.write_text(json.dumps({
            "runner_version": "0.1.0",
            "build_digest": "build-1",
            "config_contract": "spec-runner-config/v1",
            "sdk_runtime": {"package": "openai-codex", "version": "0.155.1"},
            "matt_lock_digest": "matt-lock",
            "contract_digests": {"prompt_templates": "prompts", "schemas": "schemas", "validators": "validators"},
            "os": "windows-11",
            "trust_mode": "deny_all",
            "scenario_version": "sr-07/v1",
        }), encoding="utf-8")
        github = self.root / "github-evidence.json"
        github.write_text(json.dumps({
            "evidence_kind": "live_github",
            "verified": False,
            "outcome": "not_verified",
            "reason": "no authorized sandbox",
        }), encoding="utf-8")
        output = self.root / "release-report.json"
        code, result = self.invoke(
            "diagnose", "release-build", "--subject", str(subject), "--evidence", str(github),
            "--required-kind", "live_github", "--output", str(output),
        )
        self.assertEqual(code, 0)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["required_not_passed"], ["live_github"])

    def test_public_cli_runs_a_local_delivery_spec(self) -> None:
        subprocess.run(["git", "config", "user.email", "runner@example.invalid"], cwd=self.repository, check=True)
        subprocess.run(["git", "config", "user.name", "Spec Runner"], cwd=self.repository, check=True)
        (self.repository / "state.txt").write_text("base\n", encoding="utf-8")
        (self.repository / "emit.py").write_text(
            "import json, subprocess, sys\nfrom pathlib import Path\np=Path('state.txt')\np.write_text(p.read_text() + 'SR-CLI\\n')\nsubprocess.run(['git','add','state.txt'], check=True)\nsubprocess.run(['git','commit','-qm','SR-CLI'], check=True)\nsha=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip()\nPath(sys.argv[1]).parent.mkdir(parents=True, exist_ok=True)\nPath(sys.argv[1]).write_text(json.dumps({'schema_version':'spec-runner-review-result/v1','candidate_sha':sha,'acceptance_version':'a1','findings':[]}))\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=self.repository, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.repository, check=True)
        review_path = (self.control_root / "reviews/SR-CLI.json").resolve()
        plan = {
            "schema_version": "spec-runner-delivery-plan/v1",
            "specs": [{
                "key": "SR-CLI", "acceptance_version": "a1", "acceptance": ["A1"],
                "implementation": [[sys.executable, "emit.py", str(review_path)]],
                "checks": [{"command": [sys.executable, "-c", "from pathlib import Path; assert 'SR-CLI' in Path('state.txt').read_text()"], "acceptance": ["A1"]}],
                "review_file": "reviews/SR-CLI.json",
            }],
        }
        plan_path = self.control_root / "delivery-plan.json"
        self.control_root.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        target_ref = "refs/heads/" + subprocess.check_output(["git", "-C", str(self.repository), "branch", "--show-current"], text=True).strip()
        code, result = self.invoke(
            "delivery", "run", "--plan", str(plan_path), "--repository", str(self.repository),
            "--workspace-root", str(self.root / "workspaces"), "--control-root", str(self.control_root),
            "--run-id", "cli-delivery", "--target-ref", target_ref,
        )
        self.assertEqual(code, 0, result)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["completed_specs"], ["SR-CLI"])
        # The same plan can be launched through the normal Runner entry and
        # therefore uses the run lease, SQLite status, and delivery receipt.
        self.write_config(target_ref=target_ref, delivery={"plan": "delivery-plan.json"})
        code, started = self.invoke(
            "start", "--brief", str(self.brief), "--config", str(self.config),
            "--control-root", str(self.control_root), "--launch-key", "delivery-start",
        )
        self.assertEqual(code, 0, started)
        self.assertTrue(started["created"])
        self.assertEqual(started["run"]["state"], "completed")
        self.assertEqual(started["run"]["current_step"], "delivery_plan")

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

    def test_detached_launch_rejects_an_unbounded_or_nonpositive_handshake_timeout(self) -> None:
        code, result = self.invoke(
            "launch",
            "--brief", str(self.brief),
            "--config", str(self.config),
            "--control-root", str(self.control_root),
            "--launch-key", "detached-invalid-timeout",
            "--handshake-timeout", "0",
        )
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "launch_timeout_invalid")
        self.assertFalse(self.control_root.exists())

    def test_detached_launch_resolves_relative_inputs_before_child_cwd_changes(self) -> None:
        code, result = self.invoke(
            "launch",
            "--brief", "brief.md",
            "--config", "runner.json",
            "--control-root", "控制 root",
            "--launch-key", "detached-relative-001",
            cwd=self.root,
        )
        self.assertEqual(code, 0, result)
        self.assertTrue(result["started"])
        self.assertGreater(result["pid"], 0)
        self.assertEqual(result["run"]["runtime"]["pid"], result["pid"])
        for _ in range(100):
            status_code, status_result = self.invoke(
                "status", "--control-root", "控制 root", "--run-id", result["run_id"], cwd=self.root
            )
            if status_code == 0 and status_result["run"]["state"] == "completed":
                break
            time.sleep(0.05)
        else:
            self.fail("detached deterministic runner with relative inputs did not complete")

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
