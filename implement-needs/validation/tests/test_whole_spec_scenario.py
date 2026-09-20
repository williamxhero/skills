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
        self.assertFalse(result["live_external_evidence"])


if __name__ == "__main__":
    unittest.main()
