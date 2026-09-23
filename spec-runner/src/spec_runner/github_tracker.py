from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path
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
            # Keep arbitrary Unicode issue bodies out of shell/CLI argument
            # encoding. gh reads UTF-8 without BOM from the file directly.
            with tempfile.TemporaryDirectory(prefix="spec-runner-gh-") as directory:
                prepared = list(arguments)
                for index, value in enumerate(prepared):
                    if value.startswith("body=") and index and prepared[index - 1] == "-f":
                        body_file = Path(directory) / f"body-{index}.md"
                        body_file.write_text(value[5:], encoding="utf-8", newline="\n")
                        prepared[index - 1] = "-F"
                        prepared[index] = f"body=@{body_file}"
                result = subprocess.run(["gh", *prepared], check=True, capture_output=True,
                                        text=True, encoding="utf-8", timeout=120)
        except subprocess.TimeoutExpired as exc:
            raise RunnerError("github_timeout", "GitHub command exceeded 120 seconds; reconcile writes before retry") from exc
        except FileNotFoundError as exc:
            raise RunnerError("github_unavailable", "gh CLI is not installed") from exc
        except subprocess.CalledProcessError as exc:
            message = (exc.stderr or "").strip()
            code = "github_auth" if exc.returncode == 4 or "auth" in message.lower() else "github_read_failed"
            raise RunnerError(code, "GitHub read failed", details={"exit_code": exc.returncode}) from exc
        return result.stdout

    def _issue(self, repository: str, number: int) -> dict[str, Any]:
        try:
            value = json.loads(self._runner(["api", f"repos/{repository}/issues/{number}"]))
        except json.JSONDecodeError as exc:
            raise RunnerError("github_issue_read_failed", "GitHub issue response was not valid JSON") from exc
        if not isinstance(value, dict) or value.get("repository_url", "").split("/repos/")[-1] != repository:
            raise RunnerError("github_identity_mismatch", "GitHub response repository does not match configured repository")
        return value

    def _comments(self, repository: str, number: int) -> list[dict[str, Any]]:
        raw = self._runner(["api", "--paginate", "--slurp", f"repos/{repository}/issues/{number}/comments"])
        try:
            pages = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RunnerError("github_pagination_incomplete", "GitHub comments response was not valid JSON") from exc
        if not isinstance(pages, list):
            raise RunnerError("github_pagination_incomplete", "GitHub comments response was not a page list")
        comments: list[dict[str, Any]] = []
        for page in pages:
            if not isinstance(page, list):
                raise RunnerError("github_pagination_incomplete", "GitHub comments page was not a list")
            comments.extend(item for item in page if isinstance(item, dict))
        return comments

    def _run_owned_issue(self, *, repository: str, marker: str, title: str, body: str) -> dict[str, Any] | None:
        """Find one exact run-owned issue, or fail closed on ambiguity/editing."""
        raw = self._runner(["api", "--paginate", "--slurp", f"repos/{repository}/issues?state=all&per_page=100"])
        try:
            pages = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RunnerError("github_pagination_incomplete", "GitHub issue listing was not valid JSON") from exc
        if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
            raise RunnerError("github_pagination_incomplete", "GitHub issue listing was not a complete page list")
        matches = [
            item
            for page in pages
            for item in page
            if isinstance(item, dict) and marker in str(item.get("body") or "")
        ]
        if len(matches) > 1:
            raise RunnerError("github_publish_ambiguous", "multiple run-owned issues match the operation marker")
        if not matches:
            return None
        item = matches[0]
        repository_url = str(item.get("repository_url") or "")
        if repository_url.split("/repos/")[-1] != repository:
            raise RunnerError("github_identity_mismatch", "run-owned issue belongs to another repository")
        if item.get("pull_request"):
            raise RunnerError("github_publish_ambiguous", "operation marker was found on a pull request")
        expected_body = f"{marker}\n{body}"
        if str(item.get("title") or "") != title or str(item.get("body") or "") != expected_body:
            raise RunnerError("github_publish_conflict", "run-owned issue was edited after creation")
        return item

    @staticmethod
    def _body_relations(body: str) -> tuple[str | None, tuple[str, ...]]:
        parent = re.search(r"(?im)^\s*parent\s*:?\s*#(\d+)\s*$", body)
        blocked = re.findall(r"(?im)^\s*blocked[- ]by\s*:?\s*#(\d+)\s*$", body)
        return (f"GH-{parent.group(1)}" if parent else None, tuple(f"GH-{value}" for value in sorted(set(blocked))))

    def read_issue(self, *, repository: str, number: int, linked_numbers: list[int] | None = None) -> GitHubReadResult:
        if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
            raise RunnerError("invalid_github_repository", "repository must be owner/name")
        numbers = [number] + [value for value in (linked_numbers or []) if value != number]
        records: list[IssueRecord] = []
        body_links: dict[str, list[str]] = {}
        body_relations: dict[str, dict[str, object]] = {}
        unresolved_relations: list[dict[str, str]] = []
        requested_keys = {f"GH-{value}" for value in numbers}
        for issue_number in numbers:
            issue = self._issue(repository, issue_number)
            comments = self._comments(repository, issue_number)
            body = str(issue.get("body") or "")
            linked = sorted(set(re.findall(r"(?<!\d)#(\d+)", body)))
            key = f"GH-{issue_number}"
            parent, blocked_by = self._body_relations(body)
            if parent and parent not in requested_keys:
                unresolved_relations.append({"issue": key, "relation": "parent", "target": parent})
                parent = None
            blocked_by = tuple(value for value in blocked_by if value in requested_keys)
            for relation_target in re.findall(r"(?im)^\s*blocked[- ]by\s*:?\s*#(\d+)\s*$", body):
                target = f"GH-{relation_target}"
                if target not in requested_keys:
                    unresolved_relations.append({"issue": key, "relation": "blocked_by", "target": target})
            body_relations[key] = {"parent": parent, "blocked_by": list(blocked_by)}
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
                    parent=parent,
                    blocked_by=blocked_by,
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
            relation_evidence={"native": False, "body_links": body_links, "body_relations": body_relations, "unresolved_relations": unresolved_relations, "complete_pagination": True},
        )

    def publish_draft(self, *, repository: str, draft: dict[str, Any], operation_id: str, receipt_root: Path, relation_mode: str = "body_links",
                      operation_intent: Callable[..., dict[str, object]] | None = None,
                      operation_completed: Callable[..., None] | None = None) -> dict[str, object]:
        """Publish only a validated draft and reconcile every response.

        GitHub installations differ in availability of sub-issue and dependency
        endpoints.  The caller must opt into ``native`` and provide a tested
        writer; otherwise the receipt explicitly records body-link semantics.
        """
        if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
            raise RunnerError("invalid_github_repository", "repository must be owner/name")
        if relation_mode not in {"body_links", "native"}:
            raise RunnerError("invalid_relation_mode", "relation_mode must be body_links or native")
        # Native relation writes are not implemented in this adapter.  Refuse
        # before creating even the first issue; otherwise a capability blocker
        # would leave a partially published draft with no durable receipt.
        if relation_mode == "native":
            raise RunnerError("github_native_relations_unavailable", "native relation writer must be explicitly implemented and capability-verified")
        specs = draft.get("specs")
        if not isinstance(specs, list) or not specs:
            raise RunnerError("invalid_publish_draft", "publish draft needs at least one SPEC")
        receipt_root = receipt_root.expanduser()
        if receipt_root.exists() and receipt_root.is_symlink():
            raise RunnerError("github_receipt_path_escape", "GitHub receipt root cannot be a symbolic link")
        receipt_root = receipt_root.resolve()
        receipt_root.mkdir(parents=True, exist_ok=True)
        receipt_file = receipt_root / ".spec-runner-github-receipts.json"
        if receipt_file.is_symlink():
            raise RunnerError("github_receipt_path_escape", "GitHub receipt file cannot be a symbolic link")
        try:
            receipts = json.loads(receipt_file.read_text(encoding="utf-8")) if receipt_file.exists() else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RunnerError("github_receipt_corrupt", "GitHub receipt file is not valid JSON") from exc
        if not isinstance(receipts, dict):
            raise RunnerError("github_receipt_corrupt", "GitHub receipt root must be an object")
        previous = receipts.get(operation_id)
        if previous is not None and not isinstance(previous, dict):
            raise RunnerError("github_receipt_corrupt", "GitHub operation receipt must be an object")
        draft_digest = _digest(draft)
        if previous:
            if previous.get("draft_digest") != draft_digest:
                raise RunnerError("github_operation_conflict", "operation_id was reused with another draft")
            if previous.get("repository") != repository:
                raise RunnerError("github_operation_conflict", "operation belongs to another repository")
        published: list[dict[str, object]] = list(previous.get("issues", [])) if isinstance(previous, dict) else []
        published_by_key = {str(item.get("key")): item for item in published if isinstance(item, dict) and item.get("key")}
        receipt = {
            "operation_id": operation_id,
            "draft_digest": draft_digest,
            "repository": repository,
            "issues": published,
            "relation_evidence": {"mode": relation_mode, "native": False, "written": []},
            "unknown_keys": list(previous.get("unknown_keys", [])) if isinstance(previous, dict) else [],
            "complete": False,
        }

        def save_progress() -> None:
            receipts[operation_id] = receipt
            temporary = receipt_file.with_suffix(receipt_file.suffix + ".tmp")
            temporary.write_text(json.dumps(receipts, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
            temporary.replace(receipt_file)

        # A single SPEC need not have an umbrella, but an explicitly supplied
        # parent must not disappear when it has only one child ticket.
        issue_items = list(specs)
        umbrella = draft.get("umbrella")
        if len(specs) > 1 or umbrella is not None:
            if not isinstance(umbrella, dict) or not umbrella.get("title") or not umbrella.get("body"):
                raise RunnerError("invalid_publish_draft", "multiple SPECs require an explicit umbrella draft")
            issue_items = [umbrella, *issue_items]
        seen: set[str] = set()
        for item in issue_items:
            if not isinstance(item, dict) or not all(isinstance(item.get(field), str) and item[field].strip() for field in ("key", "title", "body")):
                raise RunnerError("invalid_publish_draft", "each issue needs key, title, and body")
            key = item["key"]
            if key in seen:
                raise RunnerError("invalid_publish_draft", "duplicate issue key")
            if not isinstance(item.get("blocked_by", []), list):
                raise RunnerError("invalid_publish_draft", "blocked_by must be a list of keys")
            relations = ([item["parent"]] if item.get("parent") else []) + item.get("blocked_by", [])
            if any(not isinstance(target, str) or target not in seen for target in relations):
                raise RunnerError("invalid_publish_draft", "relations must reference earlier issue keys")
            seen.add(key)
        created = False
        for item in issue_items:
            key = item.get("key")
            title = item.get("title")
            body = item.get("body")
            if item.get("parent"):
                body += f"\n\nParent: #{published_by_key[item['parent']]['number']}"
            for dependency in item.get("blocked_by", []):
                body += f"\nBlocked by: #{published_by_key[dependency]['number']}"
            if not all(isinstance(value, str) and value.strip() for value in (key, title, body)):
                raise RunnerError("invalid_publish_draft", "each issue needs key, title, and body")
            marker = f"<!-- spec-runner-key:{key} operation:{operation_id} -->"
            rendered_body = f"{marker}\n{body}"
            item_operation_id = f"{operation_id}:issue:{key}"
            operation_state: dict[str, object] | None = None
            if operation_intent is not None:
                operation_state = operation_intent(
                    operation_id=item_operation_id,
                    operation_kind="github_issue_publication",
                    repository=repository,
                    input_digest=_digest({"key": key, "title": title, "body": rendered_body}),
                )
            if operation_state and operation_state.get("state") == "completed":
                operation_receipt = operation_state.get("receipt")
                if not isinstance(operation_receipt, dict) or not operation_receipt.get("number"):
                    raise RunnerError("github_receipt_corrupt", "completed issue operation has no issue identity")
                current = self._issue(repository, int(operation_receipt["number"]))
                if current.get("pull_request") or current.get("title") != title or current.get("body") != rendered_body:
                    raise RunnerError("github_publish_conflict", "SQLite issue receipt no longer matches the GitHub issue")
                item_receipt = operation_receipt
                published_by_key[key] = item_receipt
                if item_receipt not in published:
                    published.append(item_receipt)
                receipt["issues"] = published
                receipt["unknown_keys"] = [unknown for unknown in receipt["unknown_keys"] if unknown != key]
                save_progress()
                continue
            prior = published_by_key.get(key)
            if prior:
                if prior.get("marker") != marker:
                    raise RunnerError("github_operation_conflict", "published issue marker changed for the same operation")
                current = self._issue(repository, int(prior["number"]))
                if current.get("title") != title or current.get("body") != f"{marker}\n{body}" or current.get("pull_request"):
                    raise RunnerError("github_publish_conflict", "published issue changed since its receipt")
                continue
            response: dict[str, Any] | None
            if key in receipt["unknown_keys"]:
                response = self._run_owned_issue(repository=repository, marker=marker, title=title, body=body)
                if response is None:
                    raise RunnerError("github_publish_unknown", "a previous issue creation has unknown outcome; unique readback is still unavailable")
            else:
                response = None
            try:
                if response is None:
                    # Persist uncertainty BEFORE dispatch: process termination
                    # cannot be caught by the lost-response exception handler.
                    receipt["unknown_keys"].append(key)
                    save_progress()
                    raw_response = self._runner(["api", f"repos/{repository}/issues", "--method", "POST", "-f", f"title={title}", "-f", f"body={marker}\n{body}"])
                    response = json.loads(raw_response)
            except Exception as exc:
                # The POST may have been accepted before its response was
                # lost. Reconcile by the formal run marker, never by title.
                response = self._run_owned_issue(repository=repository, marker=marker, title=title, body=body)
                if response is None:
                    save_progress()
                    raise RunnerError("github_publish_unknown", "issue creation outcome is unknown and no unique marker readback exists") from exc
            if not isinstance(response, dict) or not response.get("number") or str(response.get("repository_url") or "").split("/repos/")[-1] != repository:
                raise RunnerError("github_publish_unconfirmed", "GitHub create response lacks matching repository identity")
            response = self._issue(repository, int(response["number"]))
            if response.get("pull_request") or str(response.get("body") or "") != rendered_body or str(response.get("title") or "") != title:
                raise RunnerError("github_publish_conflict", "GitHub create response does not match the submitted issue")
            item_receipt = {"key": key, "number": int(response["number"]), "node_id": response.get("node_id"), "marker": marker}
            if operation_completed is not None:
                operation_completed(operation_id=item_operation_id, receipt=item_receipt)
            published.append(item_receipt)
            published_by_key[key] = item_receipt
            receipt["unknown_keys"] = [unknown for unknown in receipt["unknown_keys"] if unknown != key]
            receipt["issues"] = published
            created = True
            save_progress()
        receipt["complete"] = True
        save_progress()
        return {"created": created, "receipt": receipt}
