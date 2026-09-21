import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from control_db import ControlDB
from managed_recovery import (
    BLOCKED, COMPLETED, MODEL_CAPACITY, NO_PROGRESS, STREAM_DISCONNECTED, UNCERTAIN,
    RecoveryError, assert_no_replay, build_recovery_message, checkpoint,
    classify_turn_outcome, decide_recovery, reconcile_side_effects,
    recover_capacity_attempt, replacement_attempt_allowed,
)


IDENTITY = {
    "task_id": "task-122", "run_id": "run-122", "attempt_id": "01",
    "formal_thread_id": "thread-formal", "host_id": "local", "turn_id": "turn-1",
}


class ManagedRecoveryTests(unittest.TestCase):
    def test_structured_error_precedes_provider_text(self):
        self.assertEqual(MODEL_CAPACITY, classify_turn_outcome(error={"code": "model_capacity"}, transport_error="connection lost"))
        self.assertEqual(STREAM_DISCONNECTED, classify_turn_outcome(transport_error={"code": "stream_disconnected"}))
        self.assertEqual(BLOCKED, classify_turn_outcome(error={"code": "project_not_found"}))
        self.assertEqual(
            UNCERTAIN,
            classify_turn_outcome(
                completion={"status": "completed", "items": []},
                history_readback={"turns": [{"id": "turn-1", "items": []}]},
            ),
        )
        self.assertEqual(
            COMPLETED,
            classify_turn_outcome(
                completion={"status": "completed"},
                history_readback={"turns": [{"id": "turn-1", "output": "verified handoff"}]},
            ),
        )

    def test_repeated_empty_completion_is_no_progress_and_blocks_recovery(self):
        history = {"turns": [
            {"id": "turn-1", "status": "completed", "items": []},
            {"id": "turn-2", "status": "completed", "items": []},
            {"id": "turn-3", "status": "completed", "items": []},
        ]}
        self.assertEqual(
            NO_PROGRESS,
            classify_turn_outcome(
                completion={"status": "completed"},
                history_readback=history,
                turn_id="turn-3",
            ),
        )
        decision = decide_recovery(NO_PROGRESS)
        self.assertEqual("repair", decision.action)
        self.assertEqual("blocked", decision.next_action)

    def test_checkpointed_continue_is_identity_bound_and_not_a_replay(self):
        cp = checkpoint(identity=IDENTITY, previous_turn_id="turn-1", failure_class=STREAM_DISCONNECTED,
                        durable_state={"commit": "abc", "receipts": ["issue:123"]})
        message = json.loads(build_recovery_message(identity=IDENTITY, previous_turn_id="turn-1",
                                                    failure_class=STREAM_DISCONNECTED, checkpoint_data=cp))
        self.assertEqual("continue", message["action"])
        self.assertEqual("turn-1", message["previous_turn_id"])
        self.assertEqual("abc", message["checkpoint"]["durable_state"]["commit"])
        with self.assertRaises(RecoveryError):
            assert_no_replay(original_turn_status=COMPLETED, assignment_idempotency_key="assignment:1")

    def test_reconnect_completed_wins_over_recovery(self):
        decision = decide_recovery(STREAM_DISCONNECTED, thread_readback={
            "requested_formal_thread_id": "thread-formal", "formal_thread_id": "thread-formal", "host_id": "local", "lifecycle": "working",
        }, history_readback={"turn_completed": True, "turns": [{"output": "verified"}]})
        self.assertEqual(COMPLETED, decision.failure_class)
        self.assertEqual("verify_completed_after_reconnect", decision.action)

    def test_stream_disconnect_reconciles_partial_side_effect_before_continue(self):
        decision = decide_recovery(STREAM_DISCONNECTED, thread_readback={
            "requested_formal_thread_id": "thread-formal", "formal_thread_id": "thread-formal", "host_id": "local", "lifecycle": "working",
        }, side_effects={"status": "unknown"})
        self.assertEqual("reconcile_side_effects", decision.action)
        reconciled = reconcile_side_effects(
            operation_intents=[{"idempotency_key": "commit:1"}, {"idempotency_key": "issue:1"}],
            readbacks={"commit:1": {"status": "verified", "evidence": ["git:readback"]},
                       "issue:1": {"status": "not_found", "evidence": ["github:list"]}},
        )
        self.assertEqual("reconciled", reconciled["status"])

    def test_capacity_requires_archive_and_applied_fallback_before_replacement(self):
        first = decide_recovery(MODEL_CAPACITY, old_attempt_archived=False, fallback_route_verified=False)
        self.assertEqual("archive_failed_attempt", first.action)
        blocked = decide_recovery(MODEL_CAPACITY, old_attempt_archived=True, fallback_route_verified=False)
        self.assertEqual(BLOCKED, blocked.failure_class)
        allowed = replacement_attempt_allowed(
            old_attempt_archived=True, archive_readback={"archived": True},
            route_readback={"model": "fallback", "effort": "high"},
            new_identity={**IDENTITY, "attempt_id": "02", "formal_thread_id": "replacement"},
        )
        self.assertTrue(allowed)

    def test_budget_exhaustion_is_blocked(self):
        decision = decide_recovery(STREAM_DISCONNECTED, attempts=2, budget=2,
                                   thread_readback={"requested_formal_thread_id": "thread-formal", "formal_thread_id": "thread-formal", "host_id": "local"})
        self.assertEqual(BLOCKED, decision.failure_class)

    def test_capacity_replacement_calls_archive_readback_route_before_create(self):
        calls = []
        result = recover_capacity_attempt(
            archive=lambda: calls.append("archive") or {"ok": True},
            archive_readback=lambda: calls.append("archive_readback") or {"archived": True},
            fallback_route_readback=lambda: calls.append("route_readback") or {"model": "fallback", "effort": "high"},
            create_replacement=lambda identity, route: calls.append("create") or {"formal_thread_id": identity["formal_thread_id"], "route": route},
            new_identity={**IDENTITY, "attempt_id": "02", "formal_thread_id": "replacement"},
        )
        self.assertEqual(["archive", "archive_readback", "route_readback", "create"], calls)
        self.assertEqual("replacement", result["replacement"]["formal_thread_id"])

    def test_managed_turn_receipt_survives_restart_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.db"
            db = ControlDB(path)
            db.create_run("run-122", "initiative", "requirement")
            created = db.record_managed_turn("run-122", IDENTITY, STREAM_DISCONNECTED, checkpoint={"digest": "x"})
            duplicate = db.record_managed_turn("run-122", IDENTITY, STREAM_DISCONNECTED, checkpoint={"digest": "x"})
            self.assertTrue(created["created"])
            self.assertFalse(duplicate["created"])
            db.close()
            reopened = ControlDB(path)
            self.assertEqual("turn-1", reopened.managed_turn("run-122", "thread-formal", "turn-1")["turn_id"])
            updated = reopened.update_managed_turn("run-122", "thread-formal", "turn-1", status="failed", failure_class=UNCERTAIN)
            self.assertEqual("failed", updated["status"])
            reopened.close()


if __name__ == "__main__":
    unittest.main()
