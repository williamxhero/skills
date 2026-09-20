import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from fault_injection import FAULTS, FaultInjectionBackend, FaultPlan, InjectedFault


class FaultInjectionTests(unittest.TestCase):
    def test_every_fault_is_named_and_replayable(self):
        self.assertIn("parent_turn_end", FAULTS)
        backend = FaultInjectionBackend(FaultPlan(parent_turn_end=1))
        thread = backend.create_thread(run_id="run", task_id="S1", attempt_id="01")
        with self.assertRaisesRegex(InjectedFault, "parent_turn_end"):
            backend.send_turn(thread["formal_thread_id"])
        restarted = backend.restart()
        self.assertEqual(thread["formal_thread_id"], restarted.read_thread(thread["formal_thread_id"])["formal_thread_id"])
        self.assertEqual(1, len([event for event in restarted.events if event["type"] == "parent_turn_ended"]))

    def test_temporary_empty_history_recovers_but_permanent_empty_does_not(self):
        backend = FaultInjectionBackend(FaultPlan(empty_history=1))
        thread = backend.create_thread(run_id="run", task_id="S1", attempt_id="01")
        turn = backend.send_turn(thread["formal_thread_id"])
        self.assertIsNone(backend.read_history(thread["formal_thread_id"], turn["turn_id"])["rollout"])
        self.assertIsNotNone(backend.read_history(thread["formal_thread_id"], turn["turn_id"])["rollout"])
        permanent = FaultInjectionBackend(FaultPlan(permanent_empty_history=True))
        item = permanent.create_thread(run_id="run", task_id="S1", attempt_id="01")
        completed = permanent.send_turn(item["formal_thread_id"])
        self.assertIsNone(permanent.read_history(item["formal_thread_id"], completed["turn_id"])["rollout"])
        self.assertIsNone(permanent.read_history(item["formal_thread_id"], completed["turn_id"])["rollout"])

    def test_lost_response_and_delayed_notification_are_reconciled_by_readback(self):
        backend = FaultInjectionBackend(FaultPlan(lost_response=1, delayed_notification=1))
        thread = backend.create_thread(run_id="run", task_id="S1", attempt_id="01")
        with self.assertRaisesRegex(InjectedFault, "lost_response"):
            backend.send_turn(thread["formal_thread_id"])
        event = next(event for event in backend.events if event["type"] == "response_lost")
        turn_id = event["turn_id"]
        history = backend.read_history(thread["formal_thread_id"], turn_id)
        self.assertEqual("completed", history["rollout"]["status"])
        notifications = backend.drain_notifications()
        self.assertTrue(notifications[0]["delayed"])

    def test_route_identity_and_archive_readback_fail_closed(self):
        backend = FaultInjectionBackend(FaultPlan(route_drift=True, identity_drift=True, archive_readback_failure=True))
        thread = backend.create_thread(run_id="run", task_id="S1", attempt_id="01")
        self.assertNotEqual(thread["formal_thread_id"], backend.read_thread(thread["formal_thread_id"])["formal_thread_id"])
        self.assertNotEqual("gpt-5.6-sol", backend.read_applied_route(thread["formal_thread_id"])["model"])
        backend.archive(thread["formal_thread_id"])
        self.assertFalse(backend.archive_readback(thread["formal_thread_id"])["archived"])


if __name__ == "__main__":
    unittest.main()
