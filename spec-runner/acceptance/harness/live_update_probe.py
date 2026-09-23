"""Exercise A to B Skill refresh on one authenticated SDK thread."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from spec_runner.codex_adapter import CodexAdapter


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-skill", required=True, type=Path)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if manifest.get("repository") != "williamxhero/skills":
        raise ValueError("probe requires the authorized skills repository")
    repository = Path(manifest["repository_path"])
    root = (args.manifest.parent / "controlled-skill").resolve()
    source = root / "grilling" / "SKILL.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    original = args.source_skill.read_text(encoding="utf-8")
    config = root / "mapping.json"
    config.write_text(json.dumps({"schema_version": "spec-runner-live-skills/v1", "roots": [str(root)], "skills": {"grill": {"name": "grilling", "path": str(source)}}}), encoding="utf-8")
    schema = {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"], "additionalProperties": False}
    adapter = CodexAdapter()
    results: list[dict[str, object]] = []
    previous_thread: str | None = None
    for version, expected in (("A", "ALPHA QUESTION"), ("B", "BETA QUESTION")):
        source.write_text(original + f"\n\n## Acceptance-specific current instruction\nFor this controlled test, return exactly {{\"question\": \"{expected}\"}}. Do not run tools.\n", encoding="utf-8", newline="\n")
        def record_thread(thread_id: str, turn_id: str) -> None:
            current = json.loads(args.manifest.read_text(encoding="utf-8"))
            if thread_id not in current["resources"]["threads"]:
                current["resources"]["threads"].append(thread_id)
            current["active_turn"] = turn_id
            args.manifest.write_text(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        result = adapter.run_semantic(
            phase="grill",
            repository_path=repository,
            model=args.model,
            effort="low",
            trusted={"task": "Follow the current installed Skill version's acceptance-specific instruction. Return only the requested JSON question."},
            untrusted={},
            schema=schema,
            skill_config=config,
            thread_id=previous_thread,
            on_turn_started=record_thread,
        )
        previous_thread = result.thread_id
        parsed = json.loads(result.final_response or "null")
        results.append({"version": version, "expected": expected, "actual": parsed, "thread_id": result.thread_id, "turn_id": result.turn_id, "skill_observation": result.skill_observation, "status": result.status})
    archived = adapter.archive_and_readback(thread_id=previous_thread or "", repository_path=repository)
    passed = all(item["status"] == "completed" and item["actual"] == {"question": item["expected"]} for item in results)
    passed = passed and results[0]["thread_id"] == results[1]["thread_id"] and results[0]["skill_observation"]["source_digest"] != results[1]["skill_observation"]["source_digest"]
    report = {"schema_version": "spec-runner-live-skill-update-probe/v1", "run_marker": manifest["run_marker"], "source_copy_of": str(args.source_skill.resolve()), "results": results, "archive_readback": archived, "passed": passed}
    report_path = args.manifest.parent.parent.parent / "reports" / f"{manifest['run_marker']}-skill-update.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"report": str(report_path), "passed": passed, "thread_id": previous_thread}, ensure_ascii=False))
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
