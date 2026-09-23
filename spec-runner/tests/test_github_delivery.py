from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from spec_runner.errors import RunnerError
from spec_runner.github_delivery import GitHubDelivery


class GitHubDeliveryTests(unittest.TestCase):
    def test_pr_is_created_once_and_retry_adopts_receipt(self):
        calls: list[list[str]] = []
        readback = json.dumps({"number": 12, "html_url": "https://example.invalid/pr/12",
                               "head": {"sha": "abc1234", "ref": "branch"},
                               "base": {"ref": "main"},
                               "body": "<!-- spec-runner-pr:op-1 candidate:abc1234 -->",
                               "state": "open", "merged": False})
        responses = ["[]", json.dumps({"number": 12, "html_url": "https://example.invalid/pr/12"}), readback, readback]

        def runner(args: list[str]) -> str:
            calls.append(args)
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as temp:
            adapter = GitHubDelivery(runner=runner)
            first = adapter.create_or_adopt_pr(repository="owner/repo", head="branch", base="main", candidate_sha="abc1234", body="evidence", operation_id="op-1", receipt_root=Path(temp))
            second = adapter.create_or_adopt_pr(repository="owner/repo", head="branch", base="main", candidate_sha="abc1234", body="evidence", operation_id="op-1", receipt_root=Path(temp))
            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            self.assertEqual(len(calls), 4)
            self.assertEqual(second["receipt"]["state"], "open")

    def test_stale_receipt_is_not_returned_as_verified(self):
        calls = 0

        def runner(args: list[str]) -> str:
            nonlocal calls
            calls += 1
            if any(arg == "repos/owner/repo/pulls" for arg in args) and "GET" in args:
                return "[]"
            if any(arg == "repos/owner/repo/pulls" for arg in args) and "POST" in args:
                return json.dumps({"number": 12, "html_url": "https://example.invalid/pr/12"})
            if calls == 3:
                return json.dumps({"number": 12, "head": {"sha": "abc1234", "ref": "branch"},
                                   "base": {"ref": "main"},
                                   "body": "<!-- spec-runner-pr:op-1 candidate:abc1234 -->"})
            return json.dumps({"number": 12, "head": {"sha": "new-sha", "ref": "branch"},
                               "base": {"ref": "main"},
                               "body": "<!-- spec-runner-pr:op-1 candidate:abc1234 -->"})

        with tempfile.TemporaryDirectory() as temp:
            adapter = GitHubDelivery(runner=runner)
            adapter.create_or_adopt_pr(repository="owner/repo", head="branch", base="main", candidate_sha="abc1234", body="evidence", operation_id="op-1", receipt_root=Path(temp))
            with self.assertRaises(RunnerError) as context:
                adapter.create_or_adopt_pr(repository="owner/repo", head="branch", base="main", candidate_sha="abc1234", body="evidence", operation_id="op-1", receipt_root=Path(temp))
            self.assertEqual(context.exception.code, "github_pr_receipt_stale")
            self.assertEqual(calls, 4)

    def test_checks_bind_success_to_candidate_sha_and_merge_requires_authorization(self):
        def runner(args: list[str]) -> str:
            if "check-runs" in args[-1]:
                return json.dumps({"check_runs": [{"name": "ci", "status": "completed", "conclusion": "success", "head_sha": "abc"}]})
            return json.dumps({"sha": "abc", "state": "success", "statuses": []})

        adapter = GitHubDelivery(runner=runner)
        checks = adapter.checks(repository="owner/repo", candidate_sha="abc", required=["ci"])
        self.assertTrue(checks["ready"])
        with self.assertRaises(RunnerError) as context:
            adapter.merge(repository="owner/repo", number=12, expected_head="abc")
        self.assertEqual(context.exception.code, "merge_not_authorized")

    def test_wrong_sha_is_not_ready(self):
        adapter = GitHubDelivery(runner=lambda args: json.dumps(
            {"check_runs": [{"name": "ci", "status": "completed", "conclusion": "success", "head_sha": "old"}]}
            if "check-runs" in args[-1] else {"sha": "new", "state": "success", "statuses": []}))
        checks = adapter.checks(repository="owner/repo", candidate_sha="new", required=["ci"])
        self.assertFalse(checks["ready"])
        self.assertEqual(checks["wrong_sha"], ["ci"])

    def test_merge_requires_provider_readback(self):
        calls: list[list[str]] = []

        def runner(args: list[str]) -> str:
            calls.append(args)
            if "check-runs" in args[-1]:
                return json.dumps({"check_runs": [{"name": "ci", "status": "completed", "conclusion": "success", "head_sha": "abc"}]})
            if "status" in args[-1]:
                return json.dumps({"sha": "abc", "state": "success", "statuses": []})
            if args[-1] == "repos/owner/repo/pulls/12":
                if len([call for call in calls if call[-1] == args[-1]]) == 1:
                    return json.dumps({"head": {"sha": "abc"}, "base": {"ref": "main"}, "merged_at": None})
                return json.dumps({"head": {"sha": "abc"}, "base": {"ref": "main"},
                                   "merged_at": "2026-09-22T00:00:00Z", "merge_commit_sha": "merge123"})
            return json.dumps({"merged": True, "sha": "merge123"})

        result = GitHubDelivery(runner=runner).merge(
            repository="owner/repo", number=12, expected_head="abc", expected_base="main",
            candidate_receipt={"candidate_sha": "abc", "outcome": "verified"},
            review={"candidate_sha": "abc", "approved": True, "review_digest": "review"},
            checks={"candidate_sha": "abc", "required": ["ci"], "ready": True}, allow=True)
        self.assertTrue(result["merged"])
        self.assertEqual(result["sha"], "merge123")
        self.assertEqual(len(calls), 5)

    def test_merge_false_without_readback_is_not_success(self):
        def runner(args: list[str]) -> str:
            if "check-runs" in args[-1]:
                return json.dumps({"check_runs": [{"name": "ci", "status": "completed", "conclusion": "success", "head_sha": "abc"}]})
            if "status" in args[-1]:
                return json.dumps({"sha": "abc", "state": "success", "statuses": []})
            if args[-1] == "repos/owner/repo/pulls/12":
                return json.dumps({"head": {"sha": "abc"}, "base": {"ref": "main"}, "merged_at": None})
            return json.dumps({"merged": False, "message": "not mergeable"})

        with self.assertRaisesRegex(RunnerError, "not confirmed"):
            GitHubDelivery(runner=runner).merge(
                repository="owner/repo", number=12, expected_head="abc", expected_base="main",
                candidate_receipt={"candidate_sha": "abc", "outcome": "verified"},
                review={"candidate_sha": "abc", "approved": True, "review_digest": "review"},
                checks={"candidate_sha": "abc", "required": ["ci"], "ready": True}, allow=True)

    def test_merge_requires_independent_receipts_even_when_authorized(self):
        with self.assertRaisesRegex(RunnerError, "candidate, review, and checks"):
            GitHubDelivery(runner=lambda args: "{}").merge(
                repository="owner/repo", number=12, expected_head="abc", expected_base="main", allow=True)

    def test_status_context_can_satisfy_required_check(self):
        def runner(args: list[str]) -> str:
            if "check-runs" in args[-1]:
                return json.dumps({"check_runs": []})
            return json.dumps({"sha": "abc", "state": "success", "statuses": [
                {"context": "legacy-ci", "state": "success", "updated_at": "2026-09-23T00:00:00Z"}
            ]})

        result = GitHubDelivery(runner=runner).checks(repository="owner/repo", candidate_sha="abc", required=["legacy-ci"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["states"]["legacy-ci"]["source"], "status_context")

    def test_already_merged_pr_is_adopted_without_second_merge_request(self):
        calls: list[list[str]] = []

        def runner(args: list[str]) -> str:
            calls.append(args)
            if "check-runs" in args[-1]:
                return json.dumps({"check_runs": [{"name": "ci", "status": "completed", "conclusion": "success", "head_sha": "abc"}]})
            if "status" in args[-1]:
                return json.dumps({"sha": "abc", "state": "success", "statuses": []})
            return json.dumps({"number": 12, "head": {"sha": "abc"}, "base": {"ref": "main"},
                               "merged": True, "merged_at": "2026-09-23T00:00:00Z",
                               "merge_commit_sha": "merge123"})

        result = GitHubDelivery(runner=runner).merge(
            repository="owner/repo", number=12, expected_head="abc", expected_base="main",
            candidate_receipt={"candidate_sha": "abc", "outcome": "verified"},
            review={"candidate_sha": "abc", "approved": True, "review_digest": "review"},
            checks={"candidate_sha": "abc", "required": ["ci"], "ready": True}, allow=True)
        self.assertTrue(result["adopted"])
        self.assertFalse(any("/merge" in arg for call in calls for arg in call))


if __name__ == "__main__":
    unittest.main()
