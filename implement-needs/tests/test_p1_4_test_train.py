import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from control_db import ControlDB
from next_action import next_action


class TestTrainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = ControlDB(Path(self.temp.name) / "run.db")
        self.addCleanup(self.db.close)
        self.db.create_run("run", "initiative", "requirement")
        for index in range(1, 12):
            self.db.add_spec("run", f"S{index}", f"spec {index}", index, expected_version=self.db.business_version("run"))

    def test_fixed_ten_spec_membership_and_due_checkpoint_block(self):
        ids = [f"S{index}" for index in range(1, 12)]
        self.db.initialize_test_train("run", ids, expected_version=self.db.business_version("run"))
        status = self.db.test_train_status("run")
        self.assertEqual([1, 2], [checkpoint["sequence"] for checkpoint in status["checkpoints"]])
        self.assertEqual([], status["due_checkpoints"])
        for index in range(1, 11):
            self.db.update_spec(f"S{index}", "cancelled", expected_version=self.db.business_version("run"))
        status = self.db.test_train_status("run")
        self.assertEqual([1], status["due_checkpoints"])
        self.assertEqual("run_checkpoint", next_action(self.db, "run")["kind"])
        checkpoint = self.db.record_checkpoint("run", 1, "passed", "candidate-a", ["check://l4/1"], self.db.business_version("run"))
        self.assertEqual("passed", checkpoint["status"])
        self.assertEqual([], self.db.test_train_status("run")["due_checkpoints"])

    def test_per_spec_gates_are_persisted_and_final_gate_does_not_replace_checkpoint(self):
        ids = [f"S{index}" for index in range(1, 12)]
        self.db.initialize_test_train("run", ids, expected_version=self.db.business_version("run"))
        for level in ("L0", "L1", "L2"):
            self.db.record_test_gate("run", "S1", level, "passed", "candidate-a", [f"check://{level}"], self.db.business_version("run"))
        train = self.db.test_train_status("run")
        rows = [row for row in train["obligations"] if row["spec_id"] == "S1"]
        self.assertEqual({"passed"}, {row["status"] for row in rows})
        self.assertEqual("pending", train["checkpoints"][0]["status"])


if __name__ == "__main__":
    unittest.main()
