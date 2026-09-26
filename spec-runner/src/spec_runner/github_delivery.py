"""Explicit GitHub PR/check/merge adapter used after local candidate gates."""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .errors import RunnerError
from .config import DEFAULT_GITHUB_TIMEOUT_SECONDS
from .github_cli import safe_github_args, validate_github_timeout
from .plans import digest


_DEFINITIVE_PR_CREATE_FAILURES = frozenset({
    "github_auth",
    "github_forbidden",
    "github_not_found",
    "github_rate_limited",
    "github_rejected",
    "github_delivery_unavailable",
})


class GitHubDelivery:
    def __init__(self, *, runner: Callable[[list[str]], str] | None = None,
                 timeout_seconds: float = DEFAULT_GITHUB_TIMEOUT_SECONDS):
        self.timeout_seconds = self._validate_timeout(timeout_seconds)
        self.runner = runner or (lambda args: self._gh(args, timeout_seconds=self.timeout_seconds))

    @staticmethod
    def _validate_timeout(value: float) -> float:
        return validate_github_timeout(value)

    @staticmethod
    def _gh(args: list[str], *, timeout_seconds: float = DEFAULT_GITHUB_TIMEOUT_SECONDS) -> str:
        timeout_seconds = GitHubDelivery._validate_timeout(timeout_seconds)
        try:
            return subprocess.run(["gh", *args], check=True, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout_seconds).stdout
        except subprocess.TimeoutExpired as exc:
            raise RunnerError(
                "github_delivery_timeout",
                "GitHub delivery exceeded its bounded timeout; reconcile before retry",
                details={"args": safe_github_args(args), "timeout_seconds": timeout_seconds},
            ) from exc
        except OSError as exc:
            raise RunnerError("github_delivery_unavailable", "gh CLI is not available") from exc
        except subprocess.CalledProcessError as exc:
            message = (exc.stderr or "").strip()
            status_match = re.search(r"\bHTTP\s+(\d{3})\b", message, re.IGNORECASE)
            status = int(status_match.group(1)) if status_match else None
            lowered = message.lower()
            if status in {401} or "authentication" in lowered:
                code = "github_auth"
            elif status in {403, 404}:
                code = "github_forbidden" if status == 403 else "github_not_found"
            elif status in {429} or "rate limit" in lowered:
                code = "github_rate_limited"
            elif status == 422:
                code = "github_rejected"
            elif status is not None and status >= 500:
                code = "github_server_error"
            else:
                code = "github_delivery_failed"
            raise RunnerError(code, "GitHub delivery command failed", details={
                "exit_code": exc.returncode, "http_status": status, "stderr": message[:1000]}) from exc

    @staticmethod
    def _repo(repository: str) -> None:
        if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
            raise RunnerError("invalid_github_repository", "repository must be owner/name")

    @staticmethod
    def _paged_list(raw: str, *, code: str, label: str) -> list[dict[str, Any]]:
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RunnerError(code, f"{label} response was not valid JSON") from exc
        if isinstance(document, list) and all(isinstance(item, dict) for item in document):
            return document
        if not isinstance(document, list) or any(not isinstance(page, list) for page in document):
            raise RunnerError(code, f"{label} pagination response was incomplete")
        if any(not isinstance(item, dict) for page in document for item in page):
            raise RunnerError(code, f"{label} pagination contained a non-object item")
        result = [item for page in document for item in page]
        return result

    def _read_pull_request(self, *, repository: str, number: int, head: str,
                           base: str, candidate_sha: str, marker: str) -> dict[str, Any]:
        try:
            value = json.loads(self.runner(["api", f"repos/{repository}/pulls/{number}"]))
        except RunnerError:
            raise
        except json.JSONDecodeError as exc:
            raise RunnerError("github_pr_readback_incomplete", "pull request readback was not valid JSON") from exc
        except Exception as exc:
            raise RunnerError("github_pr_readback_incomplete", "pull request readback failed") from exc
        if not isinstance(value, dict):
            raise RunnerError("github_pr_readback_incomplete", "pull request readback was not an object")
        number_value = value.get("number")
        if not isinstance(number_value, int) or isinstance(number_value, bool) or number_value != number:
            raise RunnerError("github_pr_identity_mismatch", "pull request readback has the wrong number")
        head_value = value.get("head")
        base_value = value.get("base")
        if not isinstance(head_value, dict) or not isinstance(base_value, dict):
            raise RunnerError("github_pr_readback_incomplete", "pull request readback has incomplete ref identity")
        base_repo = base_value.get("repo")
        repository_url = base_repo.get("full_name") if isinstance(base_repo, dict) else None
        if not isinstance(repository_url, str) or not repository_url.strip():
            raise RunnerError("github_identity_mismatch", "pull request readback has no repository identity")
        if repository_url != repository:
            raise RunnerError("github_identity_mismatch", "pull request belongs to another repository")
        if (str(head_value.get("sha", "")) != candidate_sha
                or str(head_value.get("ref", "")) != head
                or str(base_value.get("ref", "")) != base
                or marker not in str(value.get("body") or "")):
            raise RunnerError("github_pr_receipt_stale", "pull request no longer matches its operation")
        return value

    @staticmethod
    def _write_receipts(path: Path, receipts: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(receipts, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8", newline="\n")
        temporary.replace(path)

    def create_or_adopt_pr(self, *, repository: str, head: str, base: str, candidate_sha: str, body: str, operation_id: str, receipt_root: Path) -> dict[str, object]:
        self._repo(repository)
        if not all(isinstance(value, str) and value.strip() for value in (head, base, candidate_sha, body, operation_id)):
            raise RunnerError("invalid_pull_request", "PR requires head, base, candidate SHA, body, and operation ID")
        root = receipt_root.expanduser()
        if root.exists() and root.is_symlink():
            raise RunnerError("github_receipt_path_escape", "GitHub receipt root cannot be a symbolic link")
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        path = root / ".spec-runner-pr-receipts.json"
        if path.is_symlink():
            raise RunnerError("github_receipt_path_escape", "GitHub PR receipt file cannot be a symbolic link")
        try:
            receipts = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RunnerError("github_receipt_corrupt", "GitHub PR receipt file is not valid JSON") from exc
        if not isinstance(receipts, dict):
            raise RunnerError("github_receipt_corrupt", "GitHub PR receipt root must be an object")
        old = receipts.get(operation_id)
        if old is not None and not isinstance(old, dict):
            raise RunnerError("github_receipt_corrupt", "GitHub PR operation receipt must be an object")
        if old:
            if old.get("candidate_sha") != candidate_sha:
                raise RunnerError("github_operation_conflict", "PR operation was reused for another candidate")
            number = old.get("number")
            if not isinstance(number, int) or isinstance(number, bool) or number < 1:
                raise RunnerError("github_receipt_corrupt", "PR operation receipt has no valid number")
            expected_marker = str(old.get("marker") or f"<!-- spec-runner-pr:{operation_id} candidate:{candidate_sha} -->")
            readback = self._read_pull_request(repository=repository, number=number, head=head,
                                               base=base, candidate_sha=candidate_sha, marker=expected_marker)
            verified = {**old, "url": readback.get("html_url") or old.get("url"),
                        "state": readback.get("state"), "merged": bool(readback.get("merged")),
                        "merged_at": readback.get("merged_at")}
            receipts[operation_id] = verified
            self._write_receipts(path, receipts)
            return {"created": False, "receipt": verified}
        marker = f"<!-- spec-runner-pr:{operation_id} candidate:{candidate_sha} -->"
        # The query is scoped to the configured repository, head and base. A
        # title similarity is never used as an adoption key.
        list_args = ["api", "--paginate", "--slurp", f"repos/{repository}/pulls", "--method", "GET", "-f", f"head={head}", "-f", f"base={base}", "-f", "state=all", "-f", "per_page=100"]
        raw = self.runner(list_args)
        existing = self._paged_list(raw, code="github_pr_readback_incomplete", label="pull request listing")
        matches = [item for item in existing if isinstance(item, dict) and marker in str(item.get("body") or "") and str(item.get("head", {}).get("sha", "")) == candidate_sha]
        if len(matches) > 1:
            raise RunnerError("github_pr_ambiguous", "multiple run-owned PRs match the candidate")
        if matches:
            item = matches[0]
            if not isinstance(item.get("number"), int) or isinstance(item.get("number"), bool):
                raise RunnerError("github_pr_readback_incomplete", "adopted pull request has no valid number")
            response = self._read_pull_request(repository=repository, number=int(item["number"]), head=head,
                                               base=base, candidate_sha=candidate_sha, marker=marker)
            receipt = {"number": response["number"], "url": response.get("html_url"), "candidate_sha": candidate_sha, "head": head, "base": base, "adopted": True, "marker": marker}
        else:
            adopted_after_reconcile = False
            try:
                response = json.loads(self.runner(["api", f"repos/{repository}/pulls", "--method", "POST", "-f", f"title=Spec Runner {candidate_sha[:12]}", "-f", f"head={head}", "-f", f"base={base}", "-f", f"body={marker}\n{body}"]))
            except RunnerError as exc:
                if exc.code in _DEFINITIVE_PR_CREATE_FAILURES:
                    raise
                # A server or transport failure may have followed an accepted
                # POST. Reconcile by the exact run marker before retrying.
                try:
                    retry_listing = self._paged_list(self.runner(list_args), code="github_pr_readback_incomplete", label="pull request readback")
                except Exception as readback_exc:
                    raise RunnerError("github_pr_unknown", "PR creation outcome and readback are both unknown") from readback_exc
                recovered = [item for item in retry_listing if marker in str(item.get("body") or "") and str(item.get("head", {}).get("sha", "")) == candidate_sha]
                if len(recovered) != 1:
                    raise RunnerError("github_pr_unknown", "PR creation outcome is not uniquely reconciled") from exc
                response = recovered[0]
            except Exception as exc:
                # A lost POST response has an unknown outcome. Re-read the
                # complete scoped listing and adopt exactly one matching PR;
                # never issue a second create based on a timeout alone.
                try:
                    retry_listing = self._paged_list(self.runner(list_args), code="github_pr_readback_incomplete", label="pull request readback")
                except Exception as readback_exc:
                    raise RunnerError("github_pr_unknown", "PR creation outcome and readback are both unknown") from readback_exc
                recovered = [item for item in retry_listing if isinstance(item, dict) and marker in str(item.get("body") or "") and str(item.get("head", {}).get("sha", "")) == candidate_sha]
                if len(recovered) != 1:
                    raise RunnerError("github_pr_unknown", "PR creation outcome is not uniquely reconciled") from exc
                response = recovered[0]
                adopted_after_reconcile = True
            if not isinstance(response, dict) or not isinstance(response.get("number"), int) or isinstance(response.get("number"), bool) or response["number"] < 1:
                raise RunnerError("github_pr_unconfirmed", "GitHub PR create response was not a PR")
            response = self._read_pull_request(repository=repository, number=int(response["number"]), head=head,
                                               base=base, candidate_sha=candidate_sha, marker=marker)
            receipt = {"number": response["number"], "url": response.get("html_url"), "candidate_sha": candidate_sha, "head": head, "base": base, "adopted": adopted_after_reconcile, "marker": marker}
        receipts[operation_id] = receipt
        self._write_receipts(path, receipts)
        return {"created": not receipt.get("adopted", False), "receipt": receipt}

    def checks(self, *, repository: str, candidate_sha: str, required: list[str]) -> dict[str, object]:
        self._repo(repository)
        if not required or any(not isinstance(name, str) or not name.strip() for name in required):
            raise RunnerError("invalid_required_checks", "at least one non-empty required check is required")
        raw = self.runner(["api", "--paginate", "--slurp", "--method", "GET", "-f", "per_page=100", f"repos/{repository}/commits/{candidate_sha}/check-runs"])
        try:
            pages = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RunnerError("github_checks_incomplete", "check-runs response is not valid JSON") from exc
        if isinstance(pages, dict):
            pages = [pages]
        if not isinstance(pages, list) or any(not isinstance(page, dict) for page in pages):
            raise RunnerError("github_checks_incomplete", "check-runs pagination response is incomplete")
        runs = [item for page in pages for item in (page.get("check_runs") or []) if isinstance(item, dict)]
        status_raw = self.runner(["api", "--paginate", "--slurp", "--method", "GET", "-f", "per_page=100", f"repos/{repository}/commits/{candidate_sha}/status"])
        try:
            status_pages = json.loads(status_raw)
        except json.JSONDecodeError as exc:
            raise RunnerError("github_status_incomplete", "status contexts response is not valid JSON") from exc
        if isinstance(status_pages, dict):
            status_pages = [status_pages]
        if not isinstance(status_pages, list) or any(not isinstance(page, dict) for page in status_pages):
            raise RunnerError("github_status_incomplete", "status contexts pagination response is incomplete")
        if any(page.get("sha") != candidate_sha or not isinstance(page.get("statuses"), list)
               or any(not isinstance(item, dict) for item in page["statuses"]) for page in status_pages):
            raise RunnerError("github_status_incomplete", "status contexts do not match the candidate commit")
        statuses = [item for page in status_pages for item in page["statuses"]]
        by_name: dict[str, dict[str, Any]] = {}
        duplicates: dict[str, int] = {}
        for item in runs:
            name = str(item.get("name"))
            duplicates[name] = duplicates.get(name, 0) + 1
            current = by_name.get(name)
            if current is None or str(item.get("completed_at") or item.get("started_at") or "") > str(current.get("completed_at") or current.get("started_at") or ""):
                by_name[name] = item
        by_context: dict[str, dict[str, Any]] = {}
        context_duplicates: dict[str, int] = {}
        for item in statuses:
            name = str(item.get("context") or "")
            if not name:
                continue
            context_duplicates[name] = context_duplicates.get(name, 0) + 1
            current = by_context.get(name)
            if current is None or str(item.get("updated_at") or item.get("created_at") or "") > str(current.get("updated_at") or current.get("created_at") or ""):
                by_context[name] = item
        states: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        for name in required:
            run = by_name.get(name)
            context = by_context.get(name)
            if run is None and context is None:
                missing.append(name)
                continue
            if run is not None:
                states[name] = {"source": "check_run", "status": run.get("status"), "conclusion": run.get("conclusion"), "sha": run.get("head_sha")}
            else:
                states[name] = {"source": "status_context", "status": context.get("state"), "conclusion": context.get("state"), "sha": candidate_sha}
        wrong_sha = [name for name, item in states.items() if item["sha"] != candidate_sha]
        pending = [name for name, item in states.items() if (item["source"] == "check_run" and item["status"] not in {"completed"}) or (item["source"] == "status_context" and item["status"] == "pending")]
        failed = [name for name, item in states.items() if (item["source"] == "check_run" and item["status"] == "completed" and item["conclusion"] not in {"success"}) or (item["source"] == "status_context" and item["status"] in {"error", "failure"})]
        unknown = [name for name, item in states.items() if name not in pending and name not in failed and not (item["status"] == "completed" and item["conclusion"] == "success") and not (item["source"] == "status_context" and item["status"] == "success")]
        return {"candidate_sha": candidate_sha, "required": required, "states": states,
                "duplicates": {name: count for name, count in duplicates.items() if count > 1},
                "status_context_duplicates": {name: count for name, count in context_duplicates.items() if count > 1},
                "missing": missing, "wrong_sha": wrong_sha, "pending": pending, "failed": failed,
                "unknown": unknown,
                "ready": not missing and not wrong_sha and not pending and not failed and not unknown}

    def _approved_reviews(self, *, repository: str, number: int) -> dict[str, object]:
        """Read the complete review history and project latest user states."""
        raw = self.runner([
            "api", "--paginate", "--slurp", "--method", "GET", "-f", "per_page=100",
            f"repos/{repository}/pulls/{number}/reviews",
        ])
        reviews = self._paged_list(raw, code="github_reviews_incomplete", label="pull request reviews")
        latest: dict[str, tuple[tuple[datetime, int], dict[str, Any]]] = {}
        for review in reviews:
            user = review.get("user")
            login = user.get("login") if isinstance(user, dict) else None
            state = review.get("state")
            if not isinstance(login, str) or not login.strip() or not isinstance(state, str):
                raise RunnerError("github_reviews_incomplete", "pull request review lacks stable reviewer identity")
            if state.upper() == "PENDING":
                continue
            submitted_at = review.get("submitted_at")
            review_id = review.get("id")
            if not isinstance(submitted_at, str) or not isinstance(review_id, int) or isinstance(review_id, bool):
                raise RunnerError("github_reviews_incomplete", "submitted review lacks a timestamp or numeric identity")
            try:
                timestamp = datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise RunnerError("github_reviews_incomplete", "submitted review has an invalid timestamp") from exc
            if timestamp.tzinfo is None:
                raise RunnerError("github_reviews_incomplete", "submitted review timestamp has no timezone")
            order = (timestamp.astimezone(timezone.utc), review_id)
            previous = latest.get(login)
            if previous is None or order >= previous[0]:
                latest[login] = (order, {"login": login, "state": state})
        approved = sorted(login for login, (_, review) in latest.items() if review["state"].upper() == "APPROVED")
        return {
            "approved": approved,
            "approved_count": len(approved),
            "reviewers": [{"login": login, "state": review[1]["state"]} for login, review in sorted(latest.items())],
        }

    def _branch_protection(self, *, repository: str, base: str) -> dict[str, object]:
        """Read branch protection when the production contract requires it."""
        try:
            value = json.loads(self.runner(["api", f"repos/{repository}/branches/{base}/protection"]))
        except RunnerError as exc:
            if exc.code == "github_not_found":
                raise RunnerError("github_branch_protection_missing", "required branch protection was not found") from exc
            raise
        except json.JSONDecodeError as exc:
            raise RunnerError("github_protection_incomplete", "branch protection response was not valid JSON") from exc
        if not isinstance(value, dict):
            raise RunnerError("github_protection_incomplete", "branch protection response was not an object")
        return value

    @staticmethod
    def _validate_merge_readback(pr: object, *, repository: str, number: int,
                                 expected_head: str, expected_head_ref: str | None,
                                 expected_base: str) -> dict[str, Any]:
        if not isinstance(pr, dict) or pr.get("number") != number:
            raise RunnerError("github_merge_readback_incomplete", "pull request readback has the wrong identity")
        head = pr.get("head")
        base = pr.get("base")
        if not isinstance(head, dict) or not isinstance(base, dict):
            raise RunnerError("github_merge_readback_incomplete", "pull request readback has incomplete ref identity")
        head_repo = head.get("repo")
        base_repo = base.get("repo")
        if (not isinstance(head_repo, dict) or head_repo.get("full_name") != repository
                or not isinstance(base_repo, dict) or base_repo.get("full_name") != repository):
            raise RunnerError("github_identity_mismatch", "pull request refs do not belong to the configured repository")
        if (head.get("sha") != expected_head or base.get("ref") != expected_base
                or (expected_head_ref is not None and head.get("ref") != expected_head_ref)):
            raise RunnerError("stale_pull_request", "PR head or base changed before merge")
        return pr

    def _enqueue_merge_queue(self, *, pull_request_id: str) -> dict[str, object] | None:
        query = (
            "mutation($pullRequestId: ID!) { enqueuePullRequest(input: {pullRequestId: $pullRequestId}) "
            "{ mergeQueueEntry { id position state } } }"
        )
        try:
            raw = self.runner(["api", "graphql", "-f", f"query={query}", "-F", f"pullRequestId={pull_request_id}"])
            document = json.loads(raw)
        except RunnerError as exc:
            if exc.code in {"github_not_found", "github_rejected"}:
                return None
            raise
        except json.JSONDecodeError:
            return None
        if not isinstance(document, dict) or document.get("errors"):
            return None
        data = document.get("data")
        enqueue = data.get("enqueuePullRequest") if isinstance(data, dict) else None
        entry = enqueue.get("mergeQueueEntry") if isinstance(enqueue, dict) else None
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            return None
        return {key: entry.get(key) for key in ("id", "position", "state")}

    def merge(self, *, repository: str, number: int, expected_head: str,
              expected_base: str | None = None,
              candidate_receipt: dict[str, object] | None = None,
              review: dict[str, object] | None = None,
              checks: dict[str, object] | None = None,
              allow: bool = False,
              expected_head_ref: str | None = None,
              required_approvals: int = 0,
              require_branch_protection: bool = False,
              queue_entry: dict[str, object] | None = None) -> dict[str, object]:
        self._repo(repository)
        if not allow:
            raise RunnerError("merge_not_authorized", "merge requires an explicit Runner authorization")
        if not isinstance(candidate_receipt, dict) or not isinstance(review, dict) or not isinstance(checks, dict):
            raise RunnerError("merge_evidence_missing", "merge requires candidate, review, and checks receipts")
        if expected_base is None or not expected_base.strip():
            raise RunnerError("merge_evidence_missing", "merge requires the expected base ref")
        if (isinstance(required_approvals, bool) or not isinstance(required_approvals, int)
                or required_approvals < 0):
            raise RunnerError("merge_evidence_invalid", "required approvals must be a non-negative integer")
        if (candidate_receipt.get("outcome") != "verified"
                or candidate_receipt.get("candidate_sha") != expected_head):
            raise RunnerError("candidate_not_verified", "merge requires a verified candidate receipt for the expected head")
        if (review.get("approved") is not True
                or review.get("candidate_sha") != expected_head
                or not isinstance(review.get("review_digest"), str)
                or not review["review_digest"].strip()):
            raise RunnerError("review_not_verified", "merge requires an independent review bound to the expected head")
        if checks.get("candidate_sha") != expected_head or checks.get("ready") is not True:
            raise RunnerError("checks_not_verified", "merge requires checks verified for the expected head")
        try:
            pr = json.loads(self.runner(["api", f"repos/{repository}/pulls/{number}"]))
        except json.JSONDecodeError as exc:
            raise RunnerError("github_merge_readback_incomplete", "pull request readback was not valid JSON") from exc
        pr = self._validate_merge_readback(
            pr, repository=repository, number=number, expected_head=expected_head,
            expected_head_ref=expected_head_ref, expected_base=expected_base,
        )
        if pr.get("draft") is True:
            raise RunnerError("github_merge_blocked", "draft pull request cannot be merged")
        if pr.get("state") == "closed" and not pr.get("merged"):
            raise RunnerError("github_pull_request_closed", "pull request is closed without a merge")
        if pr.get("mergeable_state") == "dirty":
            raise RunnerError("github_merge_blocked", "GitHub reports that the pull request cannot be merged")
        queue_required = pr.get("mergeable_state") == "blocked"
        protection: dict[str, object] | None = None
        if require_branch_protection:
            protection = self._branch_protection(repository=repository, base=expected_base)
            if not isinstance(protection.get("required_status_checks"), dict):
                raise RunnerError("github_protection_incomplete", "required branch protection lacks status checks")
            if not isinstance(protection.get("required_pull_request_reviews"), dict):
                raise RunnerError("github_protection_incomplete", "required branch protection lacks pull request reviews")
        approvals: dict[str, object] | None = None
        if required_approvals:
            approvals = self._approved_reviews(repository=repository, number=number)
            if int(approvals["approved_count"]) < required_approvals:
                raise RunnerError(
                    "github_approvals_insufficient",
                    "pull request does not have the required number of approvals",
                    details={"required": required_approvals, "actual": approvals["approved_count"]},
                )
        latest_checks = self.checks(repository=repository, candidate_sha=expected_head,
                                    required=[str(item) for item in checks.get("required", [])])
        if latest_checks.get("ready") is not True:
            raise RunnerError("checks_changed_before_merge", "required checks are no longer ready at merge time", details={"checks": latest_checks})
        if pr.get("merged") is True:
            if not pr.get("merged_at") or not pr.get("merge_commit_sha"):
                raise RunnerError("github_merge_readback_incomplete", "merged pull request lacks merge identity")
            evidence = {"pr_readback": pr, "candidate_receipt": candidate_receipt,
                        "review": review, "checks_before": checks, "checks_at_merge": latest_checks,
                        "approvals": approvals, "protection": protection}
            return {"number": number, "expected_head": expected_head, "expected_base": expected_base,
                    "merged": True, "adopted": True, "sha": pr.get("merge_commit_sha"),
                    "message": "pull request was already merged", "merged_at": pr.get("merged_at"),
                    "evidence_digest": digest(evidence)}
        if queue_entry is None and queue_required:
            queue_entry = self._enqueue_merge_queue(
                pull_request_id=str(pr.get("node_id"))
            ) if isinstance(pr.get("node_id"), str) and pr.get("node_id") else None
            if queue_entry is None:
                raise RunnerError("github_merge_blocked", "GitHub reports that the pull request cannot be merged")
        if queue_entry is not None:
            return {
                "number": number, "expected_head": expected_head, "expected_base": expected_base,
                "merged": False, "waiting": True, "queue": queue_entry,
                "message": "pull request remains in the GitHub merge queue",
            }
        merge_args = ["api", f"repos/{repository}/pulls/{number}/merge", "--method", "PUT", "-f", "sha=" + expected_head]
        merge_error: Exception | None = None
        try:
            result = json.loads(self.runner(merge_args))
        except Exception as exc:
            merge_error = exc
            try:
                recovered = json.loads(self.runner(["api", f"repos/{repository}/pulls/{number}"]))
            except Exception as readback_exc:
                raise RunnerError("github_merge_unknown", "merge outcome and exact PR readback are both unknown") from readback_exc
            readback = self._validate_merge_readback(
                recovered, repository=repository, number=number, expected_head=expected_head,
                expected_head_ref=expected_head_ref, expected_base=expected_base,
            )
            result = {"merged": readback.get("merged") is True,
                      "sha": readback.get("merge_commit_sha"), "reconciled": True}
        else:
            if not isinstance(result, dict):
                raise RunnerError("github_merge_unconfirmed", "merge response was not an object")
            try:
                readback = json.loads(self.runner(["api", f"repos/{repository}/pulls/{number}"]))
            except Exception as exc:
                raise RunnerError("github_merge_unknown", "merge response was received but PR outcome is unknown") from exc
            readback = self._validate_merge_readback(
                readback, repository=repository, number=number, expected_head=expected_head,
                expected_head_ref=expected_head_ref, expected_base=expected_base,
            )
        if readback.get("merged") is not True:
            if merge_error is not None:
                raise RunnerError(
                    "github_merge_unknown",
                    "merge response was lost and the PR is not confirmed merged",
                    details={"number": number, "expected_head": expected_head},
                ) from merge_error
            queue_entry = self._enqueue_merge_queue(
                pull_request_id=str(readback.get("node_id"))
            ) if isinstance(readback.get("node_id"), str) and readback.get("node_id") else None
            if queue_entry is None:
                raise RunnerError(
                    "github_merge_not_applied",
                    "GitHub merge was not confirmed and no merge queue entry was created",
                    details={"message": result.get("message"), "number": number,
                             "expected_head": expected_head,
                             "merge_error": str(merge_error)[:500] if merge_error else None},
                ) from merge_error
            try:
                queued = json.loads(self.runner(["api", f"repos/{repository}/pulls/{number}"]))
            except Exception as exc:
                raise RunnerError("github_merge_unknown", "merge queue entry was created but PR state is unknown") from exc
            queued = self._validate_merge_readback(
                queued, repository=repository, number=number, expected_head=expected_head,
                expected_head_ref=expected_head_ref, expected_base=expected_base,
            )
            if queued.get("merged") is not True:
                return {
                    "number": number, "expected_head": expected_head, "expected_base": expected_base,
                    "merged": False, "waiting": True, "queue": queue_entry,
                    "message": "pull request is waiting in the GitHub merge queue",
                }
            readback = queued
            result = {"merged": True, "sha": readback.get("merge_commit_sha"), "reconciled": True}
        if not readback.get("merged_at") or not readback.get("merge_commit_sha"):
            raise RunnerError("github_merge_readback_incomplete", "merged pull request lacks merge identity")
        merged_at = readback.get("merged_at")
        merge_sha = result.get("sha") or readback.get("merge_commit_sha")
        evidence = {"merge_response": result, "pr_readback": readback, "candidate_receipt": candidate_receipt,
                    "review": review, "checks_before": checks, "checks_at_merge": latest_checks,
                    "approvals": approvals, "protection": protection}
        return {"number": number, "expected_head": expected_head, "expected_base": expected_base,
                "merged": True, "sha": merge_sha, "message": result.get("message"),
                "merged_at": merged_at, "evidence_digest": digest(evidence)}
