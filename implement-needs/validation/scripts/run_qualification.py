"""Create and verify a fail-closed Implement Needs qualification run.

This command is intentionally an evidence coordinator rather than a second
orchestrator.  The existing controller performs delivery; an operator or
adapter records its public GitHub, task-backend, Git and test readbacks in the
report.  The final result is decided again by verify_qualification.py.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = ROOT
sys.path.insert(0, str(ROOT))

from validation.qualification import (
    QUALIFIED, REJECTED, backend_errors, digest_paths, digest_tree, load_json, project_identity_errors,
    qualification_key, scenario_errors, verify_report, write_json,
)
from validation.scripts.whole_spec_scenario import run_takeover_matrix


def _receipt(path: Path | None) -> dict[str, Any] | None:
    return load_json(path) if path else None


def _digests() -> dict[str, str]:
    subject_files = [ROOT / "SKILL.md", *((ROOT / "references").glob("*.md")),
                     *((ROOT / "scripts").glob("*.py"))]
    return {
        "skill_digest": digest_paths(subject_files, root=ROOT),
        "harness_digest": digest_tree(SKILL_ROOT / "validation", exclude=(SKILL_ROOT / "validation/reports",)),
    }


def _preflight(scenario: dict[str, Any], project: dict[str, Any] | None, backend: dict[str, Any] | None) -> dict[str, Any]:
    reasons = scenario_errors(scenario) + project_identity_errors(project) + backend_errors(backend)
    # project id is obtained by formal readback. A request id is never a substitute.
    return {"decision": "allow" if not reasons else "repair", "reasons": sorted(set(reasons)),
            "project_identity_receipt": project, "backend_receipt": backend, **_digests()}


def _report_path(run_id: str) -> Path:
    return SKILL_ROOT / "validation/reports" / run_id / "qualification.json"


def _check_git() -> dict[str, Any]:
    try:
        result = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                text=True, capture_output=True, check=True)
        return {"checked": True, "worktree_clean": not bool(result.stdout.strip())}
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"checked": False, "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "qualify"):
        command = sub.add_parser(name)
        command.add_argument("--scenario", default="whole-spec-v1")
        command.add_argument("--backend", choices=("thread", "subagent"), required=True)
        command.add_argument("--project-identity", type=Path, required=True)
        command.add_argument("--backend-receipt", type=Path, required=True)
        command.add_argument("--run-id")
    qualify = sub.choices["qualify"]
    qualify.add_argument("--evidence-report", type=Path, required=True,
                         help="Externally collected public readbacks from the controller run")
    args = parser.parse_args()
    scenario_path = SKILL_ROOT / "validation/scenarios" / f"{args.scenario}.json"
    try:
        scenario = load_json(scenario_path)
        preflight = _preflight(scenario, _receipt(args.project_identity), _receipt(args.backend_receipt))
    except (OSError, ValueError) as exc:
        preflight = {"decision": "repair", "reasons": [f"input_error:{exc}"], **_digests()}
        scenario = {}
    run_id = args.run_id or f"qualification-{uuid.uuid4().hex[:12]}"
    if args.backend != "thread":
        preflight["decision"] = "repair"
        preflight["reasons"] = sorted(set(preflight["reasons"] + ["backend_not_yet_qualified"]))
    preflight.update({"run_id": run_id, "scenario": args.scenario, "backend": args.backend, "git": _check_git()})
    if args.command == "check":
        print(json.dumps(preflight, ensure_ascii=False))
        return 0 if preflight["decision"] == "allow" else 3
    if preflight["decision"] != "allow":
        report = {"run_id": run_id, "scenario_version": scenario.get("version"), "backend_kind": args.backend,
                  "project_identity_receipt": preflight.get("project_identity_receipt"), **_digests()}
        decision = {"decision": REJECTED, "reasons": preflight["reasons"], "run_id": run_id}
    else:
        report = load_json(args.evidence_report)
        report.update({"run_id": run_id, "backend_kind": args.backend,
                       "scenario_version": scenario.get("version"), **_digests(),
                       "project_identity_receipt": preflight["project_identity_receipt"],
                       "backend_capability_receipt": preflight["backend_receipt"],
                       "takeover_results": run_takeover_matrix(),
                       "backend_contract_version": report.get("backend_contract_version", 1)})
        decision = verify_report(report, scenario)
    report["qualification_key"] = qualification_key(report)
    report["verification"] = decision
    target = _report_path(run_id)
    write_json(target, report)
    write_json(target.parent / "preflight.json", preflight)
    print(json.dumps({"report": str(target), **decision}, ensure_ascii=False))
    return 0 if decision["decision"] == QUALIFIED else 2


if __name__ == "__main__":
    raise SystemExit(main())
