"""Explicit GitHub PR/check/merge adapter used after local candidate gates."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from .errors import RunnerError
from .plans import digest


class GitHubDelivery:
    def __init__(self, *, runner: Callable[[list[str]], str] | None = None):
        self.runner = runner or self._gh

    @staticmethod
    def _gh(args: list[str]) -> str:
        import subprocess
        try:
            return subprocess.run(["gh", *args], check=True, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RunnerError("github_delivery_failed", "GitHub delivery command failed") from exc

    @staticmethod
    def _repo(repository: str) -> None:
        if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
            raise RunnerError("invalid_github_repository", "repository must be owner/name")

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
            return {"created": False, "receipt": old}
        marker = f"<!-- spec-runner-pr:{operation_id} candidate:{candidate_sha} -->"
        # The query is scoped to the configured repository, head and base. A
        # title similarity is never used as an adoption key.
        raw = self.runner(["api", f"repos/{repository}/pulls", "--method", "GET", "-f", f"head={head}", "-f", f"base={base}"])
        existing = json.loads(raw)
        if not isinstance(existing, list):
            raise RunnerError("github_pr_readback_incomplete", "pull request listing was not a list")
        matches = [item for item in existing if isinstance(item, dict) and marker in str(item.get("body") or "") and str(item.get("head", {}).get("sha", "")) == candidate_sha]
        if len(matches) > 1:
            raise RunnerError("github_pr_ambiguous", "multiple run-owned PRs match the candidate")
        if matches:
            item = matches[0]
            receipt = {"number": item.get("number"), "url": item.get("html_url"), "candidate_sha": candidate_sha, "head": head, "base": base, "adopted": True, "marker": marker}
        else:
            adopted_after_reconcile = False
            try:
                response = json.loads(self.runner(["api", f"repos/{repository}/pulls", "--method", "POST", "-f", f"title=Spec Runner {candidate_sha[:12]}", "-f", f"head={head}", "-f", f"base={base}", "-f", f"body={marker}\n{body}"]))
            except Exception as exc:
                # A lost POST response has an unknown outcome. Re-read the
                # complete scoped listing and adopt exactly one matching PR;
                # never issue a second create based on a timeout alone.
                try:
                    retry_listing = json.loads(self.runner(["api", f"repos/{repository}/pulls", "--method", "GET", "-f", f"head={head}", "-f", f"base={base}"]))
                except Exception as readback_exc:
                    raise RunnerError("github_pr_unknown", "PR creation outcome and readback are both unknown") from readback_exc
                if not isinstance(retry_listing, list):
                    raise RunnerError("github_pr_readback_incomplete", "pull request readback was not a list") from exc
                recovered = [item for item in retry_listing if isinstance(item, dict) and marker in str(item.get("body") or "") and str(item.get("head", {}).get("sha", "")) == candidate_sha]
                if len(recovered) != 1:
                    raise RunnerError("github_pr_unknown", "PR creation outcome is not uniquely reconciled") from exc
                response = recovered[0]
                adopted_after_reconcile = True
            if not isinstance(response, dict) or not response.get("number"):
                raise RunnerError("github_pr_unconfirmed", "GitHub PR create response was not a PR")
            receipt = {"number": response["number"], "url": response.get("html_url"), "candidate_sha": candidate_sha, "head": head, "base": base, "adopted": adopted_after_reconcile, "marker": marker}
        receipts[operation_id] = receipt
        path.write_text(json.dumps(receipts, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        return {"created": not receipt.get("adopted", False), "receipt": receipt}

    def checks(self, *, repository: str, candidate_sha: str, required: list[str]) -> dict[str, object]:
        self._repo(repository)
        data = json.loads(self.runner(["api", f"repos/{repository}/commits/{candidate_sha}/check-runs"]))
        runs = data.get("check_runs") if isinstance(data, dict) else None
        if not isinstance(runs, list):
            raise RunnerError("github_checks_incomplete", "check-runs response is incomplete")
        by_name = {str(item.get("name")): item for item in runs if isinstance(item, dict)}
        missing = [name for name in required if name not in by_name]
        states = {name: {"status": by_name[name].get("status"), "conclusion": by_name[name].get("conclusion"), "sha": by_name[name].get("head_sha")} for name in required if name in by_name}
        wrong_sha = [name for name, item in states.items() if item["sha"] != candidate_sha]
        failed = [name for name, item in states.items() if item["conclusion"] not in {"success"}]
        return {"candidate_sha": candidate_sha, "required": required, "states": states, "missing": missing, "wrong_sha": wrong_sha, "failed": failed, "ready": not missing and not wrong_sha and not failed}

    def merge(self, *, repository: str, number: int, expected_head: str, allow: bool = False) -> dict[str, object]:
        self._repo(repository)
        if not allow:
            raise RunnerError("merge_not_authorized", "merge requires an explicit Runner authorization")
        pr = json.loads(self.runner(["api", f"repos/{repository}/pulls/{number}"]))
        if not isinstance(pr, dict) or pr.get("head", {}).get("sha") != expected_head:
            raise RunnerError("stale_pull_request", "PR head changed before merge")
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
        evidence = {"merge_response": result, "pr_readback": readback}
        return {"number": number, "expected_head": expected_head, "merged": True, "sha": merge_sha, "message": result.get("message"), "merged_at": merged_at, "evidence_digest": digest(evidence)}
