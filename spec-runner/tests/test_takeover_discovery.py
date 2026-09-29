from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from spec_runner.errors import RunnerError
from spec_runner.takeover_discovery import (
    build_adopted_plans,
    discover_takeover,
    validate_snapshot,
)

REPOSITORY = "acme/demo"
BASE_SHA = "c" * 40
CANDIDATE_SHA = "a" * 40
MERGE_SHA = "b" * 40


def _issue(number: int, key: str, title: str, *, state: str = "open") -> dict[str, object]:
    return {
        "number": number,
        "repository_url": f"https://api.github.com/repos/{REPOSITORY}",
        "title": title,
        "body": f"<!-- spec-runner-key:{key} operation:op-{key.lower()} -->\n{title} body",
        "updated_at": f"revision-{number}",
        "state": state,
        "labels": [{"name": "spec-runner"}],
    }


class GitHubFixture:
    def __init__(self) -> None:
        self.issues = {
            1: _issue(1, "ROOT", "Umbrella"),
            2: _issue(2, "S1", "First SPEC"),
            3: _issue(3, "S2", "Second SPEC"),
            4: _issue(4, "T1", "First ticket"),
            5: _issue(5, "T2", "Second ticket"),
        }
        self.children = {1: [2, 3], 2: [4], 3: [5], 4: [], 5: []}
        self.dependencies = {1: [], 2: [], 3: [2], 4: [], 5: [4]}
        self.branches: list[dict[str, object]] = []
        self.pull_requests: list[dict[str, object]] = []
        self.calls: list[list[str]] = []
        self.check_ready = True

    def __call__(self, arguments: list[str]) -> str:
        self.calls.append(arguments)
        endpoint = arguments[-1]
        if endpoint.endswith("issues?state=all&per_page=100"):
            return json.dumps(list(self.issues.values()))
        if "/comments" in endpoint:
            return json.dumps([[]])
        if "/sub_issues?per_page=100" in endpoint:
            number = int(endpoint.split("/issues/")[1].split("/")[0])
            return json.dumps([self.issues[item] for item in self.children[number]])
        if "/dependencies/blocked_by?per_page=100" in endpoint:
            number = int(endpoint.split("/issues/")[1].split("/")[0])
            return json.dumps([self.issues[item] for item in self.dependencies[number]])
        if endpoint.startswith(f"repos/{REPOSITORY}/issues/"):
            number = int(endpoint.rsplit("/", 1)[1])
            return json.dumps(self.issues[number])
        if endpoint == f"repos/{REPOSITORY}/branches?per_page=100":
            return json.dumps(self.branches)
        if endpoint == f"repos/{REPOSITORY}/pulls?state=all&per_page=100":
            return json.dumps(self.pull_requests)
        if endpoint.endswith("/check-runs"):
            sha = endpoint.split("/commits/")[1].split("/")[0]
            return json.dumps([{
                "check_runs": [{
                    "name": "ci", "head_sha": sha, "status": "completed",
                    "conclusion": "success" if self.check_ready else "failure",
                    "completed_at": "2026-09-29T00:00:00Z",
                }],
            }])
        if endpoint.endswith("/status"):
            sha = endpoint.split("/commits/")[1].split("/")[0]
            return json.dumps([{"sha": sha, "statuses": []}])
        raise AssertionError(f"unexpected GitHub request: {arguments}")


def _local_snapshot(workspace: Path) -> dict[str, object]:
    return {
        "repository_path": str(workspace.resolve()),
        "target_ref": "refs/heads/main",
        "target_sha": BASE_SHA,
        "checked_out_branch": "main",
        "dirty": False,
        "changed_paths": [],
        "working_tree_digest": "working-tree",
        "origin_target_sha": BASE_SHA,
        "origin_relation": "equal",
    }


def _candidate_receipt() -> dict[str, object]:
    return {
        "schema_version": "spec-runner-candidate-receipt/v1",
        "outcome": "verified",
        "candidate_sha": CANDIDATE_SHA,
        "acceptance_version": "ticket-plan-digest",
        "checks": [{"command": ["python", "-c", "pass"], "acceptance": ["T1"], "passed": True}],
        "write_scope": {"allowed_paths": ["src"], "changed_paths": ["src/example.py"]},
    }


def _review_receipt() -> dict[str, object]:
    return {
        "schema_version": "spec-runner-review-result/v1",
        "candidate_sha": CANDIDATE_SHA,
        "acceptance_version": "ticket-plan-digest",
        "findings": [],
        "approved": True,
        "review_digest": "review-digest",
    }


class TakeoverDiscoveryTests(unittest.TestCase):
    def _discover(self, fixture: GitHubFixture, root: Path, *, required: list[str] | None = None) -> dict[str, object]:
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        control = root / "control"
        control.mkdir(exist_ok=True)
        artifacts = root / "artifacts"
        artifacts.mkdir(exist_ok=True)
        return discover_takeover(
            repository=REPOSITORY,
            umbrella_issue=1,
            workspace=workspace,
            target_ref="refs/heads/main",
            control_root=control,
            takeover_key="takeover-1",
            artifact_roots=[artifacts],
            required_checks=required or [],
            runner=fixture,
        )

    def test_discovery_adopts_issue_graph_and_does_not_create_empty_ticket_plan(self) -> None:
        fixture = GitHubFixture()
        fixture.children[3] = []
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("spec_runner.takeover_discovery._local_git_snapshot", side_effect=lambda path, ref, timeout: _local_snapshot(path)):
                result = self._discover(fixture, root)
            validate_snapshot(result["snapshot"])
            plans = build_adopted_plans(result["snapshot"], model="gpt-test", effort="low", base_sha=BASE_SHA)
            self.assertEqual([item["key"] for item in plans["spec_plan"]["specs"]], ["S1", "S2"])
            self.assertEqual(set(plans["ticket_plans"]), {"S1"})
            self.assertEqual(plans["ticket_plans"]["S1"]["github_operation_id"], "op-s1")
            self.assertEqual([item["number"] for item in plans["ticket_plans"]["S1"]["github_issues"]], [2, 4])

    def test_frontier_classification_requires_all_independent_completion_evidence(self) -> None:
        fixture = GitHubFixture()
        fixture.branches = [{"name": "codex/S1", "commit": {"sha": CANDIDATE_SHA}}]
        fixture.pull_requests = [{
            "number": 101,
            "html_url": "https://example.test/pr/101",
            "body": json.dumps({"spec_key": "S1"}),
            "state": "closed",
            "merged": True,
            "merge_commit_sha": MERGE_SHA,
            "head": {"ref": "codex/S1", "sha": CANDIDATE_SHA},
            "base": {"ref": "main", "repo": {"full_name": REPOSITORY}},
        }]
        complete_delivery = {
            "schema_version": "spec-runner-production-delivery/v1",
            "spec_key": "S1",
            "candidate": _candidate_receipt(),
            "review": _review_receipt(),
            "checks": {"candidate_sha": CANDIDATE_SHA, "ready": True},
            "merge": {"merged": True, "sha": MERGE_SHA},
            "issue_closure": {"complete": True, "issues": [
                {"number": 2, "state": "closed"}, {"number": 4, "state": "closed"},
            ]},
            "cleanup": {"outcome": "cleaned"},
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            artifacts.mkdir(exist_ok=True)
            (artifacts / "candidate-S1.json").write_text(json.dumps(_candidate_receipt()), encoding="utf-8")
            (artifacts / f"review-S1-{CANDIDATE_SHA[:12]}.json").write_text(json.dumps(_review_receipt()), encoding="utf-8")
            (artifacts / "delivery-S1.json").write_text(json.dumps(complete_delivery), encoding="utf-8")
            with patch("spec_runner.takeover_discovery._local_git_snapshot", side_effect=lambda path, ref, timeout: _local_snapshot(path)):
                result = self._discover(fixture, root)
            frontiers = {item["key"]: item for item in result["spec_frontiers"]}
            self.assertEqual(frontiers["S1"]["state"], "completed")
            self.assertTrue(frontiers["S1"]["complete_evidence"]["complete"])
            self.assertEqual(frontiers["S2"]["state"], "tickets_adopted")

            (artifacts / "review-S1-duplicate.json").write_text(json.dumps({**_review_receipt(), "review_digest": "different"}), encoding="utf-8")
            with patch("spec_runner.takeover_discovery._local_git_snapshot", side_effect=lambda path, ref, timeout: _local_snapshot(path)), self.assertRaisesRegex(RunnerError, "multiple different receipts") as error:
                self._discover(fixture, root)
            self.assertEqual(error.exception.code, "takeover_receipt_ambiguous")

    def test_candidate_receipt_can_supply_candidate_identity_without_pr(self) -> None:
        fixture = GitHubFixture()
        fixture.children[1] = [2]
        fixture.children[2] = [4]
        fixture.branches = []
        with TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            artifacts.mkdir(exist_ok=True)
            (artifacts / "candidate-S1.json").write_text(json.dumps(_candidate_receipt()), encoding="utf-8")
            with patch("spec_runner.takeover_discovery._local_git_snapshot", side_effect=lambda path, ref, timeout: _local_snapshot(path)):
                result = self._discover(fixture, root)
            frontier = next(item for item in result["spec_frontiers"] if item["key"] == "S1")
            self.assertEqual(frontier["state"], "candidate_ready")
            self.assertEqual(frontier["candidate_sha"], CANDIDATE_SHA)

    def test_candidate_branch_drift_and_duplicate_spec_identity_block_discovery(self) -> None:
        fixture = GitHubFixture()
        fixture.branches = [{"name": "codex/S1", "commit": {"sha": "d" * 40}}]
        fixture.pull_requests = [{
            "number": 101, "body": json.dumps({"spec_key": "S1"}),
            "head": {"ref": "codex/S1", "sha": CANDIDATE_SHA},
            "base": {"ref": "main", "repo": {"full_name": REPOSITORY}},
        }]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("spec_runner.takeover_discovery._local_git_snapshot", side_effect=lambda path, ref, timeout: _local_snapshot(path)), self.assertRaisesRegex(RunnerError, "candidate branch tip") as error:
                self._discover(fixture, root)
            self.assertEqual(error.exception.code, "takeover_branch_drift")

        fixture = GitHubFixture()
        fixture.issues[3]["body"] = fixture.issues[3]["body"].replace("S2", "S1")
        with TemporaryDirectory() as directory:
            with patch("spec_runner.takeover_discovery._local_git_snapshot", side_effect=lambda path, ref, timeout: _local_snapshot(path)), self.assertRaisesRegex(RunnerError, "same SPEC") as error:
                self._discover(fixture, Path(directory))
            self.assertEqual(error.exception.code, "takeover_spec_identity_ambiguous")

    def test_snapshot_digest_change_is_rejected(self) -> None:
        snapshot = {"schema_version": "spec-runner-takeover-snapshot/v1", "takeover_key": "k", "input": {}, "graph": {}, "blockers": []}
        from spec_runner.plans import digest

        snapshot["digest"] = digest({key: value for key, value in snapshot.items() if key != "digest"})
        validate_snapshot(snapshot)
        snapshot["blockers"] = [{"code": "changed"}]
        with self.assertRaisesRegex(RunnerError, "does not match") as error:
            validate_snapshot(snapshot)
        self.assertEqual(error.exception.code, "takeover_snapshot_digest_mismatch")


if __name__ == "__main__":
    unittest.main()
