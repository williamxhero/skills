#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def verified_parent(value, expected):
    return (
        isinstance(value, dict)
        and set(value) == {"parent_id", "evidence"}
        and value.get("parent_id") == expected
        and isinstance(value.get("evidence"), list)
        and bool(value["evidence"])
        and all(nonempty(item) for item in value["evidence"])
    )


def validate(data):
    errors = []
    if set(data) != {"schema_version", "grill", "umbrella_spec", "specs"}:
        errors.append("top-level fields must match the contract")
    if data.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    grill = data.get("grill")
    if not isinstance(grill, dict) or grill.get("frontier_empty") is not True or not grill.get("round_evidence"):
        errors.append("Grill must have an empty frontier and round evidence")
    umbrella = data.get("umbrella_spec")
    umbrella_id = umbrella.get("id") if isinstance(umbrella, dict) else None
    if not isinstance(umbrella, dict) or set(umbrella) != {"id", "artifact"} or not all(nonempty(umbrella.get(k)) for k in ("id", "artifact")):
        errors.append("umbrella_spec must contain non-empty id and artifact")
    specs = data.get("specs")
    if not isinstance(specs, list) or not specs:
        errors.append("at least one child SPEC is required")
        specs = []
    spec_ids = []
    for si, spec in enumerate(specs):
        path = f"specs[{si}]"
        if not isinstance(spec, dict) or set(spec) != {"id", "artifact", "parent_issue", "tickets"}:
            errors.append(f"{path} fields must match the contract")
            continue
        spec_id = spec.get("id")
        spec_ids.append(spec_id)
        if not nonempty(spec_id) or not nonempty(spec.get("artifact")):
            errors.append(f"{path} id and artifact must be non-empty")
        if not verified_parent(spec.get("parent_issue"), umbrella_id):
            errors.append(f"{path} must have verified umbrella Parent issue")
        tickets = spec.get("tickets")
        if not isinstance(tickets, list) or not tickets:
            errors.append(f"{path} requires at least one ticket")
            continue
        ticket_ids = []
        for ti, ticket in enumerate(tickets):
            tpath = f"{path}.tickets[{ti}]"
            if not isinstance(ticket, dict) or set(ticket) != {"id", "artifact", "parent_issue"}:
                errors.append(f"{tpath} fields must match the contract")
                continue
            ticket_ids.append(ticket.get("id"))
            if not all(nonempty(ticket.get(k)) for k in ("id", "artifact")):
                errors.append(f"{tpath} id and artifact must be non-empty")
            if not verified_parent(ticket.get("parent_issue"), spec_id):
                errors.append(f"{tpath} must have verified owning-SPEC Parent issue")
        if len(ticket_ids) != len(set(ticket_ids)):
            errors.append(f"{path} ticket ids must be unique")
    if umbrella_id in spec_ids:
        errors.append("umbrella SPEC cannot be a child SPEC")
    if len(spec_ids) != len(set(spec_ids)):
        errors.append("child SPEC ids must be unique")
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("record", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.record.read_text(encoding="utf-8"))
        errors = validate(data) if isinstance(data, dict) else ["record must be an object"]
    except (OSError, json.JSONDecodeError) as exc:
        errors = [f"cannot read record: {exc}"]
    print(json.dumps({"decision": "allow" if not errors else "reject", "errors": errors}, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
