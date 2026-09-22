"""Read-only incident probe. Does not treat Desktop's empty projection as history."""
import argparse
import json
import sqlite3
from pathlib import Path

THREAD = "01a0bf64-26cb-7b23-849a-ca51fa8dc0bd"
RUN = "implement-needs-82-20260919"
ROLLOUT = Path(r"C:\Users\will\.codex\sessions\2026\09\20\rollout-2026-09-20T23-16-47-01a0bf64-26cb-7b23-849a-ca51fa8dc0bd.jsonl")
DB = Path(r"D:\WILL\STOCK\stock_advisor\.scratch\implement-needs-82\implement-needs.db")


def audit():
    turns = {}
    current = None
    for line_number, line in enumerate(ROLLOUT.open(encoding="utf-8"), 1):
        event = json.loads(line)
        payload = event.get("payload", {})
        if event.get("type") == "turn_context":
            current = payload.get("turn_id")
            turns.setdefault(current, {
                "turn_id": current, "model": payload.get("model"),
                "effort": payload.get("effort"), "cwd": payload.get("cwd"),
                "context_line": line_number, "tool_calls": 0, "messages": [],
            })
        if current and event.get("type") == "response_item":
            row = turns[current]
            if payload.get("type") in {"function_call", "custom_tool_call"}:
                row["tool_calls"] += 1
            if payload.get("type") == "message" and payload.get("role") == "assistant":
                row["messages"].append({"line": line_number, "phase": payload.get("phase"),
                    "text": "".join(x.get("text", "") for x in payload.get("content", []))})
        if event.get("type") == "event_msg" and payload.get("type") == "task_complete":
            completed = payload.get("turn_id")
            if completed in turns:
                turns[completed]["completed"] = True
    with sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        run = dict(conn.execute("SELECT * FROM runs WHERE run_id=?", (RUN,)).fetchone())
        specs = [dict(r) for r in conn.execute("SELECT spec_id,status FROM specs WHERE run_id=? ORDER BY position", (RUN,))]
        child = [dict(r) for r in conn.execute("SELECT formal_thread_id,lifecycle,outcome FROM threads WHERE run_id=? AND spec_id='#95'", (RUN,))]
        counts = {table: conn.execute(f"SELECT count(*) FROM {table} WHERE run_id=?", (RUN,)).fetchone()[0]
                  for table in ("managed_turns", "operation_intents", "recovery_states")}
    return {"formal_thread_id": THREAD, "host_id": "local", "run_id": RUN,
            "source_rollout": str(ROLLOUT), "run": run, "specs": specs,
            "spec95_tasks": child, "recovery_record_counts": counts,
            "turns": list(turns.values())[-7:]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-tools", action="store_true")
    parser.add_argument("--require-progress", action="store_true")
    args = parser.parse_args()
    result = audit()
    latest = result["turns"][-1]
    failures = []
    if args.require_tools and latest["tool_calls"] == 0:
        failures.append("latest_turn_has_no_tool_execution")
    if args.require_progress and result["run"]["business_version"] <= 461:
        failures.append("controller_did_not_advance_beyond_version_461")
    result["probe"] = {"decision": "FAIL" if failures else "PASS", "failures": failures}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"probe": result["probe"], "latest_turn": latest["turn_id"],
        "tool_calls": latest["tool_calls"], "model": latest["model"],
        "business_version": result["run"]["business_version"], "spec95_tasks": result["spec95_tasks"]}))
    raise SystemExit(bool(failures))
