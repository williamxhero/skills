from __future__ import annotations

import ast
import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
HANDOFF = ROOT / "implement-needs" / "scripts" / "spec_runner_handoff.py"
SKILL = ROOT / "implement-needs" / "SKILL.md"


def load_handoff_module():
    spec = importlib.util.spec_from_file_location("spec_runner_handoff", HANDOFF)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SpecRunnerHandoffTests(unittest.TestCase):
    def test_handoff_has_no_legacy_controller_dependency(self) -> None:
        tree = ast.parse(HANDOFF.read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        )
        source = HANDOFF.read_text(encoding="utf-8")
        self.assertNotIn("controller", imports)
        for forbidden in ("dispatch.py", "advance_runtime", "managed_recovery", "supervisor"):
            self.assertNotIn(forbidden, source)

    def test_new_run_command_is_runner_public_cli(self) -> None:
        module = load_handoff_module()
        with patch.object(module.shutil, "which", return_value=None):
            command = module.runner_command(
                brief=Path("brief.md"),
                config=Path("runner.json"),
                control_root=Path(".spec-runner"),
                launch_key="run-1",
            )
        self.assertEqual(command[:2], [sys.executable, "-m"])
        self.assertEqual(command[2], "spec_runner.cli")
        self.assertEqual(command[command.index("launch"):], [
            "launch",
            "--brief",
            "brief.md",
            "--config",
            "runner.json",
            "--control-root",
            ".spec-runner",
            "--launch-key",
            "run-1",
            "--handshake-timeout",
            "10.0",
        ])
        self.assertNotIn("controller.py", command)
        self.assertNotIn("dispatch.py", command)

    def test_public_controls_are_runner_operations(self) -> None:
        module = load_handoff_module()
        with patch.object(module.shutil, "which", return_value="spec-runner"):
            status = module.runner_command("status", control_root=Path(".runner"), run_id="run-1")
            answer = module.runner_command(
                "answer",
                control_root=Path(".runner"),
                run_id="run-1",
                question_id="q-1",
                value="中文答案",
            )
        self.assertEqual(status, ["spec-runner", "status", "--control-root", ".runner", "--run-id", "run-1"])
        self.assertEqual(answer[-6:], ["--run-id", "run-1", "--question-id", "q-1", "--value", "中文答案"])

    def test_status_requires_a_run_identity(self) -> None:
        module = load_handoff_module()
        with self.assertRaisesRegex(ValueError, "run_id is required"):
            module.runner_command("status", control_root=Path(".runner"))

    def test_legacy_execution_operations_are_not_public(self) -> None:
        module = load_handoff_module()
        for operation in ("takeover", "migration", "legacy"):
            with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, "unsupported handoff operation"):
                module.runner_command(operation, control_root=Path(".runner"))

    def test_runner_response_is_checked_and_utf8_is_preserved(self) -> None:
        module = load_handoff_module()
        completed = type("Completed", (), {"returncode": 0, "stdout": '{"schema_version":"spec-runner-cli/v1","run":{"run_id":"r-1","state":"running","current_step":"codex_grill","brief":"中文需求"},"step":{"step_name":"codex_grill"},"verification":[{"id":"e-1"}]}'.encode("utf-8"), "stderr": b""})()
        with patch.object(module, "runner_command", return_value=["spec-runner", "status"]), patch.object(module.subprocess, "run", return_value=completed):
            code, payload = module.invoke_public_runner("status", control_root=Path(".runner"), run_id="r-1")
        self.assertEqual(code, 0)
        self.assertEqual(payload["handoff_contract_version"], "implement-needs-handoff/v2")
        self.assertEqual(payload["run"]["brief"], "中文需求")
        self.assertEqual(payload["run_id"], "r-1")
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["phase"], "codex_grill")
        self.assertEqual(payload["next_action"], "codex_grill")
        self.assertEqual(payload["evidence_refs"], [{"id": "e-1"}])

    def test_runner_rejection_is_a_structured_blocker(self) -> None:
        module = load_handoff_module()
        completed = type("Completed", (), {"returncode": 2, "stdout": b'{"schema_version":"spec-runner-cli/v1","ok":false,"error":{"code":"unknown_run","message":"missing"}}', "stderr": b""})()
        with patch.object(module, "runner_command", return_value=["spec-runner", "status"]), patch.object(module.subprocess, "run", return_value=completed):
            code, payload = module.invoke_public_runner("status", control_root=Path(".runner"), run_id="r-1")
        self.assertEqual(code, 2)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reason"]["code"], "unknown_run")

    def test_contract_mismatch_is_a_blocker(self) -> None:
        module = load_handoff_module()
        completed = type("Completed", (), {"returncode": 0, "stdout": b'{"schema_version":"old-cli/v0"}', "stderr": b""})()
        with patch.object(module, "runner_command", return_value=["spec-runner", "status"]), patch.object(module.subprocess, "run", return_value=completed):
            code, payload = module.invoke_public_runner("status", control_root=Path(".runner"), run_id="r-1")
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reason"]["code"], "runner_contract_mismatch")

    def test_nonzero_runner_exit_cannot_be_reported_as_completed(self) -> None:
        module = load_handoff_module()
        completed = type("Completed", (), {"returncode": 3, "stdout": b'{"schema_version":"spec-runner-cli/v1","status":"completed"}', "stderr": b"runner failed"})()
        with patch.object(module, "runner_command", return_value=["spec-runner", "status"]), patch.object(module.subprocess, "run", return_value=completed):
            code, payload = module.invoke_public_runner("status", control_root=Path(".runner"), run_id="r-1")
        self.assertEqual(code, 3)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reason"]["code"], "runner_exit_conflicts_with_completion")

    def test_launch_identity_mismatch_is_a_blocker(self) -> None:
        module = load_handoff_module()
        completed = type("Completed", (), {"returncode": 0, "stdout": b'{"schema_version":"spec-runner-cli/v1","run":{"run_id":"r-1","launch_key":"other"}}', "stderr": b""})()
        with patch.object(module, "runner_command", return_value=["spec-runner", "launch"]), patch.object(module.subprocess, "run", return_value=completed):
            code, payload = module.invoke_public_runner(
                "launch",
                brief=Path("brief.md"),
                config=Path("runner.json"),
                control_root=Path(".runner"),
                launch_key="expected",
            )
        self.assertEqual(code, 1)
        self.assertEqual(payload["reason"]["code"], "runner_launch_identity_mismatch")

    def test_runner_unavailable_is_not_verified(self) -> None:
        module = load_handoff_module()
        with patch.object(module, "runner_command", return_value=["missing-spec-runner"]), patch.object(module.subprocess, "run", side_effect=FileNotFoundError("missing")):
            code, payload = module.invoke_public_runner("status", control_root=Path(".runner"), run_id="r-1")
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "not_verified")
        self.assertEqual(payload["reason"]["code"], "runner_unavailable")

    def test_clean_skill_install_contains_only_the_public_entry(self) -> None:
        skill_root = ROOT / "implement-needs"
        with tempfile.TemporaryDirectory(prefix="implement-needs-installed-") as temporary:
            installed = Path(temporary) / "implement-needs"
            shutil.copytree(skill_root, installed, ignore=shutil.ignore_patterns(".scratch", "__pycache__", ".pytest_cache"))
            self.assertEqual(
                {path.relative_to(installed).as_posix() for path in installed.rglob("*") if path.is_file()},
                {"SKILL.md", "agents/openai.yaml", "scripts/spec_runner_handoff.py", "tests/test_spec_runner_handoff.py"},
            )
            self.assertIn("implement-needs-handoff/v2", (installed / "SKILL.md").read_text(encoding="utf-8"))
            wrapper = installed / "scripts" / "spec_runner_handoff.py"
            self.assertIn("implement-needs-handoff/v2", wrapper.read_text(encoding="utf-8"))
            metadata = (installed / "agents" / "openai.yaml").read_text(encoding="utf-8")
            self.assertIn("public Spec Runner", metadata)

    def test_skill_routes_new_work_and_rejects_legacy_execution(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("scripts/spec_runner_handoff.py", text)
        self.assertIn("single user-facing entry", text)
        self.assertIn("historical\nevidence only", text)
        self.assertNotIn("takeover-file", text)
        self.assertNotIn("legacy inspect", text)
        self.assertNotIn("dispatch.py", text)
        self.assertIn("must\nbe rejected", text)


if __name__ == "__main__":
    unittest.main()
