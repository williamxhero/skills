from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

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


if __name__ == "__main__":
    unittest.main()
