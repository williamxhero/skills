from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

from .errors import RunnerError
from .tracker import IssueRecord, PlanSnapshot


@dataclass(frozen=True)
class GitHubReadResult:
    snapshot: PlanSnapshot
    relation_evidence: dict[str, object]


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class GitHubTracker:
    def __init__(self, *, runner: Callable[[list[str]], str] | None = None):
        self._runner = runner or self._run_gh

    @staticmethod
    def _run_gh(arguments: list[str]) -> str:
        try:
            result = subprocess.run(["gh", *arguments], check=True, capture_output=True, text=True, encoding="utf-8")
        except FileNotFoundError as exc:
            raise RunnerError("github_unavailable", "gh CLI is not installed") from exc
        except subprocess.CalledProcessError as exc:
            message = (exc.stderr or "").strip()
            code = "github_auth" if exc.returncode == 4 or "auth" in message.lower() else "github_read_failed"
            raise RunnerError(code, "GitHub read failed", details={"exit_code": exc.returncode}) from exc
        return result.stdout

    def _issue(self, repository: str, number: int) -> dict[str, Any]:
        value = json.loads(self._runner(["api", f"repos/{repository}/issues/{number}"]))
        if not isinstance(value, dict) or value.get("repository_url", "").split("/repos/")[-1] != repository:
            raise RunnerError("github_identity_mismatch", "GitHub response repository does not match configured repository")
        return value

    def _comments(self, repository: str, number: int) -> list[dict[str, Any]]:
        raw = self._runner(["api", "--paginate", "--slurp", f"repos/{repository}/issues/{number}/comments"])
        pages = json.loads(raw)
        if not isinstance(pages, list):
            raise RunnerError("github_pagination_incomplete", "GitHub comments response was not a page list")
        comments: list[dict[str, Any]] = []
        for page in pages:
            if not isinstance(page, list):
                raise RunnerError("github_pagination_incomplete", "GitHub comments page was not a list")
            comments.extend(item for item in page if isinstance(item, dict))
        return comments

    def read_issue(self, *, repository: str, number: int, linked_numbers: list[int] | None = None) -> GitHubReadResult:
        if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
            raise RunnerError("invalid_github_repository", "repository must be owner/name")
        numbers = [number] + [value for value in (linked_numbers or []) if value != number]
        records: list[IssueRecord] = []
        body_links: dict[str, list[str]] = {}
        for issue_number in numbers:
            issue = self._issue(repository, issue_number)
            comments = self._comments(repository, issue_number)
            body = str(issue.get("body") or "")
            linked = sorted(set(re.findall(r"(?<!\d)#(\d+)", body)))
            key = f"GH-{issue_number}"
            records.append(
                IssueRecord(
                    key=key,
                    source="github",
                    repository=repository,
                    path=f"github://{repository}/issues/{issue_number}",
                    external_id=str(issue.get("node_id") or issue.get("id") or issue_number),
                    kind="issue",
                    title=str(issue.get("title") or ""),
                    body=body,
                    comments=tuple(str(comment.get("body") or "") for comment in comments),
                    parent=None,
                    blocked_by=tuple(),
                    revision=str(issue.get("updated_at") or issue.get("id") or ""),
                    digest=_digest({"issue": issue, "comments": comments}),
                )
            )
            body_links[key] = [f"GH-{value}" for value in linked]
        records_tuple = tuple(sorted(records, key=lambda record: record.key))
        snapshot_digest = _digest([record.public() for record in records_tuple])
        snapshot = PlanSnapshot("spec-runner-github/v1", "github", repository, "github_body_links", records_tuple, snapshot_digest)
        return GitHubReadResult(
            snapshot=snapshot,
            relation_evidence={"native": False, "body_links": body_links, "complete_pagination": True},
        )
