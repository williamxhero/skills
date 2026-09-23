"""Simulated transport, real publication receipts; not live GitHub evidence."""
import json
import subprocess
from pathlib import Path

import pytest

from spec_runner.errors import RunnerError
from spec_runner.github_tracker import GitHubTracker


class Transport:
    def __init__(self):
        self.issues = []
        self.posts = 0
        self.crash = False

    def __call__(self, args):
        if "POST" in args:
            if "sub_issues" in args[1]:
                self.sub_issue = next(int(v.split("=", 1)[1]) for v in args if v.startswith("sub_issue_id="))
                return "{}"
            if "dependencies/blocked_by" in args[1]:
                self.blocked_by = next(int(v.split("=", 1)[1]) for v in args if v.startswith("issue_id="))
                return "{}"
            self.posts += 1
            issue = {"number": len(self.issues) + 1,
                     "id": 100 + len(self.issues) + 1,
                     "repository_url": "https://api.github.com/repos/williamxhero/skills",
                     "title": next(v[6:] for v in args if v.startswith("title=")),
                     "body": next(v[5:] for v in args if v.startswith("body="))}
            self.issues.append(issue)
            if self.crash:
                self.crash = False
                raise SystemExit("process lost after server accepted POST")
            return json.dumps(issue)
        if "issues?state=all" in args[-1]:
            return json.dumps([self.issues])
        if "sub_issues" in args[-1]:
            return json.dumps([self.issues[1]] if getattr(self, "sub_issue", None) == self.issues[1]["id"] else [])
        if "dependencies/blocked_by" in args[-1]:
            return json.dumps([self.issues[0]] if getattr(self, "blocked_by", None) == self.issues[0]["id"] else [])
        return json.dumps(self.issues[int(args[-1].rsplit("/", 1)[1]) - 1])


def publish(tmp_path, transport, draft):
    return GitHubTracker(runner=transport).publish_draft(
        repository="williamxhero/skills", draft=draft,
        operation_id="SRAC-publication-contract", receipt_root=tmp_path)


def draft():
    return {"umbrella": {"key": "S1", "title": "SPEC", "body": "Scope"},
            "specs": [{"key": "T1", "title": "Ticket", "body": "Work", "parent": "S1"}]}


def test_single_ticket_retains_spec_and_resolves_parent_number(tmp_path):
    transport = Transport()
    result = publish(tmp_path, transport, draft())
    assert [i["key"] for i in result["receipt"]["issues"]] == ["S1", "T1"]
    assert "Parent: #1" in transport.issues[1]["body"]
    assert not publish(tmp_path, transport, draft())["created"]
    assert transport.posts == 2


def test_hard_exit_after_create_reconciles_without_second_post(tmp_path):
    transport = Transport()
    transport.crash = True
    with pytest.raises(SystemExit):
        publish(tmp_path, transport, draft())
    receipt = json.loads((tmp_path / ".spec-runner-github-receipts.json").read_text())
    assert receipt["SRAC-publication-contract"]["unknown_keys"] == ["S1"]
    result = publish(tmp_path, transport, draft())
    assert result["receipt"]["complete"]
    assert result["receipt"]["unknown_keys"] == []
    assert transport.posts == 2


def test_completed_receipt_cannot_hide_external_edit(tmp_path):
    transport = Transport()
    publish(tmp_path, transport, draft())
    transport.issues[0]["body"] += "\nExternal edit"
    with pytest.raises(RunnerError) as error:
        publish(tmp_path, transport, draft())
    assert error.value.code == "github_publish_conflict"
    assert transport.posts == 2


def test_invalid_late_item_fails_before_first_publication(tmp_path):
    transport = Transport()
    value = draft()
    value["specs"][0]["parent"] = "MISSING"
    with pytest.raises(RunnerError):
        publish(tmp_path, transport, value)
    assert transport.posts == 0


def test_real_transport_uses_utf8_file_and_bounded_wait(monkeypatch):
    body = "中文需求\nParent: #123\n$not_shell `literal`"
    paths = []

    def run(args, **kwargs):
        value = next(arg for arg in args if arg.startswith("body=@"))
        path = Path(value[6:])
        paths.append(path)
        assert path.read_bytes() == body.encode("utf-8")
        assert args[args.index(value) - 1] == "-F"
        assert kwargs["timeout"] == 120
        return subprocess.CompletedProcess(args, 0, stdout="{}")

    monkeypatch.setattr(subprocess, "run", run)
    assert GitHubTracker._run_gh(["api", "repos/williamxhero/skills/issues", "-f", "body=" + body]) == "{}"
    assert not paths[0].exists()


def test_transport_timeout_is_structured(monkeypatch):
    def run(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RunnerError) as error:
        GitHubTracker._run_gh(["api", "repos/williamxhero/skills/issues"])
    assert error.value.code == "github_timeout"


def test_issue_operations_are_independently_durable_and_read_back(tmp_path):
    transport = Transport()
    operations = {}

    def intent(**identity):
        existing = operations.get(identity["operation_id"])
        if existing:
            assert {key: existing[key] for key in ("operation_kind", "repository", "input_digest")} == {
                key: identity[key] for key in ("operation_kind", "repository", "input_digest")}
            return existing
        operations[identity["operation_id"]] = {**identity, "state": "intent"}
        return operations[identity["operation_id"]]

    def completed(*, operation_id, receipt):
        operations[operation_id].update(state="completed", receipt=receipt)

    def run():
        return GitHubTracker(runner=transport).publish_draft(
            repository="williamxhero/skills", draft=draft(),
            operation_id="SRAC-per-object", receipt_root=tmp_path,
            operation_intent=intent, operation_completed=completed)

    first = run()
    assert set(operations) == {"SRAC-per-object:issue:S1", "SRAC-per-object:issue:T1"}
    assert all(value["state"] == "completed" for value in operations.values())
    transport.issues[1]["body"] += "\nmanual edit"
    with pytest.raises(RunnerError, match="SQLite issue receipt"):
        run()
    assert first["receipt"]["complete"]
    assert transport.posts == 2


def test_native_relations_are_logged_written_and_read_back(tmp_path):
    transport = Transport()
    operations = {}

    def intent(**identity):
        existing = operations.get(identity["operation_id"])
        if existing:
            assert existing["input_digest"] == identity["input_digest"]
            return existing
        operations[identity["operation_id"]] = {**identity, "state": "intent"}
        return operations[identity["operation_id"]]

    def completed(*, operation_id, receipt):
        operations[operation_id].update(state="completed", receipt=receipt)

    value = {"umbrella": {"key": "S1", "title": "SPEC", "body": "Scope"},
             "specs": [{"key": "T1", "title": "Ticket", "body": "Work", "parent": "S1",
                        "blocked_by": ["S1"]}]}
    result = GitHubTracker(runner=transport).publish_draft(
        repository="williamxhero/skills", draft=value, operation_id="SRAC-native",
        receipt_root=tmp_path, relation_mode="native", operation_intent=intent,
        operation_completed=completed)
    assert len([key for key in operations if ":relation:" in key]) == 2
    assert all(operations[key]["state"] == "completed" for key in operations if ":relation:" in key)
    assert result["receipt"]["relation_evidence"]["native"] is True
