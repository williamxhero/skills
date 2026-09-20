"""Check whether a normal Implement Needs run has a matching qualification."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from validation.qualification import QUALIFIED, digest_paths, digest_tree, load_json


def current_key() -> dict[str, str]:
    subject_files = [ROOT / "SKILL.md", *((ROOT / "references").glob("*.md")),
                     *((ROOT / "scripts").glob("*.py"))]
    return {
        "skill_digest": digest_paths(subject_files, root=ROOT),
        "harness_digest": digest_tree(ROOT / "validation", exclude=(ROOT / "validation/reports",)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=ROOT / "validation/reports/index.json")
    parser.add_argument("--scenario", default="whole-spec-v1")
    parser.add_argument("--backend", default="thread")
    args = parser.parse_args()
    key = current_key()
    try:
        index = load_json(args.index)
    except (OSError, ValueError) as exc:
        result = {"decision": "blocked", "reason": "qualification_index_unavailable", "detail": str(exc), **key}
        print(json.dumps(result, ensure_ascii=False)); return 3
    matches = []
    for entry in index.get("qualifications", []):
        if not isinstance(entry, dict):
            continue
        if (entry.get("decision") == QUALIFIED and entry.get("scenario_version") == args.scenario
                and entry.get("backend_kind") == args.backend
                and all(entry.get(name) == value for name, value in key.items())):
            matches.append(entry)
    result = {"decision": "allow" if matches else "blocked",
              "reason": "matching_qualification" if matches else "qualification_missing",
              "matches": matches, **key}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if matches else 3


if __name__ == "__main__":
    raise SystemExit(main())
