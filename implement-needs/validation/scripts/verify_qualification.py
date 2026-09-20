"""Independently verify an Implement Needs qualification report."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from validation.qualification import QUALIFIED, load_json, verify_report, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    try:
        decision = verify_report(load_json(args.report), load_json(args.scenario))
    except (OSError, ValueError) as exc:
        decision = {"decision": "REJECTED", "reasons": [f"input_error:{exc}"]}
    if args.receipt:
        write_json(args.receipt, decision)
    import json
    print(json.dumps(decision, ensure_ascii=False))
    return 0 if decision["decision"] == QUALIFIED else 2


if __name__ == "__main__":
    raise SystemExit(main())
