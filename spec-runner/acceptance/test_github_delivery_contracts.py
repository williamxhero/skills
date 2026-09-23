from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from spec_runner.github_delivery import GitHubDelivery


def test_check_runs_are_paginated_and_pending_is_waiting_not_failure():
    calls: list[list[str]] = []

    def runner(args: list[str]) -> str:
        calls.append(args)
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
    result = GitHubDelivery(runner=lambda args: json.dumps({"check_runs": [
        {"name": "ci", "status": "completed", "conclusion": "neutral", "head_sha": "abc"},
        {"name": "lint", "status": "completed", "conclusion": "success", "head_sha": "old"},
    ]})).checks(repository="owner/repo", candidate_sha="abc", required=["ci", "lint"])
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
