"""Build, cold-install, and replay the public CLI from one exact wheel."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import venv
from pathlib import Path
from typing import Any


REPOSITORY = Path(__file__).resolve().parents[3]
ACCEPTANCE = REPOSITORY / "spec-runner" / "acceptance"
EXPECTED_REMOTE = "https://github.com/williamxhero/skills.git"


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {Path(command[0]).name} {command[1]}")
    return result


def _json_command(command: list[str], *, cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    result = _run(command, cwd=cwd, env=env)
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"command did not return JSON: {Path(command[0]).name} {command[1]}") from exc
    if not isinstance(document, dict):
        raise RuntimeError("command JSON result must be an object")
    return document


def _python_in(environment: Path) -> Path:
    executable = "python.exe" if os.name == "nt" else "python"
    scripts = "Scripts" if os.name == "nt" else "bin"
    return environment / scripts / executable


def _isolated_environment(environment: Path) -> dict[str, str]:
    result = os.environ.copy()
    result.pop("PYTHONPATH", None)
    result.pop("PYTHONHOME", None)
    result["PYTHONNOUSERSITE"] = "1"
    return result


def replay(repository: Path) -> dict[str, object]:
    repository = repository.resolve(strict=True)
    if repository != REPOSITORY.resolve(strict=True):
        raise ValueError("repository must be the checked-out skills repository")
    remote = subprocess.run(
        ["git", "-C", os.fspath(repository), "remote", "get-url", "origin"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    if remote.removesuffix(".git") != EXPECTED_REMOTE.removesuffix(".git"):
        raise ValueError("origin must be williamxhero/skills")
    commit = subprocess.run(
        ["git", "-C", os.fspath(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()

    prepared = _json_command(
        [sys.executable, os.fspath(ACCEPTANCE / "harness" / "prepare.py"), "prepare", "--repository", os.fspath(repository)],
        cwd=repository,
        env=os.environ.copy(),
    )
    manifest_path = Path(str(prepared["manifest"]))
    marker = str(prepared["run"]["run_marker"])
    run_root = manifest_path.parent
    build_root = run_root / "build"
    wheelhouse = build_root / "wheel"
    environment_path = build_root / "venv"
    outsider = build_root / "non-source cwd with spaces"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    outsider.mkdir(parents=True, exist_ok=True)

    _run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", os.fspath(wheelhouse), os.fspath(repository / "spec-runner")],
        cwd=outsider,
    )
    wheels = list(wheelhouse.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("wheel build did not produce exactly one artifact")
    wheel = wheels[0].resolve()
    wheel_sha = hashlib.sha256(wheel.read_bytes()).hexdigest()

    venv.EnvBuilder(with_pip=True, clear=False).create(environment_path)
    isolated_python = _python_in(environment_path).resolve(strict=True)
    isolated_env = _isolated_environment(environment_path)
    _run([os.fspath(isolated_python), "-m", "pip", "install", "--disable-pip-version-check", os.fspath(wheel)], cwd=outsider, env=isolated_env)

    version = _run([os.fspath(isolated_python), "-m", "spec_runner.cli", "--version"], cwd=outsider, env=isolated_env).stdout.strip()
    package = _json_command(
        [os.fspath(isolated_python), "-m", "spec_runner.cli", "diagnose", "package", "--wheel", os.fspath(wheel)],
        cwd=outsider,
        env=isolated_env,
    )
    installed = _run(
        [os.fspath(isolated_python), "-c", "import spec_runner; print(spec_runner.__file__)"],
        cwd=outsider,
        env=isolated_env,
    ).stdout.strip()
    installed_path = Path(installed).resolve(strict=True)
    source_tree = (repository / "spec-runner" / "src").resolve()
    try:
        installed_path.relative_to(source_tree)
    except ValueError:
        pass
    else:
        raise RuntimeError("installed import resolved inside the source repository")
    if "site-packages" not in installed_path.parts and "dist-packages" not in installed_path.parts:
        raise RuntimeError("installed import did not resolve from the isolated environment")
    if version != "0.1.0" or package.get("outcome") != "verified":
        raise RuntimeError("installed version or wheel diagnostic did not pass")

    seed = f"{marker}-fault-replay"
    replays = []
    for number in (1, 2):
        outcome = _json_command(
            [os.fspath(isolated_python), "-m", "spec_runner.cli", "fault", "run", "--seed", seed],
            cwd=outsider,
            env=isolated_env,
        )
        cases = outcome.get("cases")
        if outcome.get("passed") is not True or not isinstance(cases, list) or len(cases) != 10:
            raise RuntimeError(f"installed CLI fault replay {number} did not pass all ten cases")
        replays.append({
            "number": number,
            "seed": seed,
            "schema_version": outcome.get("schema_version"),
            "passed": True,
            "cases": len(cases),
            "report_digest": outcome.get("report_digest"),
            "case_outcomes": [
                {"id": item.get("id"), "passed": item.get("passed")}
                for item in cases if isinstance(item, dict)
            ],
        })
    if replays[0]["report_digest"] != replays[1]["report_digest"] or replays[0]["case_outcomes"] != replays[1]["case_outcomes"]:
        raise RuntimeError("the two deterministic installed CLI replays differed")

    return {
        "schema_version": "spec-runner-installed-wheel-replay/v1",
        "run_marker": marker,
        "candidate": {
            "commit": commit,
            "wheel": wheel.name,
            "sha256": wheel_sha,
            "archive_digest": package.get("archive_digest"),
            "package_diagnostic": package.get("outcome"),
            "source_unavailable": True,
            "pythonpath_cleared": "PYTHONPATH" not in isolated_env,
        },
        "environment": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "cwd": os.fspath(outsider),
            "installed_import": os.fspath(installed_path),
            "cwd_outside_source": not outsider.resolve().is_relative_to(source_tree),
            "sdk": f"openai-codex=={importlib.metadata.version('openai-codex')}",
        },
        "replays": replays,
        "replay_comparison": "identical report digest and per-case outcomes",
        "outcome": "passed",
        "unverified_scenarios": [
            "live Codex production workflow",
            "live GitHub publication and merge queue",
            "source-thread takeover",
            "six-SPEC release-train L4",
            "final connected L5",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=REPOSITORY)
    args = parser.parse_args()
    report = replay(args.repository)
    marker = str(report["run_marker"])
    report_path = ACCEPTANCE / "reports" / f"{marker}-installed-wheel.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"outcome": report["outcome"], "report": os.fspath(report_path), "candidate": report["candidate"], "replays": report["replays"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
