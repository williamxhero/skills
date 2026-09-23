"""One read-only installed-wheel SDK probe; not a workflow substitute."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from spec_runner.codex_adapter import CodexAdapter


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", default="low")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if manifest.get("repository") != "williamxhero/skills":
        raise ValueError("probe requires the authorized repository")
    marker = manifest["run_marker"]
    repository = Path(manifest["repository_path"])
    schema = {
        "type": "object",
        "properties": {"marker": {"type": "string"}, "question": {"type": "string"}},
        "required": ["marker", "question"],
        "additionalProperties": False,
    }
    adapter = CodexAdapter()
    def record_thread(thread_id: str, turn_id: str) -> None:
        # Only the test manifest is changed. This callback runs after the SDK
        # supplies a durable turn ID and before waiting for the worker result.
        current = json.loads(args.manifest.read_text(encoding="utf-8"))
        threads = current["resources"]["threads"]
        if thread_id not in threads:
            threads.append(thread_id)
        current["active_turn"] = turn_id
        args.manifest.write_text(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    result = adapter.run_semantic(
        phase="grill",
        repository_path=repository,
        model=args.model,
        effort=args.effort,
        trusted={"task": "Using the current installed grilling method, return one useful clarification question about a task CSV to JSON tool. Do not run tools or change files.", "run_marker": marker},
        untrusted={},
        schema=schema,
        on_turn_started=record_thread,
    )
    report = {
        "schema_version": "spec-runner-live-skill-probe/v1",
        "run_marker": marker,
        "sdk_result": result.public(),
    }
    report_path = args.manifest.parent.parent.parent / "reports" / f"{marker}-skill-probe.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"report": os.fspath(report_path), "thread_id": result.thread_id, "turn_id": result.turn_id, "status": result.status}, ensure_ascii=False))
    return 0 if result.status == "completed" else 3


if __name__ == "__main__":
    raise SystemExit(main())
