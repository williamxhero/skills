import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation/scripts"))
import whole_spec_scenario as module


class WholeSpecScenarioTests(unittest.TestCase):
    def test_fault_matrix_is_three_spec_and_side_effect_free(self):
        result = module.run_fault_matrix(seed=129)
        self.assertEqual(3, len(result["plan"]["specs"]))
        self.assertEqual("stream_disconnected", result["first_attempt"]["status"])
        self.assertEqual("completed", result["checkpoint_continue"]["status"])
        self.assertEqual("model_capacity", result["capacity_failure"]["error"]["code"])
        self.assertTrue(result["archive_readback"]["archived"])
        backend = module.FaultInjectingBackend(module.FaultPlan(delayed_history_reads=1))
        thread = backend.create_thread(run_id="r", task_id="S", attempt_id="01")
        turn = backend.send(thread["formal_thread_id"], thread["host_id"])
        self.assertIsNone(backend.read_persisted_history(thread["formal_thread_id"], turn["turn_id"])["rollout"])
        self.assertIsNotNone(backend.read_persisted_history(thread["formal_thread_id"], turn["turn_id"])["rollout"])
        self.assertFalse(result["live_external_evidence"])

    def test_takeover_matrix_covers_every_supported_entry_stage(self):
        result = module.run_takeover_matrix()
        self.assertTrue(result["all_stages_covered"])
        self.assertEqual(
            {
                "requirement",
                "planning",
                "ticketing",
                "implementation",
                "verification",
                "merge_cleanup",
                "final_verification",
                "release",
                "synchronization",
                "terminal",
                "managed_run",
            },
            {item["entry_stage"] for item in result["cases"]},
        )
        self.assertEqual(0, result["resources_created"])


if __name__ == "__main__":
    unittest.main()
