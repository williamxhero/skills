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
        responses = ["[]", json.dumps({"number": 12, "html_url": "https://example.invalid/pr/12"})]

        def runner(args: list[str]) -> str:
            calls.append(args)
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as temp:
            adapter = GitHubDelivery(runner=runner)
            first = adapter.create_or_adopt_pr(repository="owner/repo", head="branch", base="main", candidate_sha="abc1234", body="evidence", operation_id="op-1", receipt_root=Path(temp))
            second = adapter.create_or_adopt_pr(repository="owner/repo", head="branch", base="main", candidate_sha="abc1234", body="evidence", operation_id="op-1", receipt_root=Path(temp))
            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            self.assertEqual(len(calls), 2)

    def test_checks_bind_success_to_candidate_sha_and_merge_requires_authorization(self):
        def runner(args: list[str]) -> str:
            if "check-runs" in args[-1]:
                return json.dumps({"check_runs": [{"name": "ci", "status": "completed", "conclusion": "success", "head_sha": "abc"}]})
            return json.dumps({"head": {"sha": "abc"}})

        adapter = GitHubDelivery(runner=runner)
        checks = adapter.checks(repository="owner/repo", candidate_sha="abc", required=["ci"])
        self.assertTrue(checks["ready"])
        with self.assertRaises(RunnerError) as context:
            adapter.merge(repository="owner/repo", number=12, expected_head="abc")
        self.assertEqual(context.exception.code, "merge_not_authorized")

    def test_wrong_sha_is_not_ready(self):
        adapter = GitHubDelivery(runner=lambda args: json.dumps({"check_runs": [{"name": "ci", "status": "completed", "conclusion": "success", "head_sha": "old"}]}))
        checks = adapter.checks(repository="owner/repo", candidate_sha="new", required=["ci"])
        self.assertFalse(checks["ready"])
        self.assertEqual(checks["wrong_sha"], ["ci"])
