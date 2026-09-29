"""Read-only discovery of an externally managed GitHub delivery graph."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import DEFAULT_GIT_TIMEOUT_SECONDS, DEFAULT_GITHUB_TIMEOUT_SECONDS
from .errors import RunnerError
from .github_delivery import GitHubDelivery
from .github_tracker import GitHubTracker
from .plans import digest, validate_spec_plan, validate_ticket_plan
from .takeover import _git, _path_is_within, _working_tree_snapshot

SNAPSHOT_SCHEMA = "spec-runner-takeover-snapshot/v1"
_ISSUE_KEY = re.compile(r"(?m)^<!--\s*spec-runner-key:([A-Za-z0-9][A-Za-z0-9._-]{0,199})\s+operation:([^\s]+)\s*-->")
_PARENT = re.compile(r"(?im)^\s*parent(?:\s+spec)?\s*:?\s*#(\d+)\s*$")
_BLOCKED = re.compile(r"(?im)^\s*blocked[- ]by\s*:?\s*#(\d+)\s*$")


def _gh_json(runner: Callable[[list[str]], str], arguments: list[str], *, code: str) -> Any:
    try:
        result = json.loads(runner(arguments))
    except RunnerError:
        raise
    except (TypeError, json.JSONDecodeError) as exc:
        raise RunnerError(code, "GitHub readback was not valid JSON") from exc
    return result


def _paged_objects(value: Any, *, code: str, label: str) -> list[dict[str, Any]]:
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    if not isinstance(value, list) or any(not isinstance(page, list) for page in value):
        raise RunnerError(code, f"{label} pagination response was incomplete")
    if any(not isinstance(item, dict) for page in value for item in page):
        raise RunnerError(code, f"{label} pagination contained a non-object item")
    return [item for page in value for item in page]


def _issue_key(issue: dict[str, Any]) -> str:
    body = str(issue.get("body") or "")
    matches = list(_ISSUE_KEY.finditer(body))
    if len(matches) > 1:
        raise RunnerError("takeover_issue_marker_conflict", "issue contains more than one Spec Runner identity marker")
    number = issue.get("number")
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise RunnerError("takeover_issue_identity_invalid", "issue readback has no valid issue number")
    return matches[0].group(1) if matches else f"GH-{number}"


def _issue_identity(issue: dict[str, Any], key: str) -> dict[str, Any]:
    """Keep the exact external Issue identity needed for later adoption."""
    body = str(issue.get("body") or "")
    matches = list(_ISSUE_KEY.finditer(body))
    marker = matches[0].group(0) if len(matches) == 1 else None
    if marker:
        body = body[:matches[0].start()] + body[matches[0].end():]
        body = body.lstrip("\r\n")
    return {
        "key": key,
        "number": _issue_number(issue),
        "title": str(issue.get("title") or ""),
        "body": body,
        "marker": marker,
        "state": str(issue.get("state") or "unknown"),
        "revision": str(issue.get("updated_at") or ""),
    }


def _issue_number(value: dict[str, Any]) -> int:
    number = value.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise RunnerError("takeover_issue_identity_invalid", "issue relation has no valid issue number")
    return number


def _full_issue(tracker: GitHubTracker, repository: str, number: int) -> dict[str, Any]:
    issue = tracker._issue(repository, number)
    if issue.get("pull_request"):
        raise RunnerError("takeover_issue_identity_invalid", "an issue relation points to a pull request")
    return {"issue": issue, "comments": tracker._comments(repository, number)}


def _children(runner: Callable[[list[str]], str], repository: str, number: int) -> list[dict[str, Any]]:
    value = _gh_json(
        runner,
        ["api", "--paginate", "--slurp", f"repos/{repository}/issues/{number}/sub_issues?per_page=100"],
        code="takeover_relation_read_incomplete",
    )
    return _paged_objects(value, code="takeover_relation_read_incomplete", label="sub-issue")


def _dependencies(runner: Callable[[list[str]], str], repository: str, number: int) -> list[dict[str, Any]]:
    value = _gh_json(
        runner,
        ["api", "--paginate", "--slurp", f"repos/{repository}/issues/{number}/dependencies/blocked_by?per_page=100"],
        code="takeover_relation_read_incomplete",
    )
    return _paged_objects(value, code="takeover_relation_read_incomplete", label="blocked-by")


def _collect_issue_graph(
    *, runner: Callable[[list[str]], str], tracker: GitHubTracker,
    repository: str, umbrella_number: int,
) -> tuple[dict[int, dict[str, Any]], dict[int, set[int]], dict[int, list[int]]]:
    records: dict[int, dict[str, Any]] = {}
    parent_by_number: dict[int, int | None] = {}
    children: dict[int, list[int]] = {}
    listed = _read_issues(runner, repository)
    listed_by_number = {
        _issue_number(item): item for item in listed if not item.get("pull_request")
    }
    body_children: dict[int, list[int]] = {}
    for item in listed_by_number.values():
        match = _PARENT.search(str(item.get("body") or ""))
        if match:
            body_children.setdefault(int(match.group(1)), []).append(_issue_number(item))
    queue: list[tuple[int, int | None]] = [(umbrella_number, None)]
    while queue:
        number, expected_parent = queue.pop(0)
        if number in records:
            if parent_by_number[number] != expected_parent:
                raise RunnerError("takeover_relation_conflict", "issue is reachable through multiple parent paths")
            continue
        record = _full_issue(tracker, repository, number)
        issue = record["issue"]
        if _issue_number(issue) != number:
            raise RunnerError("takeover_issue_identity_invalid", "GitHub issue readback does not match requested number")
        body_parent_match = _PARENT.search(str(issue.get("body") or ""))
        body_parent = int(body_parent_match.group(1)) if body_parent_match else None
        if body_parent is not None and expected_parent is not None and body_parent != expected_parent:
            raise RunnerError("takeover_relation_conflict", "issue Parent marker conflicts with the native sub-issue relation")
        if expected_parent is None and number != umbrella_number and body_parent is None:
            raise RunnerError("takeover_relation_conflict", "discovered child issue has no verifiable Parent relation")
        parent_by_number[number] = expected_parent if expected_parent is not None else body_parent
        records[number] = record
        native_children = _children(runner, repository, number)
        native_numbers = [_issue_number(item) for item in native_children]
        body_numbers = sorted(body_children.get(number, []))
        if native_numbers and body_numbers and set(native_numbers) != set(body_numbers):
            raise RunnerError("takeover_relation_conflict", "native sub-issues and Parent markers disagree")
        child_numbers = native_numbers or body_numbers
        if len(set(child_numbers)) != len(child_numbers):
            raise RunnerError("takeover_relation_conflict", "GitHub returned a duplicate child issue")
        children[number] = child_numbers
        queue.extend((child_number, number) for child_number in child_numbers)

    dependencies: dict[int, set[int]] = {number: set() for number in records}
    for number, record in records.items():
        issue = record["issue"]
        body_numbers = {int(item) for item in _BLOCKED.findall(str(issue.get("body") or ""))}
        native_numbers = {_issue_number(item) for item in _dependencies(runner, repository, number)}
        if body_numbers and native_numbers and body_numbers != native_numbers:
            raise RunnerError("takeover_relation_conflict", "native and body blocked-by relations disagree")
        targets = native_numbers or body_numbers
        if targets - records.keys():
            raise RunnerError(
                "takeover_relation_incomplete",
                "a blocked-by issue is outside the discovered umbrella graph",
                details={"issue": number, "targets": sorted(targets - records.keys())},
            )
        dependencies[number] = targets
    return records, dependencies, children


def _read_branches(runner: Callable[[list[str]], str], repository: str) -> list[dict[str, Any]]:
    value = _gh_json(
        runner, ["api", "--paginate", "--slurp", f"repos/{repository}/branches?per_page=100"],
        code="takeover_branch_read_incomplete",
    )
    return _paged_objects(value, code="takeover_branch_read_incomplete", label="branch")


def _read_pull_requests(runner: Callable[[list[str]], str], repository: str) -> list[dict[str, Any]]:
    value = _gh_json(
        runner, ["api", "--paginate", "--slurp", f"repos/{repository}/pulls?state=all&per_page=100"],
        code="takeover_pr_read_incomplete",
    )
    return _paged_objects(value, code="takeover_pr_read_incomplete", label="pull request")


def _read_issues(runner: Callable[[list[str]], str], repository: str) -> list[dict[str, Any]]:
    value = _gh_json(
        runner, ["api", "--paginate", "--slurp", f"repos/{repository}/issues?state=all&per_page=100"],
        code="takeover_issue_list_incomplete",
    )
    return _paged_objects(value, code="takeover_issue_list_incomplete", label="issue")


def _pr_spec_key(pr: dict[str, Any]) -> str | None:
    body = str(pr.get("body") or "")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("spec_key"), str):
        return payload["spec_key"]
    operation = re.search(r"github:[^:\s]+:([A-Za-z0-9][A-Za-z0-9._-]{0,199}):[0-9a-f]{7,64}", body)
    return operation.group(1) if operation else None


def _local_git_snapshot(repository_path: Path, target_ref: str, git_timeout_seconds: float) -> dict[str, Any]:
    repository = repository_path.expanduser().resolve()
    head = _git(repository, "rev-parse", "--verify", target_ref, timeout_seconds=git_timeout_seconds)
    snapshot = _working_tree_snapshot(repository, git_timeout_seconds=git_timeout_seconds)
    branch = _git(repository, "branch", "--show-current", timeout_seconds=git_timeout_seconds)
    remotes = _git(repository, "remote", timeout_seconds=git_timeout_seconds).splitlines()
    remote_sha: str | None = None
    remote_relation = "not_configured"
    if "origin" in remotes:
        remote_ref = target_ref.removeprefix("refs/heads/")
        try:
            output = subprocess.run(
                ["git", "-C", os.fspath(repository), "ls-remote", "--heads", "origin", f"refs/heads/{remote_ref}"],
                check=True, capture_output=True, text=True, encoding="utf-8", timeout=git_timeout_seconds,
            ).stdout.strip()
        except subprocess.TimeoutExpired as exc:
            raise RunnerError("takeover_remote_read_timeout", "origin target ref read exceeded its bounded timeout") from exc
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RunnerError("takeover_remote_read_failed", "origin target ref could not be read") from exc
        if output:
            fields = output.split()
            if len(fields) != 2 or fields[1] != f"refs/heads/{remote_ref}":
                raise RunnerError("takeover_remote_read_incomplete", "origin target ref readback was ambiguous")
            remote_sha = fields[0]
            if remote_sha == head:
                remote_relation = "equal"
            else:
                try:
                    subprocess.run(
                        ["git", "-C", os.fspath(repository), "merge-base", "--is-ancestor", head, remote_sha],
                        check=True, capture_output=True, timeout=git_timeout_seconds,
                    )
                    remote_relation = "remote_ahead"
                except subprocess.CalledProcessError:
                    remote_relation = "diverged_or_unavailable"
                    try:
                        subprocess.run(
                            ["git", "-C", os.fspath(repository), "merge-base", "--is-ancestor", remote_sha, head],
                            check=True, capture_output=True, timeout=git_timeout_seconds,
                        )
                        remote_relation = "local_ahead"
                    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
                        pass
                except (OSError, subprocess.TimeoutExpired):
                    remote_relation = "diverged_or_unavailable"
        else:
            remote_relation = "missing"
    return {
        "repository_path": os.fspath(repository), "target_ref": target_ref,
        "target_sha": head, "checked_out_branch": branch or None,
        "dirty": bool(snapshot["status_entries"] or snapshot["redacted_path_count"]),
        "changed_paths": snapshot["changed"], "working_tree_digest": snapshot["snapshot_digest"],
        "origin_target_sha": remote_sha, "origin_relation": remote_relation,
    }


def _scan_receipts(roots: list[Path], repository: Path) -> dict[str, Any]:
    receipts: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for raw_root in roots:
        root = raw_root.expanduser().resolve()
        if not root.exists() or not root.is_dir() or root.is_symlink():
            raise RunnerError("takeover_artifact_root_invalid", "each authorized artifact root must be an existing directory")
        for path in sorted(root.rglob("*.json")):
            if path.is_symlink() or not _path_is_within(path, root):
                raise RunnerError("takeover_artifact_path_escape", "artifact root contains a symlink or escaped path")
            canonical = os.path.normcase(os.fspath(path.resolve()))
            if canonical in seen_paths:
                continue
            seen_paths.add(canonical)
            if len(seen_paths) > 10000:
                raise RunnerError("takeover_artifact_scan_limit", "authorized artifact scan exceeded 10000 files")
            if path.stat().st_size > 5 * 1024 * 1024:
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and value.get("schema_version") in {
                "spec-runner-production-delivery/v1", "spec-runner-candidate-receipt/v1",
                "spec-runner-review-result/v1", "spec-runner-workspace/v1",
                "spec-runner-takeover-snapshot/v1", "spec-runner-ticket-plan/v1",
            }:
                spec_key = value.get("spec_key")
                receipts.append({
                    "path": os.fspath(path.resolve()),
                    "digest": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "schema_version": value.get("schema_version"),
                    "spec_key": spec_key, "run_id": value.get("run_id"),
                    "value": value,
                })
    return {
        "roots": [os.fspath(root.expanduser().resolve()) for root in roots],
        "receipts": receipts, "repository_path": os.fspath(repository.resolve()),
    }


def _classify_spec(
    spec: dict[str, Any], tickets: list[dict[str, Any]], delivery: dict[str, Any] | None,
    pr: dict[str, Any] | None, checks: dict[str, Any] | None,
    candidate_receipt: dict[str, Any] | None = None,
    review_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    complete_evidence = _complete_delivery_evidence(
        spec, tickets, delivery, pr, checks,
        candidate_receipt=candidate_receipt, review_receipt=review_receipt,
    )
    if complete_evidence["complete"]:
        state = "completed"
    elif not tickets:
        state = "spec_ready"
    elif delivery and isinstance(delivery.get("merge"), dict) and delivery["merge"].get("merged") is True or pr and pr.get("merged") is True:
        state = "closure_pending"
    elif pr and checks and checks.get("ready") is not True:
        state = "checks_pending"
    elif pr and delivery and isinstance(delivery.get("review"), dict) and delivery["review"].get("approved") is True:
        state = "merge_pending"
    elif pr and delivery and isinstance(delivery.get("candidate"), dict):
        state = "review_pending"
    elif spec.get("candidate_sha"):
        state = "candidate_ready"
    else:
        state = "tickets_adopted"
    return {
        "key": spec["key"], "issue_number": spec["number"], "revision": spec["revision"],
        "state": state, "blocked_by": spec["blocked_by"],
        "ticket_numbers": [item["number"] for item in tickets],
        "branch": spec.get("branch"), "candidate_sha": spec.get("candidate_sha"),
        "pull_request": spec.get("pull_request"), "checks": checks,
        "complete_evidence": complete_evidence,
    }


def _receipt_for_spec(receipts: list[dict[str, Any]], spec_key: str,
                      schema_version: str, prefix: str) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    for receipt in receipts:
        if receipt.get("schema_version") != schema_version:
            continue
        path = Path(str(receipt.get("path") or "")).name
        if receipt.get("spec_key") == spec_key or path.startswith(f"{prefix}{spec_key}"):
            value = receipt.get("value")
            if isinstance(value, dict):
                matches.append(value)
    if len(matches) > 1:
        unique = {digest(item) for item in matches}
        if len(unique) > 1:
            raise RunnerError("takeover_receipt_ambiguous", "multiple different receipts identify one SPEC")
    return matches[0] if matches else None


def _complete_delivery_evidence(
    spec: dict[str, Any], tickets: list[dict[str, Any]], delivery: dict[str, Any] | None,
    pr: dict[str, Any] | None, checks: dict[str, Any] | None,
    *, candidate_receipt: dict[str, Any] | None = None,
    review_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check every independent receipt needed before adopting a SPEC as done."""
    reasons: list[str] = []
    candidate = candidate_receipt
    review = review_receipt
    delivery_candidate = delivery.get("candidate") if isinstance(delivery, dict) else None
    delivery_review = delivery.get("review") if isinstance(delivery, dict) else None
    merge = delivery.get("merge") if isinstance(delivery, dict) else None
    recorded_checks = delivery.get("checks") if isinstance(delivery, dict) else None
    candidate_sha = spec.get("candidate_sha")
    merge_sha = merge.get("sha") if isinstance(merge, dict) else None
    if not isinstance(candidate_sha, str) or len(candidate_sha) != 40:
        reasons.append("candidate_sha_missing")
    if (
        not isinstance(candidate, dict)
        or candidate.get("outcome") != "verified"
        or candidate.get("candidate_sha") != candidate_sha
        or not isinstance(delivery_candidate, dict)
        or delivery_candidate != candidate
    ):
        reasons.append("candidate_receipt_missing")
    if (
        not isinstance(review, dict)
        or review.get("approved") is not True
        or review.get("candidate_sha") != candidate_sha
        or not str(review.get("review_digest") or "")
        or not isinstance(delivery_review, dict)
        or any(delivery_review.get(key) != review.get(key) for key in ("approved", "candidate_sha", "review_digest"))
    ):
        reasons.append("review_receipt_missing")
    effective_checks = recorded_checks if isinstance(recorded_checks, dict) else checks
    if not isinstance(effective_checks, dict) or effective_checks.get("candidate_sha") != candidate_sha or effective_checks.get("ready") is not True:
        reasons.append("checks_readback_missing")
    if not isinstance(merge, dict) or merge.get("merged") is not True or not isinstance(merge_sha, str) or len(merge_sha) != 40:
        reasons.append("merge_readback_missing")
    closure = delivery.get("issue_closure") if isinstance(delivery, dict) else None
    expected_issues = {int(spec["number"]), *(int(ticket["number"]) for ticket in tickets)}
    closed_issues = {
        int(item["number"])
        for item in (closure.get("issues", []) if isinstance(closure, dict) else [])
        if isinstance(item, dict) and isinstance(item.get("number"), int) and item.get("state") == "closed"
    }
    if not isinstance(closure, dict) or closure.get("complete") is not True or not expected_issues <= closed_issues:
        reasons.append("issue_closure_missing")
    cleanup = delivery.get("cleanup") if isinstance(delivery, dict) else None
    if not isinstance(cleanup, dict) or cleanup.get("outcome") != "cleaned":
        reasons.append("cleanup_receipt_missing")
    target = spec.get("target") if isinstance(spec.get("target"), dict) else None
    if not isinstance(target, dict) or target.get("target_sha") != target.get("origin_target_sha") or target.get("origin_relation") != "equal":
        reasons.append("target_remote_readback_missing")
    return {"complete": not reasons, "reasons": reasons, "candidate_sha": candidate_sha, "merge_sha": merge_sha}


def _topological_specs(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {str(item["key"]): item for item in specs}
    remaining = set(by_key)
    ordered: list[dict[str, Any]] = []
    while remaining:
        ready = sorted(
            key for key in remaining
            if set(by_key[key].get("blocked_by", [])) <= {str(item["key"]) for item in ordered}
        )
        if not ready:
            raise RunnerError("takeover_spec_dependency_cycle", "discovered SPEC dependency graph contains a cycle")
        ordered.extend(by_key[key] for key in ready)
        remaining.difference_update(ready)
    return ordered


def build_adopted_plans(
    snapshot: dict[str, Any], *, model: str, effort: str, base_sha: str,
    existing_ticket_plans: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Materialize a discovered Issue graph into the Runner's durable plans."""
    validate_snapshot(snapshot)
    graph = snapshot.get("graph")
    if not isinstance(graph, dict) or not isinstance(graph.get("umbrella"), dict) or not isinstance(graph.get("specs"), list):
        raise RunnerError("invalid_takeover_snapshot", "discovery snapshot has no complete graph")
    raw_specs = [item for item in graph["specs"] if isinstance(item, dict) and isinstance(item.get("key"), str)]
    if not raw_specs:
        raise RunnerError("takeover_spec_graph_empty", "discovery snapshot contains no SPECs")
    ordered = _topological_specs(raw_specs)
    umbrella = graph["umbrella"]
    global_requirement = f"{umbrella.get('title') or 'Umbrella SPEC'}\n{umbrella.get('body') or ''}".strip()
    requirements = [global_requirement]
    plan_specs: list[dict[str, Any]] = []
    ticket_plans: dict[str, dict[str, Any]] = {}
    source_plans = existing_ticket_plans or {}
    for index, item in enumerate(ordered):
        key = str(item["key"])
        requirement = f"{key}: {item.get('title') or key}\n{item.get('body') or ''}".strip()
        requirements.append(requirement)
        plan_specs.append({
            "key": key, "title": str(item.get("title") or key), "body": str(item.get("body") or key),
            "blocked_by": list(item.get("blocked_by", [])),
            "covers": [global_requirement, requirement] if index == 0 else [requirement],
            "route": {"model": model, "effort": effort, "reason": "adopted from an immutable GitHub takeover snapshot"},
        })
        source = source_plans.get(key)
        if isinstance(source, dict):
            ticket_plan = dict(source)
        else:
            tickets = item.get("tickets")
            if not isinstance(tickets, list):
                raise RunnerError("takeover_ticket_graph_invalid", f"SPEC {key} has no complete ticket graph")
            if not tickets:
                continue
            ticket_plan = {
                "schema_version": "spec-runner-ticket-plan/v1", "spec_key": key,
                "spec_title": str(item.get("title") or key), "spec_body": str(item.get("body") or ""),
                "base_sha": base_sha,
                "github_operation_id": next((
                    re.search(r"operation:([^\s]+)", str(issue.get("marker"))).group(1)
                    for issue in [item.get("github_issue")]
                    if isinstance(issue, dict) and isinstance(issue.get("marker"), str)
                    and re.search(r"operation:([^\s]+)", str(issue.get("marker")))
                ), None),
                "github_issues": [item.get("github_issue"), *[
                    ticket.get("github_issue") for ticket in tickets
                    if isinstance(ticket, dict) and isinstance(ticket.get("github_issue"), dict)
                ]],
                "tickets": [{
                    "key": str(ticket["key"]), "title": str(ticket.get("title") or ticket["key"]),
                    "body": str(ticket.get("body") or ticket["key"]), "blocked_by": list(ticket.get("blocked_by", [])),
                    "acceptance": [f"ticket:{ticket['key']}"],
                } for ticket in tickets if isinstance(ticket, dict) and isinstance(ticket.get("key"), str)],
            }
        if isinstance(ticket_plan, dict) and ticket_plan.get("tickets"):
            ticket_plans[key] = validate_ticket_plan(ticket_plan, expected_spec_key=key)
    spec_plan = validate_spec_plan({
        "schema_version": "spec-runner-spec-plan/v1", "requirements": requirements, "specs": plan_specs,
    })
    return {"spec_plan": spec_plan, "ticket_plans": ticket_plans}


def discover_takeover(
    *, repository: str, umbrella_issue: int, workspace: Path, target_ref: str,
    control_root: Path, takeover_key: str, artifact_roots: list[Path],
    required_checks: list[str] | None = None, source_thread_id: str | None = None,
    github_timeout_seconds: float = DEFAULT_GITHUB_TIMEOUT_SECONDS,
    git_timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
    runner: Callable[[list[str]], str] | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
        raise RunnerError("invalid_github_repository", "repository must be owner/name")
    if isinstance(umbrella_issue, bool) or not isinstance(umbrella_issue, int) or umbrella_issue < 1:
        raise RunnerError("takeover_issue_invalid", "umbrella issue number must be positive")
    if not isinstance(takeover_key, str) or not takeover_key.strip() or any(character.isspace() for character in takeover_key):
        raise RunnerError("invalid_takeover_key", "takeover key must be non-empty and contain no whitespace")
    if not target_ref.strip():
        raise RunnerError("takeover_target_ref_invalid", "target ref must be non-empty")
    gh_runner = runner or (lambda arguments: GitHubTracker._run_gh(arguments, timeout_seconds=github_timeout_seconds))
    tracker = GitHubTracker(runner=gh_runner, timeout_seconds=github_timeout_seconds)
    issue_graph, dependencies, children = _collect_issue_graph(
        runner=gh_runner, tracker=tracker, repository=repository, umbrella_number=umbrella_issue,
    )
    umbrella = issue_graph[umbrella_issue]["issue"]
    branches = _read_branches(gh_runner, repository)
    pull_requests = _read_pull_requests(gh_runner, repository)
    branch_by_name = {str(item.get("name")): item for item in branches if isinstance(item.get("name"), str)}
    local = _local_git_snapshot(workspace, target_ref, git_timeout_seconds)
    receipt_scan = _scan_receipts([*artifact_roots, control_root], workspace)
    required = list(required_checks or [])
    by_spec: dict[str, dict[str, Any]] = {}
    for number in children.get(umbrella_issue, []):
        issue_record = issue_graph[number]
        issue = issue_record["issue"]
        key = _issue_key(issue)
        ticket_records: list[dict[str, Any]] = []
        for ticket_number in children.get(number, []):
            ticket_issue = issue_graph[ticket_number]["issue"]
            ticket_records.append({
                "key": _issue_key(ticket_issue), "number": ticket_number,
                "title": str(ticket_issue.get("title") or ""), "body": str(ticket_issue.get("body") or ""),
                "state": str(ticket_issue.get("state") or "unknown"),
                "revision": str(ticket_issue.get("updated_at") or ""),
                "labels": sorted(str(label.get("name")) for label in ticket_issue.get("labels", []) if isinstance(label, dict) and label.get("name")),
                "blocked_by": sorted(_issue_key(issue_graph[target]["issue"]) for target in dependencies[ticket_number]),
                "github_issue": _issue_identity(ticket_issue, _issue_key(ticket_issue)),
            })
        matching_prs = [pr for pr in pull_requests if _pr_spec_key(pr) == key]
        if len(matching_prs) > 1:
            raise RunnerError("takeover_pr_ambiguous", "more than one PR identifies the same SPEC", details={"spec_key": key})
        pr = matching_prs[0] if matching_prs else None
        branch_name = None
        candidate_sha = None
        candidate_receipt = _receipt_for_spec(
            receipt_scan["receipts"], key, "spec-runner-candidate-receipt/v1", "candidate-",
        )
        review_receipt = _receipt_for_spec(
            receipt_scan["receipts"], key, "spec-runner-review-result/v1", "review-",
        )
        if pr:
            head = pr.get("head")
            base = pr.get("base")
            if not isinstance(head, dict) or not isinstance(base, dict) or not isinstance(base.get("repo"), dict) or base["repo"].get("full_name") != repository:
                raise RunnerError("takeover_pr_identity_mismatch", "SPEC PR has incomplete or foreign repository identity")
            branch_name = str(head.get("ref") or "")
            candidate_sha = str(head.get("sha") or "")
            if not candidate_sha:
                raise RunnerError("takeover_candidate_identity_missing", "SPEC PR has no candidate SHA")
            branch_record = branch_by_name.get(branch_name)
            if branch_record and str((branch_record.get("commit") or {}).get("sha") or "") != candidate_sha:
                raise RunnerError("takeover_branch_drift", "candidate branch tip differs from the PR head SHA", details={"spec_key": key, "branch": branch_name})
        else:
            matching_branches = [name for name in branch_by_name if name.startswith(f"spec-runner/{key}-")]
            if len(matching_branches) > 1:
                raise RunnerError("takeover_branch_ambiguous", "more than one candidate branch matches the SPEC", details={"spec_key": key})
            if matching_branches:
                branch_name = matching_branches[0]
                candidate_sha = str((branch_by_name[branch_name].get("commit") or {}).get("sha") or "")
        receipt_candidate_sha = candidate_receipt.get("candidate_sha") if isinstance(candidate_receipt, dict) else None
        if receipt_candidate_sha is not None:
            if not isinstance(receipt_candidate_sha, str) or len(receipt_candidate_sha) != 40:
                raise RunnerError("takeover_candidate_identity_invalid", "candidate receipt has no valid candidate SHA", details={"spec_key": key})
            if candidate_sha is not None and candidate_sha != receipt_candidate_sha:
                raise RunnerError("takeover_candidate_identity_mismatch", "candidate receipt does not match the branch or PR tip", details={"spec_key": key})
            candidate_sha = receipt_candidate_sha
        delivery = None
        for receipt in receipt_scan["receipts"]:
            if receipt.get("schema_version") != "spec-runner-production-delivery/v1" or receipt.get("spec_key") != key:
                continue
            receipt_value = json.loads(Path(str(receipt["path"])).read_text(encoding="utf-8"))
            if delivery is not None and digest(delivery) != digest(receipt_value):
                raise RunnerError("takeover_receipt_ambiguous", "multiple different delivery receipts identify one SPEC")
            delivery = receipt_value
        checks = None
        if pr and candidate_sha and required:
            checks = GitHubDelivery(
                runner=gh_runner, timeout_seconds=github_timeout_seconds,
            ).checks(repository=repository, candidate_sha=candidate_sha, required=required)
        merged_sha = pr.get("merge_commit_sha") if pr and pr.get("merged") else None
        if pr and pr.get("merged") and not merged_sha:
            raise RunnerError("takeover_merge_readback_incomplete", "merged PR has no merge commit SHA")
        spec_data = {
            "key": key, "number": number, "title": str(issue.get("title") or ""),
            "body": str(issue.get("body") or ""), "revision": str(issue.get("updated_at") or ""),
            "state": str(issue.get("state") or "unknown"),
            "labels": sorted(str(label.get("name")) for label in issue.get("labels", []) if isinstance(label, dict) and label.get("name")),
            "comments": issue_record["comments"], "blocked_by": sorted(_issue_key(issue_graph[target]["issue"]) for target in dependencies[number]),
            "tickets": ticket_records, "branch": branch_name, "candidate_sha": candidate_sha,
            "github_issue": _issue_identity(issue, key),
            "pull_request": ({
                "number": pr.get("number"), "url": pr.get("html_url"), "state": pr.get("state"),
                "merged": bool(pr.get("merged")), "merge_sha": merged_sha,
                "head_sha": candidate_sha, "head_ref": branch_name,
                "base_ref": (pr.get("base") or {}).get("ref"),
            } if pr else None),
            "target": local,
        }
        spec_data["delivery"] = delivery
        spec_data["candidate_receipt"] = candidate_receipt
        spec_data["review_receipt"] = review_receipt
        spec_data["checks"] = checks
        spec_data["frontier"] = _classify_spec(
            spec_data, ticket_records, delivery, pr, checks,
            candidate_receipt=candidate_receipt, review_receipt=review_receipt,
        )
        if key in by_spec:
            raise RunnerError("takeover_spec_identity_ambiguous", "more than one Issue identifies the same SPEC", details={"spec_key": key})
        ticket_keys = [str(item["key"]) for item in ticket_records]
        if len(ticket_keys) != len(set(ticket_keys)):
            raise RunnerError("takeover_ticket_identity_ambiguous", "more than one Issue identifies the same ticket", details={"spec_key": key})
        by_spec[key] = spec_data
    if not by_spec:
        raise RunnerError("takeover_spec_graph_empty", "umbrella issue has no discoverable child SPEC issues")
    blockers: list[dict[str, Any]] = []
    if local["dirty"]:
        blockers.append({"code": "working_tree_dirty", "changed_paths": local["changed_paths"]})
    if local["origin_relation"] in {"diverged_or_unavailable", "missing"}:
        blockers.append({"code": "target_ref_not_synchronized", "origin_relation": local["origin_relation"]})
    graph = {
        "repository": repository,
        "umbrella": {
            "number": umbrella_issue, "key": _issue_key(umbrella),
            "title": str(umbrella.get("title") or ""), "body": str(umbrella.get("body") or ""),
            "state": str(umbrella.get("state") or "unknown"),
            "revision": str(umbrella.get("updated_at") or ""),
            "labels": sorted(str(label.get("name")) for label in umbrella.get("labels", []) if isinstance(label, dict) and label.get("name")),
            "comments": issue_graph[umbrella_issue]["comments"],
        },
        "specs": list(by_spec.values()), "local": local,
        "artifacts": receipt_scan, "required_checks": required,
        "source_thread_id": source_thread_id,
    }
    base = {
        "schema_version": SNAPSHOT_SCHEMA, "takeover_key": takeover_key,
        "input": {
            "repository": repository, "umbrella_issue": umbrella_issue,
            "workspace": os.fspath(workspace.expanduser().resolve()), "target_ref": target_ref,
            "control_root": os.fspath(control_root.expanduser().resolve()),
            "artifact_roots": [os.fspath(path.expanduser().resolve()) for path in artifact_roots],
            "required_checks": required, "source_thread_id": source_thread_id,
        },
        "graph": graph, "blockers": blockers,
    }
    snapshot = {**base, "digest": digest(base)}
    facts = {
        "requirements": [str(umbrella.get("title") or "Umbrella SPEC"), str(umbrella.get("body") or "")],
        "requirements_material": [str(umbrella.get("body") or "")], "tracker": True,
        "takeover_snapshot": snapshot,
        "specs": [
            {"key": spec_key, "state": value["frontier"]["state"],
             "blocked_by": value["blocked_by"], "frontier": value["frontier"],
             "issue_number": value["number"]}
            for spec_key, value in by_spec.items()
        ],
        "target": local, "source_thread_id": source_thread_id, "blockers": blockers,
    }
    inventory = {
        "schema_version": "spec-runner-takeover-input/v1", "repository_path": local["repository_path"],
        "source_threads": [], "artifacts": [], "facts": facts,
        "discovery_snapshot_digest": snapshot["digest"],
    }
    return {
        "snapshot": snapshot, "inventory": inventory,
        "spec_frontiers": [value["frontier"] for value in by_spec.values()],
        "blockers": blockers, "digest": snapshot["digest"],
    }


def validate_snapshot(snapshot: dict[str, Any]) -> None:
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA:
        raise RunnerError("invalid_takeover_snapshot", "unexpected takeover snapshot schema")
    claimed = snapshot.get("digest")
    body = dict(snapshot)
    body.pop("digest", None)
    if not isinstance(claimed, str) or digest(body) != claimed:
        raise RunnerError("takeover_snapshot_digest_mismatch", "takeover snapshot digest does not match its contents")
