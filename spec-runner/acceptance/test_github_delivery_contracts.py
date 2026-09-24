from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from spec_runner.errors import RunnerError
from spec_runner.github_delivery import GitHubDelivery


def test_check_runs_are_paginated_and_pending_is_waiting_not_failure():
    calls: list[list[str]] = []

    def runner(args: list[str]) -> str:
        calls.append(args)
        if "status" in args[-1]:
            return json.dumps({"sha": "abc", "state": "pending", "statuses": []})
        return json.dumps([{"check_runs": [
            {"name": "ci", "status": "in_progress", "conclusion": None, "head_sha": "abc", "started_at": "2026-01-01T00:00:00Z"},
        ]}, {"check_runs": [
            {"name": "lint", "status": "completed", "conclusion": "success", "head_sha": "abc"},
        ]}])

    result = GitHubDelivery(runner=runner).checks(repository="owner/repo", candidate_sha="abc", required=["ci", "lint"])
    assert result["pending"] == ["ci"]
    assert result["failed"] == []
    assert result["ready"] is False
    assert "--paginate" in calls[0] and "--slurp" in calls[0]


def test_check_runs_reject_completed_neutral_and_wrong_sha():
    def runner(args: list[str]) -> str:
        if "status" in args[-1]:
            return json.dumps({"sha": "abc", "state": "failure", "statuses": []})
        return json.dumps({"check_runs": [
            {"name": "ci", "status": "completed", "conclusion": "neutral", "head_sha": "abc"},
            {"name": "lint", "status": "completed", "conclusion": "success", "head_sha": "old"},
        ]})

    result = GitHubDelivery(runner=runner).checks(repository="owner/repo", candidate_sha="abc", required=["ci", "lint"])
    assert result["failed"] == ["ci"]
    assert result["wrong_sha"] == ["lint"]
    assert result["ready"] is False


@pytest.mark.parametrize("pages", ["{}", "[{}]"])
def test_malformed_pr_pagination_is_rejected(pages):
    with pytest.raises(Exception):
        GitHubDelivery(runner=lambda args: pages).create_or_adopt_pr(
            repository="owner/repo", head="branch", base="main", candidate_sha="abc",
            body="body", operation_id="op", receipt_root=Path("."),
        )


def test_pr_listing_with_non_object_entry_fails_before_create(tmp_path):
    calls: list[list[str]] = []

    def runner(args: list[str]) -> str:
        calls.append(args)
        return '[[{"number": 1}, "not-an-object"]]'

    with pytest.raises(RunnerError) as error:
        GitHubDelivery(runner=runner).create_or_adopt_pr(
            repository="owner/repo", head="branch", base="main", candidate_sha="abc",
            body="body", operation_id="op", receipt_root=tmp_path,
        )

    assert error.value.code == "github_pr_readback_incomplete"
    assert len(calls) == 1
    assert "POST" not in calls[0]


@pytest.mark.parametrize("code", [
    "github_auth",
    "github_forbidden",
    "github_not_found",
    "github_rate_limited",
    "github_rejected",
    "github_delivery_unavailable",
])
def test_definitive_pr_create_failure_keeps_its_classification(tmp_path, code):
    calls: list[list[str]] = []

    def runner(args: list[str]) -> str:
        calls.append(args)
        if "POST" in args:
            raise RunnerError(code, "definitive create failure")
        return "[]"

    with pytest.raises(RunnerError) as error:
        GitHubDelivery(runner=runner).create_or_adopt_pr(
            repository="owner/repo", head="branch", base="main", candidate_sha="abc",
            body="body", operation_id="op", receipt_root=tmp_path,
        )

    assert error.value.code == code
    assert len(calls) == 2


def test_gh_timeout_is_structured_and_bounded():
    with patch("spec_runner.github_delivery.subprocess.run",
               side_effect=subprocess.TimeoutExpired(["gh"], 120)) as run:
        with pytest.raises(Exception) as error:
            GitHubDelivery._gh(["api", "repos/owner/repo"])
    assert error.value.code == "github_delivery_timeout"
    assert run.call_args.kwargs["timeout"] == 120


def test_gh_http_failures_preserve_recovery_class():
    with patch("spec_runner.github_delivery.subprocess.run",
               side_effect=subprocess.CalledProcessError(1, ["gh"], stderr="HTTP 429 rate limit")):
        with pytest.raises(Exception) as error:
            GitHubDelivery._gh(["api", "repos/owner/repo"])
    assert error.value.code == "github_rate_limited"
