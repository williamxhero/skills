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


def test_check_runs_reject_malformed_page_even_with_passing_status_context():
    def runner(args: list[str]) -> str:
        if args[-1].endswith("/status"):
            return json.dumps({"sha": "abc", "statuses": [{"context": "ci", "state": "success"}]})
        return json.dumps({"check_runs": ["unreadable check run"]})

    with pytest.raises(RunnerError) as error:
        GitHubDelivery(runner=runner).checks(repository="owner/repo", candidate_sha="abc", required=["ci"])

    assert error.value.code == "github_checks_incomplete"


def test_newer_check_run_waits_even_when_older_run_completes_later():
    def runner(args: list[str]) -> str:
        if args[-1].endswith("/status"):
            return json.dumps({"sha": "abc", "statuses": []})
        return json.dumps({"check_runs": [
            {"id": 10, "name": "ci", "status": "completed", "conclusion": "success",
             "head_sha": "abc", "started_at": "2026-01-01T10:00:00Z",
             "completed_at": "2026-01-01T10:20:00Z"},
            {"id": 11, "name": "ci", "status": "in_progress", "conclusion": None,
             "head_sha": "abc", "started_at": "2026-01-01T10:10:00Z"},
        ]})

    result = GitHubDelivery(runner=runner).checks(
        repository="owner/repo", candidate_sha="abc", required=["ci"]
    )

    assert result["pending"] == ["ci"]
    assert result["ready"] is False


def test_newer_queued_check_run_without_start_time_still_waits():
    def runner(args: list[str]) -> str:
        if args[-1].endswith("/status"):
            return json.dumps({"sha": "abc", "statuses": []})
        return json.dumps({"check_runs": [
            {"id": 10, "name": "ci", "status": "completed", "conclusion": "success",
             "head_sha": "abc", "started_at": "2026-01-01T10:00:00Z"},
            {"id": 11, "name": "ci", "status": "queued", "conclusion": None,
             "head_sha": "abc", "started_at": None},
        ]})

    result = GitHubDelivery(runner=runner).checks(
        repository="owner/repo", candidate_sha="abc", required=["ci"]
    )

    assert result["pending"] == ["ci"]
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


def test_pr_adoption_rejects_matching_sha_from_another_head_repository(tmp_path):
    calls: list[list[str]] = []
    pull = {
        "number": 12,
        "head": {"sha": "abc", "ref": "branch", "repo": {"full_name": "other/repo"}},
        "base": {"ref": "main", "repo": {"full_name": "owner/repo"}},
        "body": "<!-- spec-runner-pr:op candidate:abc -->",
    }

    def runner(args: list[str]) -> str:
        calls.append(args)
        return json.dumps([pull] if "--paginate" in args else pull)

    with pytest.raises(RunnerError) as error:
        GitHubDelivery(runner=runner).create_or_adopt_pr(
            repository="owner/repo", head="branch", base="main", candidate_sha="abc",
            body="body", operation_id="op", receipt_root=tmp_path,
        )
    assert error.value.code == "github_identity_mismatch"
    assert len(calls) == 2
    assert not any("POST" in call for call in calls)
    assert not (tmp_path / ".spec-runner-pr-receipts.json").exists()


def test_pr_operation_receipt_is_durable_before_local_projection_replay(tmp_path: Path):
    calls: list[list[str]] = []
    completed: list[dict[str, object]] = []
    marker = "<!-- spec-runner-pr:op-durable candidate:abc -->"
    pull = {
        "number": 12,
        "html_url": "https://github.com/owner/repo/pull/12",
        "head": {"sha": "abc", "ref": "branch", "repo": {"full_name": "owner/repo"}},
        "base": {"ref": "main", "repo": {"full_name": "owner/repo"}},
        "body": marker,
        "state": "open",
        "merged": False,
        "merged_at": None,
    }

    def runner(args: list[str]) -> str:
        calls.append(args)
        if args[-1] == "repos/owner/repo/pulls/12":
            return json.dumps(pull)
        if "repos/owner/repo/pulls" in args and "POST" not in args:
            return "[]"
        if "POST" in args:
            return json.dumps(pull)
        raise AssertionError(args)

    def intent(**_kwargs):
        return {"state": "intent"}

    def record(**kwargs):
        completed.append(kwargs["receipt"])

    first = GitHubDelivery(runner=runner).create_or_adopt_pr(
        repository="owner/repo", head="branch", base="main", candidate_sha="abc",
        body="body", operation_id="op-durable", receipt_root=tmp_path,
        operation_intent=intent, operation_completed=record,
    )
    assert first["receipt"]["number"] == 12
    assert len(completed) == 1
    assert (tmp_path / ".spec-runner-pr-receipts.json").is_file()

    (tmp_path / ".spec-runner-pr-receipts.json").unlink()
    calls.clear()
    durable = completed[0]
    replay = GitHubDelivery(runner=runner).create_or_adopt_pr(
        repository="owner/repo", head="branch", base="main", candidate_sha="abc",
        body="body", operation_id="op-durable", receipt_root=tmp_path,
        operation_intent=lambda **_kwargs: {"state": "completed", "receipt": durable},
        operation_completed=record,
    )
    assert replay["created"] is False
    assert replay["receipt"]["number"] == 12
    assert len(completed) == 1
    assert not any("POST" in call for call in calls)


def test_merge_queue_checkpoint_is_reused_after_ambiguous_enqueue(tmp_path: Path):
    calls: list[list[str]] = []
    progress: list[dict[str, object]] = []

    def runner(args: list[str]) -> str:
        calls.append(args)
        endpoint = args[-1]
        if "check-runs" in endpoint:
            return json.dumps({"check_runs": [{"name": "ci", "status": "completed",
                                                "conclusion": "success", "head_sha": "abc"}]})
        if endpoint.endswith("/status"):
            return json.dumps({"sha": "abc", "state": "success", "statuses": []})
        if endpoint == "repos/owner/repo/pulls/12":
            return json.dumps({
                "number": 12, "node_id": "PR_node_12",
                "head": {"sha": "abc", "ref": "branch", "repo": {"full_name": "owner/repo"}},
                "base": {"ref": "main", "repo": {"full_name": "owner/repo"}},
                "merged": False, "merged_at": None, "merge_commit_sha": None,
                "mergeable_state": "blocked",
            })
        if "graphql" in args:
            query = next(item for item in args if item.startswith("query="))
            if "enqueuePullRequest" in query:
                return json.dumps({"errors": [{"message": "queue response lost"}]})
            return json.dumps({"data": {"node": {"mergeQueueEntry": {
                "id": "MQ-12", "position": 1, "state": "QUEUED"
            }}}})
        raise AssertionError(args)

    common = {
        "repository": "owner/repo", "number": 12, "expected_head": "abc",
        "expected_base": "main",
        "candidate_receipt": {"candidate_sha": "abc", "outcome": "verified"},
        "review": {"candidate_sha": "abc", "approved": True, "review_digest": "review"},
        "checks": {"candidate_sha": "abc", "required": ["ci"], "ready": True},
        "allow": True, "expected_head_ref": "branch", "operation_id": "merge-op",
    }

    first = GitHubDelivery(runner=runner).merge(
        **common,
        operation_intent=lambda **_kwargs: {"state": "intent"},
        operation_progress=lambda **kwargs: progress.append(kwargs),
    )
    assert first["waiting"] is True
    assert first["queue"]["id"] == "MQ-12"
    assert len(progress) == 1

    replay = GitHubDelivery(runner=runner).merge(
        **common,
        operation_intent=lambda **_kwargs: {"state": "waiting_merge_queue", "receipt": first},
        operation_progress=lambda **kwargs: progress.append(kwargs),
    )
    assert replay["waiting"] is True
    assert replay["queue"]["id"] == "MQ-12"
    assert not any("/merge" in arg for call in calls for arg in call)


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


def test_gh_http_422_is_a_definitive_rejection():
    with patch("spec_runner.github_delivery.subprocess.run",
               side_effect=subprocess.CalledProcessError(1, ["gh"], stderr="HTTP 422 validation failed")):
        with pytest.raises(Exception) as error:
            GitHubDelivery._gh(["api", "repos/owner/repo/pulls", "--method", "POST"])
    assert error.value.code == "github_rejected"


def test_merge_response_sha_must_match_authoritative_pr_readback():
    calls: list[list[str]] = []

    def runner(args: list[str]) -> str:
        calls.append(args)
        endpoint = args[-1]
        if "check-runs" in endpoint:
            return json.dumps({"check_runs": [{
                "name": "ci", "status": "completed", "conclusion": "success", "head_sha": "abc",
            }]})
        if endpoint.endswith("/status"):
            return json.dumps({"sha": "abc", "state": "success", "statuses": []})
        if endpoint == "repos/owner/repo/pulls/12":
            merged = sum(call[-1] == endpoint for call in calls) > 1
            return json.dumps({
                "number": 12,
                "head": {"sha": "abc", "repo": {"full_name": "owner/repo"}},
                "base": {"ref": "main", "repo": {"full_name": "owner/repo"}},
                "merged": merged,
                "merged_at": "2026-09-27T00:00:00Z" if merged else None,
                "merge_commit_sha": "actual-merge" if merged else None,
            })
        if "repos/owner/repo/pulls/12/merge" in args:
            return json.dumps({"merged": True, "sha": "wrong-merge"})
        raise AssertionError(args)

    with pytest.raises(RunnerError) as error:
        GitHubDelivery(runner=runner).merge(
            repository="owner/repo", number=12, expected_head="abc", expected_base="main",
            candidate_receipt={"candidate_sha": "abc", "outcome": "verified"},
            review={"candidate_sha": "abc", "approved": True, "review_digest": "review"},
            checks={"candidate_sha": "abc", "required": ["ci"], "ready": True}, allow=True,
        )
    assert error.value.code == "github_merge_readback_mismatch"
    assert sum("repos/owner/repo/pulls/12/merge" in call for call in calls) == 1
