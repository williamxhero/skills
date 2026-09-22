from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
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
