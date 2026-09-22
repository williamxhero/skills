from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

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

    def test_native_relation_mode_blocks_before_any_external_write(self) -> None:
        calls: list[list[str]] = []

        def fake(arguments: list[str]) -> str:
            calls.append(arguments)
            return "{}"

        with self.assertRaisesRegex(RunnerError, "native relation"):
            GitHubTracker(runner=fake).publish_draft(
                repository="acme/demo",
                draft={"specs": [{"key": "SR-01", "title": "Spec", "body": "body"}]},
                operation_id="op-native",
                receipt_root=Path("."),
                relation_mode="native",
            )
        self.assertEqual(calls, [])

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


if __name__ == "__main__":
    unittest.main()
