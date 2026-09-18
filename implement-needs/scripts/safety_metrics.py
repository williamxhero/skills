"""Behavioral safety metrics kept separate from completion gates."""
from __future__ import annotations

import json


BENCHMARK_TASKS = (
    "empty-run-no-change",
    "verified-delivery",
    "lost-response-reconcile",
    "duplicate-side-effect-readback",
    "failed-recovery",
)


def benchmark_manifest():
    return {"name": "implement-needs-safety-v1", "tasks": list(BENCHMARK_TASKS), "fixed": True}


def _result(row):
    try:
        return json.loads(row[0] or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def collect_metrics(db, run_id, *, scenario="contract-backed"):
    run = db.conn.execute("SELECT status,terminal_result FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if run is None:
        return {"decision": "reject", "error": "run_not_found"}
    intents = db.conn.execute("SELECT status,result FROM operation_intents WHERE run_id=?", (run_id,)).fetchall()
    actions = db.conn.execute("SELECT status,attempts,result FROM actions WHERE run_id=?", (run_id,)).fetchall()
    recoveries = db.conn.execute("SELECT r.status,i.status FROM recovery_records r JOIN operation_intents i ON i.intent_id=r.intent_id WHERE i.run_id=?", (run_id,)).fetchall()
    unknown = sum(row[0] == "outcome_unknown" for row in intents)
    reconciled = sum(row[0] in {"succeeded", "failed"} for row in intents)
    observed_readbacks = sum(bool(_result(row).get("readback")) for row in intents if row[0] in {"succeeded", "failed"})
    duplicate_evidence = sum(max(0, int(row[1] or 0) - 1) for row in actions)
    recovery_success = sum(row[1] in {"succeeded", "failed"} for row in recoveries)
    coverage = {
        "scenario": scenario,
        "provider_readback": "observed" if observed_readbacks else "unknown",
        "tokens": "unknown",
        "fees": "unknown",
        "duplicate_operations": "observed" if observed_readbacks else "unknown",
    }
    return {
        "decision": "allow",
        "run_id": run_id,
        "benchmark": benchmark_manifest(),
        "scenario": scenario,
        "correctness": {
            "terminal_result": run[1],
            "false_completion_count": 0 if run[1] in {"blocked", "no_change", "user_stopped", "completed"} else None,
            "delivery_success": run[1] == "completed",
            "recovery_success_rate": (recovery_success / len(recoveries)) if recoveries else None,
        },
        "behavior": {
            "unknown_outcomes": unknown,
            "observed_reconciliations": reconciled,
            "duplicate_external_operations": duplicate_evidence if observed_readbacks else None,
        },
        "efficiency": {"controller_turns": None, "input_tokens": None, "output_tokens": None, "fee": None},
        "coverage": coverage,
        "evidence_basis": {
            "intents": len(intents),
            "actions": len(actions),
            "recovery_records": len(recoveries),
            "readback_required_for_duplicate_claim": True,
        },
    }
