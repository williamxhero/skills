from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from spec_runner.errors import RunnerError
from spec_runner.github_tracker import GitHubTracker


class GitHubTrackerTests(unittest.TestCase):
    def test_reads_issue_and_all_comment_pages_without_claiming_native_relations(self) -> None:
        calls: list[list[str]] = []

        def fake(arguments: list[str]) -> str:
            calls.append(arguments)
            if "comments" in arguments[-1]:
                return json.dumps([[{"body": "page one"}], [{"body": "page two"}]])
            return json.dumps(
                {
                    "id": 123,
                    "node_id": "I_123",
                    "repository_url": "https://api.github.com/repos/acme/demo",
                    "title": "Root",
                    "body": "Parent #2",
                    "updated_at": "2026-09-22T00:00:00Z",
                }
            )

        result = GitHubTracker(runner=fake).read_issue(repository="acme/demo", number=1)
        self.assertEqual(result.snapshot.records[0].comments, ("page one", "page two"))
        self.assertFalse(result.relation_evidence["native"])
        self.assertTrue(result.relation_evidence["complete_pagination"])
        self.assertEqual(len(calls), 2)

    def test_body_relations_are_explicit_and_not_claimed_as_native(self) -> None:
        def fake(arguments: list[str]) -> str:
            if "comments" in arguments[-1]:
                return json.dumps([[]])
            issue_number = arguments[-1].split("/issues/")[-1]
            if issue_number == "1":
                return json.dumps({"id": 1, "node_id": "I1", "repository_url": "https://api.github.com/repos/acme/demo", "title": "Child", "body": "Parent: #2\nBlocked by: #3", "updated_at": "r1"})
            return json.dumps({"id": int(issue_number), "node_id": f"I{issue_number}", "repository_url": "https://api.github.com/repos/acme/demo", "title": issue_number, "body": "", "updated_at": f"r{issue_number}"})

        result = GitHubTracker(runner=fake).read_issue(repository="acme/demo", number=1, linked_numbers=[2])
        record = next(item for item in result.snapshot.records if item.key == "GH-1")
        self.assertEqual(record.parent, "GH-2")
        self.assertEqual(record.blocked_by, ())
        self.assertEqual(result.relation_evidence["body_relations"]["GH-1"]["parent"], "GH-2")
        self.assertEqual(result.relation_evidence["unresolved_relations"], [{"issue": "GH-1", "relation": "blocked_by", "target": "GH-3"}])
        self.assertFalse(result.relation_evidence["native"])

    def test_malformed_issue_and_comments_responses_are_structured_read_errors(self) -> None:
        with self.assertRaisesRegex(RunnerError, "not valid JSON") as issue_error:
            GitHubTracker(runner=lambda arguments: "not-json").read_issue(repository="acme/demo", number=1)
        self.assertEqual(issue_error.exception.code, "github_issue_read_failed")

        def comments_only(arguments: list[str]) -> str:
            if "comments" in arguments[-1]:
                return "not-json"
            return json.dumps({"repository_url": "https://api.github.com/repos/acme/demo", "title": "Issue", "body": "", "updated_at": "r1"})

        with self.assertRaisesRegex(RunnerError, "not valid JSON") as comments_error:
            GitHubTracker(runner=comments_only).read_issue(repository="acme/demo", number=1)
        self.assertEqual(comments_error.exception.code, "github_pagination_incomplete")

    def test_malformed_paged_objects_fail_closed(self) -> None:
        raw = json.dumps([["not-an-object"]])
        tracker = GitHubTracker(runner=lambda arguments: raw)
        with self.assertRaisesRegex(RunnerError, "non-object") as comments_error:
            tracker._comments("acme/demo", 1)
        self.assertEqual(comments_error.exception.code, "github_pagination_incomplete")
        with self.assertRaisesRegex(RunnerError, "non-object") as issue_error:
            tracker._run_owned_issue(repository="acme/demo", marker="marker", title="title", body="body")
        self.assertEqual(issue_error.exception.code, "github_pagination_incomplete")
        with self.assertRaisesRegex(RunnerError, "non-object") as relation_error:
            tracker._relation_items("acme/demo", "repos/acme/demo/issues/1/sub_issues")
        self.assertEqual(relation_error.exception.code, "github_relation_read_incomplete")

    def test_partial_publish_and_lost_response_reconcile_by_marker(self) -> None:
        calls: list[list[str]] = []
        umbrella = {"number": 9, "node_id": "I9", "repository_url": "https://api.github.com/repos/acme/demo", "title": "Umbrella", "body": "<!-- spec-runner-key:ROOT operation:op-1 -->\nroot"}
        first = {"number": 10, "node_id": "I10", "repository_url": "https://api.github.com/repos/acme/demo", "title": "One", "body": "<!-- spec-runner-key:SR-01 operation:op-1 -->\none"}
        second = {"number": 11, "node_id": "I11", "repository_url": "https://api.github.com/repos/acme/demo", "title": "Two", "body": "<!-- spec-runner-key:SR-02 operation:op-1 -->\ntwo"}

        def fake(arguments: list[str]) -> str:
            calls.append(arguments)
            if arguments[0:2] == ["api", "repos/acme/demo/issues"] and "POST" in arguments:
                if "title=Umbrella" in arguments:
                    return json.dumps(umbrella)
                if "title=One" in arguments:
                    return json.dumps(first)
                raise RunnerError("github_delivery_failed", "simulated lost response")
            if "issues?state=all" in arguments[-1]:
                return json.dumps([[umbrella, first, second]])
            if len(arguments) == 2 and arguments[1].startswith("repos/acme/demo/issues/"):
                number = int(arguments[1].rsplit("/", 1)[1])
                return json.dumps({9: umbrella, 10: first, 11: second}[number])
            raise AssertionError(arguments)

        with tempfile.TemporaryDirectory() as temp:
            result = GitHubTracker(runner=fake).publish_draft(
                repository="acme/demo",
                draft={"umbrella": {"key": "ROOT", "title": "Umbrella", "body": "root"}, "specs": [
                    {"key": "SR-01", "title": "One", "body": "one"},
                    {"key": "SR-02", "title": "Two", "body": "two"},
                ]},
                operation_id="op-1",
                receipt_root=Path(temp),
            )
        self.assertTrue(result["created"])
        self.assertEqual([item["number"] for item in result["receipt"]["issues"]], [9, 10, 11])
        self.assertEqual(sum("POST" in call for call in calls), 3)

    def test_unknown_create_is_persisted_and_not_replayed_blindly(self) -> None:
        calls: list[list[str]] = []

        def fake(arguments: list[str]) -> str:
            calls.append(arguments)
            if "POST" in arguments:
                raise RunnerError("github_delivery_failed", "simulated lost response")
            if "issues?state=all" in arguments[-1]:
                return json.dumps([[]])
            raise AssertionError(arguments)

        with tempfile.TemporaryDirectory() as temp:
            adapter = GitHubTracker(runner=fake)
            draft = {"specs": [{"key": "SR-01", "title": "One", "body": "one"}]}
            with self.assertRaisesRegex(RunnerError, "unknown"):
                adapter.publish_draft(repository="acme/demo", draft=draft, operation_id="op-unknown", receipt_root=Path(temp))
            with self.assertRaisesRegex(RunnerError, "unknown"):
                adapter.publish_draft(repository="acme/demo", draft=draft, operation_id="op-unknown", receipt_root=Path(temp))
        self.assertEqual(sum("POST" in call for call in calls), 1)

    def test_http_failures_keep_auth_permission_not_found_rate_and_server_states_distinct(self) -> None:
        cases = {
            "HTTP 401": "github_auth",
            "HTTP 403: Resource not accessible": "github_forbidden",
            "HTTP 404": "github_not_found",
            "HTTP 403: API rate limit exceeded": "github_rate_limited",
            "HTTP 429": "github_rate_limited",
            "HTTP 503": "github_server_error",
            "request failed without status": "github_request_failed",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                with patch("spec_runner.github_tracker.subprocess.run",
                           side_effect=subprocess.CalledProcessError(1, ["gh"], stderr=message)):
                    with self.assertRaises(RunnerError) as error:
                        GitHubTracker._run_gh(["api", "repos/acme/demo/issues"])
                self.assertEqual(error.exception.code, expected)

    def test_definitive_issue_create_failures_are_not_reclassified_as_unknown(self) -> None:
        cases = ("github_auth", "github_forbidden", "github_not_found", "github_rate_limited",
                 "github_rejected", "github_unavailable")
        draft = {"specs": [{"key": "SR-01", "title": "One", "body": "one"}]}
        for code in cases:
            with self.subTest(code=code):
                calls: list[list[str]] = []

                def fake(arguments: list[str], failure: str = code) -> str:
                    calls.append(arguments)
                    if "POST" in arguments:
                        raise RunnerError(failure, "definitive GitHub failure")
                    raise AssertionError("definitive create failures must not trigger marker reconciliation")

                with tempfile.TemporaryDirectory() as temp:
                    with self.assertRaises(RunnerError) as error:
                        GitHubTracker(runner=fake).publish_draft(
                            repository="acme/demo", draft=draft, operation_id="op-definitive",
                            receipt_root=Path(temp),
                        )
                    self.assertEqual(error.exception.code, code)
                    receipt = json.loads((Path(temp) / ".spec-runner-github-receipts.json").read_text(encoding="utf-8"))
                    self.assertEqual(receipt["op-definitive"]["unknown_keys"], [])
                self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
