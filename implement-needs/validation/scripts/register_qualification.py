"""Atomically register only an independently verified QUALIFIED report."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT))

from validation.qualification import QUALIFIED, load_json, qualification_key, verify_report


def register(*, index_path: Path, scenario_path: Path, report_path: Path) -> dict:
    scenario = load_json(scenario_path)
    report = load_json(report_path)
    decision = verify_report(report, scenario)
    if decision["decision"] != QUALIFIED:
        return {"decision": "blocked", "reason": "report_not_qualified", "verification": decision}
    try:
        index = load_json(index_path)
    except FileNotFoundError:
        index = {"schema_version": 1, "qualifications": []}
    entries = index.get("qualifications")
    if not isinstance(entries, list):
        return {"decision": "blocked", "reason": "qualification_index_invalid"}
    entry = {**qualification_key(report), "decision": QUALIFIED, "run_id": report.get("run_id"),
             "report": str(report_path.resolve().parent.relative_to(index_path.resolve().parent)).replace("\\", "/") + "/" + report_path.name}
    fields = ("scenario_version", "skill_digest", "harness_digest", "backend_kind", "backend_contract_version")
    if any(isinstance(item, dict) and all(item.get(k) == entry.get(k) for k in fields) for item in entries):
        return {"decision": "already_registered", "entry": entry}
    updated = {"schema_version": 1, "qualifications": [*entries, entry]}
    index_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix="qualification-index-", suffix=".json", dir=index_path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(updated, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp_name, index_path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return {"decision": "registered", "entry": entry}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = register(index_path=args.index, scenario_path=args.scenario, report_path=args.report)
    except (OSError, ValueError, KeyError) as exc:
        result = {"decision": "blocked", "reason": "qualification_input_error", "detail": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["decision"] in {"registered", "already_registered"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
