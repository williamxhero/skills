#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROUTE_POLICY_DIR = Path(__file__).resolve().parents[2] / "route-codex-task" / "scripts"
sys.path.insert(0, str(ROUTE_POLICY_DIR))
from route_policy import (
    DIFFICULTY_FLOOR,
    POLICY_ERROR,
    all_policy_capabilities,
    validate_route,
)

DIFFICULTIES = set(DIFFICULTY_FLOOR)


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


def verified_route(value, difficulty, xhigh_evidence):
    issues = []
    validate_route(
        value,
        "$.route",
        all_policy_capabilities(),
        issues,
        minimum=DIFFICULTY_FLOOR.get(difficulty),
        xhigh_evidence=xhigh_evidence if isinstance(xhigh_evidence, list) else [],
    )
    return not issues


def validate(data):
    errors = []
    if POLICY_ERROR is not None:
        errors.append(f"route-codex-task policy is invalid: {POLICY_ERROR}")
    if set(data) != {"schema_version", "grill", "umbrella_spec", "specs"}:
        errors.append("top-level fields must match the contract")
    if data.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    grill = data.get("grill")
    rounds = grill.get("round_evidence") if isinstance(grill, dict) else None
    if not isinstance(grill, dict) or grill.get("frontier_empty") is not True or not isinstance(rounds, list) or not rounds:
        errors.append("Grill must have an empty frontier and round evidence")
    else:
        for ri, entry in enumerate(rounds):
            required = {"round", "questions", "acceptance_command", "acceptance_evidence", "resume_evidence"}
            if not isinstance(entry, dict) or not required.issubset(entry) or entry.get("acceptance_command") != "全部采用推荐选项/答案":
                errors.append(f"grill round {ri + 1} lacks structured acceptance evidence")
                continue
            questions = entry.get("questions")
            if not isinstance(questions, list) or not questions:
                errors.append(f"grill round {ri + 1} requires questions")
            for qi, question in enumerate(questions or []):
                if not isinstance(question, dict) or not all(nonempty(question.get(k)) for k in ("question", "recommendation", "rationale")) or not isinstance(question.get("number"), int):
                    errors.append(f"grill round {ri + 1} question {qi + 1} is incomplete")
            if not isinstance(entry.get("acceptance_evidence"), list) or not entry["acceptance_evidence"] or not isinstance(entry.get("resume_evidence"), list) or not entry["resume_evidence"]:
                errors.append(f"grill round {ri + 1} lacks resume evidence")
        if isinstance(rounds[-1], dict) and (not rounds[-1].get("frontier_empty") or not rounds[-1].get("frontier_empty_evidence")):
            errors.append("final Grill round must prove frontier_empty=true")
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
        if not isinstance(spec, dict) or set(spec) != {"id", "artifact", "difficulty", "xhigh_evidence", "route", "parent_issue", "tickets"}:
            errors.append(f"{path} fields must match the contract")
            continue
        spec_id = spec.get("id")
        spec_ids.append(spec_id)
        if not nonempty(spec_id) or not nonempty(spec.get("artifact")):
            errors.append(f"{path} id and artifact must be non-empty")
        difficulty = spec.get("difficulty")
        if difficulty not in DIFFICULTIES:
            errors.append(f"{path} difficulty must be easy, standard, hard, or extreme")
        xhigh_evidence = spec.get("xhigh_evidence")
        if not isinstance(xhigh_evidence, list) or not all(nonempty(item) for item in xhigh_evidence):
            errors.append(f"{path} xhigh_evidence must be an array of non-empty strings")
        if not verified_route(spec.get("route"), difficulty, xhigh_evidence):
            errors.append(f"{path} must have a valid justified whole-SPEC implementation route")
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
