from __future__ import annotations

import ast
import importlib.util
import sys
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

    def test_runner_response_is_checked_and_utf8_is_preserved(self) -> None:
        module = load_handoff_module()
        completed = type("Completed", (), {"returncode": 0, "stdout": '{"schema_version":"spec-runner-cli/v1","run":{"run_id":"r-1","brief":"中文需求"}}'.encode("utf-8"), "stderr": b""})()
        with patch.object(module, "runner_command", return_value=["spec-runner", "status"]), patch.object(module.subprocess, "run", return_value=completed):
            code, payload = module.invoke_public_runner("status", control_root=Path(".runner"), run_id="r-1")
        self.assertEqual(code, 0)
        self.assertEqual(payload["handoff_contract_version"], "implement-needs-handoff/v2")
        self.assertEqual(payload["run"]["brief"], "中文需求")

    def test_contract_mismatch_is_a_blocker(self) -> None:
        module = load_handoff_module()
        completed = type("Completed", (), {"returncode": 0, "stdout": b'{"schema_version":"old-cli/v0"}', "stderr": b""})()
        with patch.object(module, "runner_command", return_value=["spec-runner", "status"]), patch.object(module.subprocess, "run", return_value=completed):
            code, payload = module.invoke_public_runner("status", control_root=Path(".runner"), run_id="r-1")
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reason"]["code"], "runner_contract_mismatch")

    def test_skill_routes_new_work_and_rejects_legacy_execution(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("scripts/spec_runner_handoff.py", text)
        self.assertIn("single user-facing entry", text)
        self.assertIn("historical\nevidence only", text)
        self.assertNotIn("takeover-file", text)
        self.assertNotIn("legacy inspect", text)
        self.assertNotIn("dispatch.py", text)


if __name__ == "__main__":
    unittest.main()
