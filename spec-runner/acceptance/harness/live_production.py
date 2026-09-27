"""Run one authorized production acceptance namespace through the public CLI.

The harness owns only run setup and output capture. Spec Runner owns planning,
workers, checks, review, delivery, cleanup, and external resources.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _read_manifest(path: Path) -> tuple[dict[str, object], Path, Path]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "spec-runner-acceptance-manifest/v1":
        raise ValueError("invalid acceptance manifest")
    marker = manifest.get("run_marker")
    repository = manifest.get("repository_path")
    if not isinstance(marker, str) or not isinstance(repository, str):
        raise ValueError("manifest lacks run identity")
    if manifest.get("repository") != "williamxhero/skills":
        raise ValueError("unexpected acceptance repository")
    return manifest, Path(repository), path.parent


def _skill_roots() -> list[str]:
    roots = [
        Path.home() / ".agents" / "skills",
        Path.home() / ".codex" / "skills",
    ]
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        roots.insert(0, Path(codex_home) / "skills")
    return [os.fspath(root.resolve()) for root in roots if root.is_dir()]


def prepare_inputs(*, manifest_path: Path, scenario: Path, github: bool) -> tuple[Path, Path, Path]:
    manifest, repository, runtime = _read_manifest(manifest_path)
    marker = str(manifest["run_marker"])
    control = runtime / "control"
    control.mkdir(parents=True, exist_ok=True)
    brief = control / "brief.md"
    brief.write_text(scenario.read_text(encoding="utf-8").replace("<run-marker>", marker), encoding="utf-8", newline="\n")
    skills = control / "skills.json"
    skills.write_text(json.dumps({
        "schema_version": "spec-runner-live-skills/v1",
        "roots": _skill_roots(),
        "skills": {
            "grill": {"name": "grilling"},
            "to-spec": {"name": "to-spec"},
            "to-tickets": {"name": "to-tickets"},
            "implement": {"name": "implement-spec"},
            "review": {"name": "code-review"},
        },
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    fixture = f"spec-runner/acceptance/fixture-app/{marker}"
    config: dict[str, object] = {
        "schema_version": "spec-runner-config/v1",
        "repository_path": os.fspath(repository),
        "target_ref": "refs/heads/master",
        "artifact_root": "artifacts",
        "execution_backend": "codex_sdk",
        "allowed_stages": ["grill", "planning", "to-tickets", "implement", "review", "repair", "production"],
        "model": {"name": "gpt-5.5", "effort": "high"},
        "authorization": {"artifact_roots": ["artifacts"]},
        "skills": {"config": "skills.json", "roots": _skill_roots()},
        "workflow": {
            "mode": "production",
            "acceptance": {
                "ids": ["FIXTURE_TESTS", "FIXTURE_SCOPE"],
                "checks": [
                    {"command": [sys.executable, "-m", "pytest", fixture, "-q"], "acceptance": ["FIXTURE_TESTS"], "timeout_seconds": 180},
                    {"command": [sys.executable, "-c", "import subprocess; base='" + str(manifest["base_sha"]) + "'; prefix='" + fixture + "/'; paths=set(); paths.update(subprocess.check_output(['git','diff','--name-only','-z',base,'HEAD']).decode().split('\\0')); paths.update(subprocess.check_output(['git','diff','--name-only','-z','HEAD']).decode().split('\\0')); paths.update(subprocess.check_output(['git','ls-files','--others','--exclude-standard','-z']).decode().split('\\0')); paths={p for p in paths if p}; assert all(p.startswith(prefix) for p in paths), sorted(paths)"] , "acceptance": ["FIXTURE_SCOPE"], "timeout_seconds": 60, "scope": "candidate"},
                ],
                "write_scope": [fixture],
            },
        },
    }
    if github:
        config["github"] = {
            "repository": "williamxhero/skills",
            "required_checks": ["contract (ubuntu-latest)", "contract (windows-latest)"],
            "receipt_root": "github-receipts",
            "base": "refs/heads/master",
            "merge_authorized": True,
            "required_approvals": 0,
            "require_branch_protection": False,
            "timeout_seconds": 180,
        }
    config_path = control / "runner.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return brief, config_path, control


def run(*, manifest_path: Path, scenario: Path, python_executable: Path, github: bool) -> int:
    manifest, repository, runtime = _read_manifest(manifest_path)
    brief, config, control = prepare_inputs(manifest_path=manifest_path, scenario=scenario, github=github)
    command = [
        os.fspath(python_executable), "-m", "spec_runner.cli", "start",
        "--brief", os.fspath(brief), "--config", os.fspath(config),
        "--control-root", os.fspath(control), "--launch-key", str(manifest["run_marker"]),
    ]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(command, cwd=repository, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = runtime / "runner-start.jsonl"
    output.write_text(result.stdout + result.stderr, encoding="utf-8", newline="\n")
    print(json.dumps({"exit_code": result.returncode, "output": os.fspath(output), "control_root": os.fspath(control)}, ensure_ascii=False))
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--github", action="store_true")
    args = parser.parse_args()
    try:
        return run(manifest_path=args.manifest, scenario=args.scenario, python_executable=args.python, github=args.github)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
