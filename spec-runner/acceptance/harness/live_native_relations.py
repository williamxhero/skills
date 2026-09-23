"""Exercise native GitHub issue relations using only run-owned acceptance issues.

The script is opt-in. It writes two marked issues and closes only those exact
issue numbers after successful readback. Reports are stored under acceptance.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / "src"))

from spec_runner.github_tracker import GitHubTracker  # noqa: E402
from spec_runner.store import RunRecord, Store, now  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default="williamxhero/skills")
    parser.add_argument("--marker", default=f"SRAC-{datetime.now(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:12]}")
    args = parser.parse_args()
    if args.repository != "williamxhero/skills":
        parser.error("live acceptance is restricted to williamxhero/skills")
    if not re.fullmatch(r"SRAC-[A-Za-z0-9-]{8,64}", args.marker):
        parser.error("marker must be a unique SRAC- run marker")

    runtime = ROOT / ".runtime" / args.marker
    runtime.mkdir(parents=True, exist_ok=False)
    report_path = ROOT / "reports" / f"{args.marker}-native-relations.json"
    store = Store.open(runtime, create=True)
    timestamp = now()
    run = RunRecord(args.marker, "live_acceptance", "github-native-relations", "acceptance",
                    str(ROOT.parent.parent), "HEAD", "acceptance", "github", "starting",
                    "github_publication", f"logs/{args.marker}.jsonl", timestamp, timestamp)
    store.create_run(run, f"start:{args.marker}")
    issue_numbers: list[int] = []
    api_calls: list[list[str]] = []
    report: dict[str, object] = {"marker": args.marker, "repository": args.repository,
                                 "evidence_kind": "live_github", "outcome": "running"}

    def intent(**identity):
        return store.prepare_external_operation(run_id=args.marker, **identity)

    def completed(*, operation_id: str, receipt: dict[str, object]) -> None:
        store.complete_external_operation(operation_id=operation_id, receipt=receipt)
        if isinstance(receipt.get("number"), int) and receipt["number"] not in issue_numbers:
            issue_numbers.append(int(receipt["number"]))

    tracker = GitHubTracker()
    original_runner = tracker._runner

    def logged_runner(arguments: list[str]) -> str:
        api_calls.append([str(value).split("=", 1)[0] if str(value).startswith(("title=", "body=", "sub_issue_id=", "issue_id=")) else str(value) for value in arguments])
        return original_runner(arguments)

    tracker._runner = logged_runner

    try:
        draft = {
            "umbrella": {"key": f"{args.marker}-P", "title": f"[{args.marker}] native relation parent",
                         "body": f"Acceptance-only parent issue. Marker: {args.marker}."},
            "specs": [{"key": f"{args.marker}-C", "title": f"[{args.marker}] native relation child",
                       "body": f"Acceptance-only child issue. Marker: {args.marker}.",
                       "parent": f"{args.marker}-P", "blocked_by": [f"{args.marker}-P"]}],
        }
        result = tracker.publish_draft(repository=args.repository, draft=draft,
            operation_id=f"{args.marker}:publication", receipt_root=runtime / "projection",
            relation_mode="native", operation_intent=intent, operation_completed=completed)
        receipt = result["receipt"]
        issue_numbers = [int(item["number"]) for item in receipt["issues"]]
        report.update({"issue_numbers": issue_numbers, "receipt": receipt,
                       "native_relations_confirmed": receipt["relation_evidence"]["native"] is True,
                       "outcome": "passed"})
    except Exception as exc:
        report.update({"issue_numbers": issue_numbers, "outcome": "failed",
                       "error_type": type(exc).__name__, "error": str(exc),
                       "error_details": getattr(exc, "details", {}), "api_calls": api_calls})
        # Keep callback receipts and reconcile only this exact run marker.
        try:
            raw = GitHubTracker()._runner(["api", "--paginate", "--slurp",
                f"repos/{args.repository}/issues?state=all&per_page=100"])
            pages = json.loads(raw)
            reconciled = [int(item["number"]) for page in pages for item in page
                if isinstance(item, dict) and args.marker in str(item.get("body") or "")
                and str(item.get("title") or "").startswith(f"[{args.marker}]")]
            issue_numbers = sorted(set(issue_numbers + reconciled))
            report["issue_numbers"] = issue_numbers
        except Exception as reconcile_error:
            report["cleanup_reconcile_error"] = str(reconcile_error)
    finally:
        closed: list[int] = []
        close_errors: dict[str, str] = {}
        for number in issue_numbers:
            try:
                current = json.loads(GitHubTracker()._runner(["api", f"repos/{args.repository}/issues/{number}"]))
                if args.marker not in str(current.get("body") or ""):
                    raise RuntimeError("marker mismatch; refusing cleanup")
                GitHubTracker()._runner(["api", f"repos/{args.repository}/issues/{number}",
                                         "--method", "PATCH", "-f", "state=closed"])
                verify = json.loads(GitHubTracker()._runner(["api", f"repos/{args.repository}/issues/{number}"]))
                if verify.get("state") != "closed":
                    raise RuntimeError("close state was not confirmed")
                closed.append(number)
            except Exception as exc:
                close_errors[str(number)] = str(exc)
        report["closed_issue_numbers"] = closed
        report["cleanup_errors"] = close_errors
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8", newline="\n")
        store.close()
    print(json.dumps({"report": str(report_path), "outcome": report["outcome"],
                      "issue_numbers": report.get("issue_numbers"),
                      "closed_issue_numbers": report.get("closed_issue_numbers"),
                      "cleanup_errors": report.get("cleanup_errors")}, ensure_ascii=False))
    return 0 if report["outcome"] == "passed" and not report.get("cleanup_errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
