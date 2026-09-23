"""Explicit GitHub PR/check/merge adapter used after local candidate gates."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from .errors import RunnerError
from .plans import digest


class GitHubDelivery:
    def __init__(self, *, runner: Callable[[list[str]], str] | None = None):
        self.runner = runner or self._gh

    @staticmethod
    def _gh(args: list[str]) -> str:
        try:
            return subprocess.run(["gh", *args], check=True, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=120).stdout
        except subprocess.TimeoutExpired as exc:
            raise RunnerError("github_delivery_timeout", "GitHub delivery exceeded the 120 second boundary; reconcile before retry") from exc
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
        result = [item for page in document for item in page if isinstance(item, dict)]
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
        number_value = value.get("number", number)
        if not isinstance(number_value, int) or isinstance(number_value, bool) or number_value != number:
            raise RunnerError("github_pr_identity_mismatch", "pull request readback has the wrong number")
        repository_url = str(value.get("base", {}).get("repo", {}).get("full_name") or "")
        if repository_url and repository_url != repository:
            raise RunnerError("github_identity_mismatch", "pull request belongs to another repository")
        if (str(value.get("head", {}).get("sha", "")) != candidate_sha
                or str(value.get("head", {}).get("ref", "")) != head
                or str(value.get("base", {}).get("ref", "")) != base
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
        raw = self.runner(["api", "--paginate", "--slurp", "-f", "per_page=100", f"repos/{repository}/commits/{candidate_sha}/check-runs"])
        try:
            pages = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RunnerError("github_checks_incomplete", "check-runs response is not valid JSON") from exc
        if isinstance(pages, dict):
            pages = [pages]
        if not isinstance(pages, list) or any(not isinstance(page, dict) for page in pages):
            raise RunnerError("github_checks_incomplete", "check-runs pagination response is incomplete")
        runs = [item for page in pages for item in (page.get("check_runs") or []) if isinstance(item, dict)]
        status_raw = self.runner(["api", "--paginate", "--slurp", "-f", "per_page=100", f"repos/{repository}/commits/{candidate_sha}/status"])
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

    def merge(self, *, repository: str, number: int, expected_head: str,
              expected_base: str | None = None,
              candidate_receipt: dict[str, object] | None = None,
              review: dict[str, object] | None = None,
              checks: dict[str, object] | None = None,
              allow: bool = False) -> dict[str, object]:
        self._repo(repository)
        if not allow:
            raise RunnerError("merge_not_authorized", "merge requires an explicit Runner authorization")
        if not isinstance(candidate_receipt, dict) or not isinstance(review, dict) or not isinstance(checks, dict):
            raise RunnerError("merge_evidence_missing", "merge requires candidate, review, and checks receipts")
        if expected_base is None or not expected_base.strip():
            raise RunnerError("merge_evidence_missing", "merge requires the expected base ref")
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
        if not isinstance(pr, dict) or pr.get("head", {}).get("sha") != expected_head or pr.get("base", {}).get("ref") != expected_base:
            raise RunnerError("stale_pull_request", "PR head changed before merge")
        if pr.get("state") == "closed" and not pr.get("merged"):
            raise RunnerError("github_pull_request_closed", "pull request is closed without a merge")
        if pr.get("mergeable_state") in {"dirty", "blocked"}:
            raise RunnerError("github_merge_blocked", "GitHub reports that the pull request cannot be merged")
        latest_checks = self.checks(repository=repository, candidate_sha=expected_head,
                                    required=[str(item) for item in checks.get("required", [])])
        if latest_checks.get("ready") is not True:
            raise RunnerError("checks_changed_before_merge", "required checks are no longer ready at merge time", details={"checks": latest_checks})
        if pr.get("merged") is True:
            evidence = {"pr_readback": pr, "candidate_receipt": candidate_receipt,
                        "review": review, "checks_before": checks, "checks_at_merge": latest_checks}
            return {"number": number, "expected_head": expected_head, "expected_base": expected_base,
                    "merged": True, "adopted": True, "sha": pr.get("merge_commit_sha"),
                    "message": "pull request was already merged", "merged_at": pr.get("merged_at"),
                    "evidence_digest": digest(evidence)}
        result = json.loads(self.runner(["api", f"repos/{repository}/pulls/{number}/merge", "--method", "PUT", "-f", "sha=" + expected_head]))
        if not isinstance(result, dict):
            raise RunnerError("github_merge_unconfirmed", "merge response was not an object")
        readback = json.loads(self.runner(["api", f"repos/{repository}/pulls/{number}"]))
        if not isinstance(readback, dict):
            raise RunnerError("github_merge_readback_incomplete", "GitHub PR merge readback was not an object")
        merged_at = readback.get("merged_at")
        applied = bool(result.get("merged")) or bool(readback.get("merged")) or bool(merged_at)
        if not applied:
            raise RunnerError(
                "github_merge_not_applied",
                "GitHub merge request was not confirmed as applied",
                details={"message": result.get("message"), "number": number, "expected_head": expected_head},
            )
        merge_sha = result.get("sha") or readback.get("merge_commit_sha")
        evidence = {"merge_response": result, "pr_readback": readback, "candidate_receipt": candidate_receipt,
                    "review": review, "checks_before": checks, "checks_at_merge": latest_checks}
        return {"number": number, "expected_head": expected_head, "expected_base": expected_base,
                "merged": True, "sha": merge_sha, "message": result.get("message"),
                "merged_at": merged_at, "evidence_digest": digest(evidence)}
