"""Build a fail-closed single-ticket ledger from GitHub readback JSON."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

QUEUE_MARKER = "唯一单线队列"
DEFAULT_COUNT = 33
_QUEUE_RE = re.compile(r"`(?P<queue>#[0-9]+(?:\s*→\s*#[0-9]+)+)`")
_TICKET_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s+(?P<id>#[0-9]+)\s+—\s+(?P<title>.+?)\s*$")


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"GitHub readback field {name} is missing")
    return value.strip()


def _queue_from_body(body: str) -> list[str]:
    section = body[body.find(QUEUE_MARKER):] if QUEUE_MARKER in body else body
    matches = _QUEUE_RE.findall(section)
    if not matches:
        raise ValueError("GitHub root issue has no single-ticket queue readback")
    queue = [item.strip() for item in matches[-1].split("→")]
    if len(queue) != DEFAULT_COUNT or len(set(queue)) != DEFAULT_COUNT:
        raise ValueError("GitHub root issue queue must contain exactly 33 unique tickets")
    return queue


def _section_specs(body: str) -> dict[str, str]:
    current = None
    result: dict[str, str] = {}
    for line in body.splitlines():
        if line.startswith("### "):
            title = line[4:].strip()
            current = {"A0": "#431", "Strategy Genome": "#402", "Research Memory": "#381"}.get(title)
        match = _TICKET_RE.match(line)
        if match and current:
            result[match.group("id")] = current
    return result


def _parent_issue(body: str) -> str | None:
    match = re.search(r"^Parent(?: issue)?\s*:?\s*(#[0-9]+)", body, re.MULTILINE | re.IGNORECASE)
    return match.group(1) if match else None


def _blocked_by(body: str, queue: set[str]) -> list[str]:
    match = re.search(r"## Blocked by\s*(.*?)(?=\n## |\Z)", body, re.DOTALL | re.IGNORECASE)
    if not match:
        return []
    blockers = []
    for line in match.group(1).splitlines():
        if not line.strip():
            if blockers:
                break
            continue
        declared = re.match(
            r"^\s*(?:[-*]\s*)?(?P<ids>#[0-9]+(?:\s*(?:,|，|、|and|&)?\s*#[0-9]+)*)",
            line,
            re.IGNORECASE,
        )
        if not declared:
            if blockers:
                break
            continue
        blockers.extend(ticket_id for ticket_id in re.findall(r"#[0-9]+", declared.group("ids")) if ticket_id in queue)
    return blockers


_EVIDENCE_SCHEMES = {
    "commit": {"commit", "git", "https", "pr", "sha"},
    "test": {"check", "ci", "https", "pytest", "test", "file"},
    "acceptance": {"acceptance", "file", "github", "https"},
}


def _evidence_list(value: Any, kind: str, ticket_id: str) -> list[str]:
    """Normalize one historical evidence field without inventing a pass."""
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    result = []
    for item in values:
        if isinstance(item, str):
            candidate = item.strip()
        elif isinstance(item, Mapping):
            candidate = item.get("evidence") or item.get("uri") or item.get("url")
            if not isinstance(candidate, str) and kind == "commit":
                sha = item.get("sha") or item.get("commit")
                candidate = f"git:{sha}" if isinstance(sha, str) and sha.strip() else None
            if not isinstance(candidate, str) and kind == "test":
                report = item.get("report") or item.get("artifact")
                candidate = f"file:///{quote(str(report).replace(chr(92), '/'))}" if report else None
        else:
            candidate = None
        if not isinstance(candidate, str) or not candidate.strip() or ":" not in candidate:
            raise ValueError(f"{ticket_id} has unmappable {kind} evidence")
        scheme = candidate.split(":", 1)[0].lower()
        if scheme not in _EVIDENCE_SCHEMES[kind]:
            raise ValueError(f"{ticket_id} has invalid {kind} evidence scheme")
        result.append(candidate.strip())
    return result


def build_ticket_ledger(readback: Mapping[str, Any], *, expected_count: int = DEFAULT_COUNT) -> dict[str, Any]:
    """Convert a root issue plus issue readbacks into importer input.

    GitHub ``state=CLOSED`` is retained only as source metadata. It becomes a
    closed controller ticket only when the readback explicitly supplies a
    controller status and structured commit/test evidence.
    """
    root = readback.get("root_issue")
    issues = readback.get("issues")
    if not isinstance(root, Mapping) or not isinstance(issues, list):
        raise TypeError("GitHub readback requires root_issue and issues")
    body = _text(root.get("body"), "root_issue.body")
    queue = _queue_from_body(body)
    if len(queue) != expected_count:
        raise ValueError(f"GitHub queue contains {len(queue)} tickets, expected {expected_count}")
    by_id: dict[str, Mapping[str, Any]] = {}
    for issue in issues:
        if not isinstance(issue, Mapping):
            raise TypeError("GitHub issue readbacks must be objects")
        number = issue.get("number")
        ticket_id = f"#{number}" if isinstance(number, int) else _text(issue.get("ticket_id"), "ticket_id")
        if ticket_id in by_id:
            raise ValueError(f"duplicate GitHub issue readback: {ticket_id}")
        by_id[ticket_id] = issue
    delivery_records = readback.get("delivery_records", readback.get("historical_delivery", []))
    if delivery_records is None:
        delivery_records = []
    if not isinstance(delivery_records, list):
        raise TypeError("delivery_records must be an array")
    deliveries: dict[str, Mapping[str, Any]] = {}
    for record in delivery_records:
        if not isinstance(record, Mapping):
            raise TypeError("delivery records must be objects")
        ticket_id = record.get("ticket_id") or record.get("id")
        if not isinstance(ticket_id, str) or ticket_id in deliveries:
            raise ValueError("delivery records contain invalid or duplicate ticket_id")
        deliveries[ticket_id] = record
    missing = [ticket_id for ticket_id in queue if ticket_id not in by_id]
    if missing:
        raise ValueError("GitHub issue readback is missing: " + ", ".join(missing))
    orphan_deliveries = sorted(set(deliveries) - set(queue))
    if orphan_deliveries:
        raise ValueError("historical delivery records reference tickets outside the queue: " + ", ".join(orphan_deliveries))
    specs = _section_specs(body)
    entries = []
    for position, ticket_id in enumerate(queue, 1):
        issue = by_id[ticket_id]
        delivery = deliveries.get(ticket_id, {})
        title = _text(issue.get("title"), f"{ticket_id}.title")
        issue_url = _text(issue.get("url", issue.get("html_url")), f"{ticket_id}.url")
        spec_id = issue.get("spec_id") or issue.get("parent_spec_id") or _parent_issue(str(issue.get("body", ""))) or specs.get(ticket_id)
        spec_id = _text(spec_id, f"{ticket_id}.spec_id")
        # Controller status is an independent readback; GitHub state alone is
        # deliberately insufficient to claim a ticket was delivered.
        status = delivery.get("controller_status", delivery.get("status", issue.get("controller_status", issue.get("status", "planned"))))
        if status == "closed" and str(issue.get("state", "")).upper() != "CLOSED":
            raise ValueError(f"{ticket_id} controller status is closed but GitHub is not closed")
        commits = _evidence_list(delivery.get("commits", issue.get("commits", [])), "commit", ticket_id)
        tests = _evidence_list(delivery.get("tests", delivery.get("test_evidence", issue.get("tests", issue.get("test_evidence", [])))), "test", ticket_id)
        acceptance = _evidence_list(delivery.get("acceptance", delivery.get("acceptance_evidence", issue.get("acceptance", issue.get("acceptance_evidence", [])))), "acceptance", ticket_id)
        delivery_evidence = delivery.get("evidence", delivery.get("readback_evidence", []))
        if not isinstance(delivery_evidence, list) or any(not isinstance(item, str) for item in delivery_evidence):
            raise ValueError(f"{ticket_id} delivery evidence must be a string array")
        if status == "closed" and not delivery:
            raise ValueError(f"{ticket_id} closed without a historical delivery record")
        entries.append({
            "ticket_id": ticket_id,
            "spec_id": spec_id,
            "title": title,
            "status": status,
            "blocked_by": issue.get("blocked_by", _blocked_by(str(issue.get("body", "")), set(queue))),
            "commits": commits,
            "tests": tests,
            "acceptance": acceptance,
            "issue_url": issue_url,
            "queue_position": position,
            # Local delivery evidence proves delivery only; GitHub evidence
            # remains the independent issue readback source.
            "readback_evidence": [issue_url],
            "delivery_evidence": list(delivery_evidence),
        })
    root_url = _text(root.get("url", root.get("html_url")), "root_issue.url")
    return {
        "schema_version": 1,
        "expected_ticket_count": expected_count,
        "source": {
            "kind": "github",
            "root_issue": root_url,
            "evidence": [root_url],
        },
        "queue": queue,
        "tickets": entries,
    }
