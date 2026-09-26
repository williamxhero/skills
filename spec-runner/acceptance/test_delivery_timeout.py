from __future__ import annotations

import subprocess
import json
from unittest.mock import patch

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from spec_runner.errors import RunnerError
from spec_runner.config import RunnerConfig, canonical_repository
from spec_runner.delivery import _git
from spec_runner.github_delivery import GitHubDelivery
from spec_runner.github_tracker import GitHubTracker
from spec_runner.takeover import _git as takeover_git
from spec_runner import cli, workflow
from spec_runner.multi_spec import _run_command


def test_implementation_command_timeout_is_bounded_and_classified(tmp_path: Path):
    with pytest.raises(RunnerError) as error:
        _run_command([sys.executable, "-c", "import time; time.sleep(1)"], cwd=tmp_path, timeout_seconds=1)
    assert error.value.code == "delivery_command_timeout"
    assert error.value.details["timed_out"] is True


def test_invalid_implementation_timeout_is_rejected(tmp_path: Path):
    with pytest.raises(RunnerError) as error:
        _run_command([sys.executable, "-c", "pass"], cwd=tmp_path, timeout_seconds=0)
    assert error.value.code == "delivery_timeout_invalid"


def test_git_reconciliation_timeout_is_structured_and_bounded(tmp_path):
    with patch("spec_runner.workflow.subprocess.run", side_effect=subprocess.TimeoutExpired(["git"], 120)) as run:
        with pytest.raises(RunnerError) as error:
            workflow._git_checked(tmp_path, "status", "--porcelain")

    assert error.value.code == "implementation_git_timeout"
    assert error.value.details["timeout_seconds"] == 120
    assert run.call_args.kwargs["timeout"] == 120


def test_configured_git_timeout_is_validated_and_bound_to_config_digest(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    document = {
        "schema_version": "spec-runner-config/v1",
        "repository_path": str(repository),
        "target_ref": "HEAD",
        "artifact_root": "artifacts",
        "execution_backend": "deterministic_test",
        "allowed_stages": ["implement"],
        "model": {"name": "fake", "effort": "high"},
        "authorization": {"artifact_roots": ["artifacts"]},
        "git": {"timeout_seconds": 7.5},
    }
    config_file = tmp_path / "runner.json"
    config_file.write_text(json.dumps(document), encoding="utf-8")
    with patch("spec_runner.config.canonical_repository", wraps=canonical_repository) as repository_check:
        config = RunnerConfig.from_file(config_file, tmp_path / "control")

    assert config.git_timeout_seconds == 7.5
    assert repository_check.call_args.kwargs["timeout_seconds"] == 7.5
    default_document = dict(document)
    default_document.pop("git")
    default_file = tmp_path / "runner-default.json"
    default_file.write_text(json.dumps(default_document), encoding="utf-8")
    assert config.digest != RunnerConfig.from_file(default_file, tmp_path / "control").digest

    for value in (0, -1, True, float("inf")):
        invalid = dict(document)
        invalid["git"] = {"timeout_seconds": value}
        invalid_file = tmp_path / f"invalid-{str(value).replace('.', '_')}.json"
        invalid_file.write_text(json.dumps(invalid, allow_nan=True), encoding="utf-8")
        with pytest.raises(RunnerError, match="positive finite"):
            RunnerConfig.from_file(invalid_file, tmp_path / "control")


def test_canonical_repository_timeout_is_structured(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    timeout = subprocess.TimeoutExpired(["git"], 0.5)
    with patch("spec_runner.config.subprocess.run", side_effect=timeout) as run:
        with pytest.raises(RunnerError) as error:
            canonical_repository(str(repository), timeout_seconds=0.5)

    assert error.value.code == "repository_git_timeout"
    assert error.value.details["timeout_seconds"] == 0.5
    assert run.call_args.kwargs["timeout"] == 0.5


def test_delivery_git_timeout_is_structured(tmp_path: Path):
    with patch("spec_runner.delivery.subprocess.run", side_effect=subprocess.TimeoutExpired(["git"], 0.25)) as run:
        with pytest.raises(RunnerError) as error:
            _git(tmp_path, "status", timeout_seconds=0.25)

    assert error.value.code == "implementation_git_timeout"
    assert error.value.details["timeout_seconds"] == 0.25
    assert run.call_args.kwargs["timeout"] == 0.25


def test_takeover_git_timeout_is_structured(tmp_path: Path):
    with patch("spec_runner.takeover.subprocess.run", side_effect=subprocess.TimeoutExpired(["git"], 0.25)) as run:
        with pytest.raises(RunnerError) as error:
            takeover_git(tmp_path, "status", timeout_seconds=0.25)

    assert error.value.code == "implementation_git_timeout"
    assert error.value.details["timeout_seconds"] == 0.25
    assert run.call_args.kwargs["timeout"] == 0.25


def test_configured_github_timeout_is_validated_and_bound_to_config_digest(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    document = {
        "schema_version": "spec-runner-config/v1",
        "repository_path": str(repository),
        "target_ref": "HEAD",
        "artifact_root": "artifacts",
        "execution_backend": "deterministic_test",
        "allowed_stages": ["implement"],
        "model": {"name": "fake", "effort": "high"},
        "authorization": {"artifact_roots": ["artifacts"]},
        "github": {
            "repository": "owner/repo",
            "required_checks": ["ci"],
            "receipt_root": "receipts",
            "base": "main",
            "merge_authorized": False,
            "required_approvals": 2,
            "require_branch_protection": True,
            "timeout_seconds": 6.5,
        },
    }
    config_file = tmp_path / "runner-github.json"
    config_file.write_text(json.dumps(document), encoding="utf-8")
    config = RunnerConfig.from_file(config_file, tmp_path / "control")
    assert config.github_timeout_seconds == 6.5
    assert config.github_required_approvals == 2
    assert config.github_require_branch_protection is True

    legacy_document = json.loads(json.dumps(document))
    legacy_document["github"].pop("required_approvals")
    legacy_document["github"].pop("require_branch_protection")
    legacy_file = tmp_path / "runner-github-legacy.json"
    legacy_file.write_text(json.dumps(legacy_document), encoding="utf-8")
    legacy_config = RunnerConfig.from_file(legacy_file, tmp_path / "control")
    assert legacy_config.github_policy_compatible is True
    assert legacy_config.legacy_github_policy_digest != legacy_config.digest

    default_document = json.loads(json.dumps(document))
    default_document["github"].pop("timeout_seconds")
    default_file = tmp_path / "runner-github-default.json"
    default_file.write_text(json.dumps(default_document), encoding="utf-8")
    assert config.digest != RunnerConfig.from_file(default_file, tmp_path / "control").digest

    for value in (0, -1, True, float("inf")):
        invalid = json.loads(json.dumps(document))
        invalid["github"]["timeout_seconds"] = value
        invalid_file = tmp_path / f"invalid-github-{str(value).replace('.', '_')}.json"
        invalid_file.write_text(json.dumps(invalid, allow_nan=True), encoding="utf-8")
        with pytest.raises(RunnerError, match="positive finite"):
            RunnerConfig.from_file(invalid_file, tmp_path / "control")


def test_github_delivery_timeout_is_configurable():
    with patch("spec_runner.github_delivery.subprocess.run",
               side_effect=subprocess.TimeoutExpired(["gh"], 0.75)) as run:
        with pytest.raises(RunnerError) as error:
            GitHubDelivery._gh(
                ["api", "repos/owner/repo/issues", "-f", "body=private body"],
                timeout_seconds=0.75,
            )

    assert error.value.code == "github_delivery_timeout"
    assert error.value.details["timeout_seconds"] == 0.75
    assert error.value.details["args"] == [
        "api", "repos/owner/repo/issues", "-f", "[redacted]",
    ]
    assert "private body" not in json.dumps(error.value.details)
    assert run.call_args.kwargs["timeout"] == 0.75


def test_github_tracker_timeout_is_configurable():
    with patch("spec_runner.github_tracker.subprocess.run",
               side_effect=subprocess.TimeoutExpired(["gh"], 0.75)) as run:
        with pytest.raises(RunnerError) as error:
            GitHubTracker._run_gh(
                ["api", "repos/owner/repo/issues", "-f", "body=private body"],
                timeout_seconds=0.75,
            )

    assert error.value.code == "github_timeout"
    assert error.value.details["timeout_seconds"] == 0.75
    assert error.value.details["args"] == [
        "api", "repos/owner/repo/issues", "-f", "[redacted]",
    ]
    assert "private body" not in json.dumps(error.value.details)
    assert run.call_args.kwargs["timeout"] == 0.75


def test_github_tracker_cli_passes_timeout_to_gh(tmp_path: Path, capsys):
    with patch("spec_runner.github_tracker.subprocess.run",
               side_effect=subprocess.TimeoutExpired(["gh"], 0.625)) as run:
        exit_code = cli.main([
            "tracker", "github-read", "--repository", "owner/repo", "--issue", "1",
            "--timeout-seconds", "0.625",
        ])

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "github_timeout"
    assert run.call_args.kwargs["timeout"] == 0.625
