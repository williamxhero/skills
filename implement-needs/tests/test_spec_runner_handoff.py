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
        ])
        self.assertNotIn("controller.py", command)
        self.assertNotIn("dispatch.py", command)

    def test_github_takeover_runs_discover_then_apply(self) -> None:
        module = load_handoff_module()
        with patch.object(module.shutil, "which", return_value=None):
            discover = module.takeover_discovery_command(
                repository="owner/repo", issue=42, workspace=Path("repo"),
                target_ref="refs/heads/main", control_root=Path("control"),
                takeover_key="takeover-42", output=Path("snapshot.json"),
                artifact_roots=[Path("artifacts")], required_checks=["ci"],
            )
            apply = module.takeover_apply_command(
                discovery=Path("snapshot.json"), control_root=Path("control"),
                takeover_key="takeover-42", brief=Path("brief.md"),
                config=Path("runner.json"), launch_key="launch-42",
            )
        self.assertEqual(discover[2:4], ["spec_runner.cli", "takeover"])
        self.assertIn("--issue", discover)
        self.assertIn("42", discover)
        self.assertIn("--required-check", discover)
        self.assertEqual(apply[2:4], ["spec_runner.cli", "takeover"])
        self.assertIn("--discovery", apply)
        self.assertIn("snapshot.json", apply)

    def test_skill_routes_new_work_and_describes_legacy_as_compatibility(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("scripts/spec_runner_handoff.py", text)
        self.assertIn("New requests enter the Runner directly", text)
        self.assertIn("Existing legacy run", text)
        self.assertIn("read-only `legacy inspect`", text)
        self.assertIn("takeover discover", text)
        self.assertIn("Issue-backed ticket graph skips to-tickets", text)
        self.assertNotIn("qualification_gate.py", text)
        self.assertNotIn("scripts/dispatch.py", text)


if __name__ == "__main__":
    unittest.main()
