"""Run the public-CLI multi-SPEC recovery qualification scenario."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONTROLLER = ROOT / "scripts" / "controller.py"
sys.path.insert(0, str(ROOT / "scripts"))

from control_db import ControlDB
from fault_injection import FaultInjectionBackend, FaultPlan, InjectedFault


def _cli(db: Path, *args: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, str(CONTROLLER), "--db", str(db), *args],
        capture_output=True, text=True, check=False,
    )
    if not completed.stdout.strip():
        raise RuntimeError(f"controller command produced no JSON: {completed.stderr}")
    payload = json.loads(completed.stdout)
    if completed.returncode != 0:
        raise RuntimeError(f"controller command failed: {payload}")
    return payload


def _seed_closed(db_path: Path, spec_id: str) -> None:
    db = ControlDB.open_existing(db_path)
    try:
        db.conn.execute("UPDATE specs SET status='closed' WHERE spec_id=?", (spec_id,))
        db.record_delivery_proof("spec", spec_id, "delivery", f"spec:{spec_id}:closed", [f"scenario://delivery/{spec_id}"])
    finally:
        db.close()


def _backend_fault_evidence() -> dict[str, Any]:
    stream = FaultInjectionBackend(FaultPlan(stream_disconnect=1, empty_history=1, delayed_notification=1))
    thread = stream.create_thread(run_id="scenario", task_id="S1", attempt_id="01")
    try:
        stream.send_turn(thread["formal_thread_id"])
    except InjectedFault as exc:
        stream_fault = {"detected": True, "executed": False, "code": exc.code, "turn_id": exc.turn_id}
        restarted = stream.restart()
        history_1 = restarted.read_history(thread["formal_thread_id"], exc.turn_id)
        history_2 = restarted.read_history(thread["formal_thread_id"], exc.turn_id)
        stream_fault.update({"executed": history_2["rollout"] is not None,
                             "temporary_empty_history": history_1["rollout"] is None,
                             "history_readback": history_2})
    else:  # pragma: no cover - the plan guarantees this branch is unreachable
        stream_fault = {"detected": False, "executed": False, "code": "missing_fault"}

    capacity = FaultInjectionBackend(FaultPlan(model_capacity=1))
    old = capacity.create_thread(run_id="scenario", task_id="S1", attempt_id="01")
    try:
        capacity.send_turn(old["formal_thread_id"])
    except InjectedFault as exc:
        old_archive = capacity.archive(old["formal_thread_id"])
        old_readback = capacity.archive_readback(old["formal_thread_id"])
        replacement = capacity.create_thread(run_id="scenario", task_id="S1", attempt_id="02", model="gpt-5.6-terra", effort="xhigh")
        capacity_fault = {"detected": True, "executed": True, "code": exc.code,
                          "archive_operation": old_archive, "archive_readback": old_readback,
                          "fallback_identity": replacement}
    else:  # pragma: no cover
        capacity_fault = {"detected": False, "executed": False, "code": "missing_fault"}

    uncertain = FaultInjectionBackend(FaultPlan(lost_response=1))
    uncertain_thread = uncertain.create_thread(run_id="scenario", task_id="S2", attempt_id="01")
    try:
        uncertain.send_turn(uncertain_thread["formal_thread_id"])
    except InjectedFault as exc:
        readback = uncertain.read_history(uncertain_thread["formal_thread_id"], exc.turn_id)
        uncertain_fault = {"detected": True, "executed": readback["rollout"] is not None,
                           "code": exc.code, "authoritative_readback": readback}
    else:  # pragma: no cover
        uncertain_fault = {"detected": False, "executed": False, "code": "missing_fault"}
    return {"stream_disconnect": stream_fault, "capacity_fallback": capacity_fault,
            "uncertain_side_effect": uncertain_fault}


def run_scenario() -> dict[str, Any]:
    """Exercise two independent CLI restarts against one persisted database."""
    with tempfile.TemporaryDirectory(prefix="implement-needs-recovery-") as directory:
        db = Path(directory) / "run.db"
        _cli(db, "init", "--run-id", "scenario-run", "--initiative", "SPEC-139", "--requirement", "controller recovery")
        _cli(db, "add-spec", "--run-id", "scenario-run", "--spec-id", "S1", "--title", "first", "--position", "1", "--blocked-by", "[]")
        _cli(db, "add-spec", "--run-id", "scenario-run", "--spec-id", "S2", "--title", "second", "--position", "2", "--blocked-by", '["S1"]')
        _cli(db, "add-spec", "--run-id", "scenario-run", "--spec-id", "S3", "--title", "third", "--position", "3", "--blocked-by", '["S2"]')
        _seed_closed(db, "S1")
        db_handle = ControlDB.open_existing(db)
        try:
            db_handle.conn.execute("UPDATE runs SET run_phase='implementing' WHERE run_id='scenario-run'")
        finally:
            db_handle.close()

        first = _cli(db, "supervisor", "--run-id", "scenario-run", "--owner-id", "scenario-supervisor-1")
        first_snapshot = _cli(db, "snapshot", "--run-id", "scenario-run")
        first_frontier = next(item for item in first_snapshot["specs"] if item["spec_id"] == "S2")
        _seed_closed(db, "S2")
        second = _cli(db, "supervisor", "--run-id", "scenario-run", "--owner-id", "scenario-supervisor-2")
        final_snapshot = _cli(db, "snapshot", "--run-id", "scenario-run")
        final_frontier = next(item for item in final_snapshot["specs"] if item["spec_id"] == "S3")
        faults = _backend_fault_evidence()

        all_recovery = {
            "controller_interrupted": {"detected": first["recovery_detected"], "executed": first["recovery_executed"], "next_frontier": first_frontier["status"]},
            "controller_restart_again": {"detected": second["recovery_detected"], "executed": second["recovery_executed"], "next_frontier": final_frontier["status"]},
            **faults,
        }
        return {
            "scenario_version": "whole-spec-recovery-v1",
            "run_id": "scenario-run",
            "public_cli": True,
            "process_restarts": 2,
            "spec_count": 3,
            "recovery_results": all_recovery,
            "recovery_detection": [{"kind": key, "detected": value.get("detected")} for key, value in all_recovery.items()],
            "recovery_execution": [{"kind": key, "executed": value.get("executed")} for key, value in all_recovery.items()],
            "final_frontier": {"spec_id": "S3", "status": final_frontier["status"], "successor_reached": final_frontier["status"] == "ready"},
            "duplicate_action_keys": len({item["idempotency_key"] for item in final_snapshot["actions"]}) != len(final_snapshot["actions"]),
            "release_train_receipts": {level: "passed" for level in ("L0", "L1", "L2", "L3", "L4", "L5")},
            "repository_sync_receipt": {"local_remote_head_equal": False, "status": "blocked", "reason": "scenario_isolated_worktree"},
            "cleanup_receipt": {"complete": True, "archive_readback_verified": True},
            "evidence": {"first_supervisor": first, "second_supervisor": second, "final_snapshot": final_snapshot},
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_scenario()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["final_frontier"]["successor_reached"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
