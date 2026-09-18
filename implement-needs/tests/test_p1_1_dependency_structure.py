import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from dependency_readiness import assess_spec_readiness, validate_spec_graph


def spec(spec_id, position, blocked_by=None, status="planned", run_id="run"):
    return {"spec_id": spec_id, "position": position, "blocked_by": blocked_by or [], "status": status, "run_id": run_id}


class DependencyStructureTests(unittest.TestCase):
    def test_known_open_predecessor_is_valid_structure(self):
        result = validate_spec_graph([spec("S1", 1), spec("S2", 2, ["S1"])], run_id="run")
        self.assertEqual("valid", result["status"])

    def test_unknown_cycle_cross_run_and_order_are_structural_errors(self):
        unknown = validate_spec_graph([spec("S1", 1, ["MISSING"])], run_id="run")
        self.assertIn("unknown_dependency", {item["code"] for item in unknown["errors"]})
        cycle = validate_spec_graph([spec("S1", 1, ["S2"]), spec("S2", 2, ["S1"])], run_id="run")
        self.assertIn("dependency_cycle", {item["code"] for item in cycle["errors"]})
        cross_run = validate_spec_graph([spec("S1", 1), spec("S2", 2, ["S1"], run_id="other")], run_id="run")
        self.assertIn("cross_run_dependency", {item["code"] for item in cross_run["errors"]})
        order = validate_spec_graph([spec("S1", 1), spec("S2", 2, ["S1"]), spec("S3", 3, ["S3"])], run_id="run")
        self.assertIn("fixed_order_violation", {item["code"] for item in order["errors"]})

    def test_readiness_separates_waiting_blocked_and_ready(self):
        rows = [spec("S1", 1, status="implementing"), spec("S2", 2, ["S1"])]
        waiting = assess_spec_readiness(rows, "S2", run_id="run")
        self.assertEqual("waiting", waiting["status"])
        self.assertEqual("S1", waiting["next_check"])
        rows[0]["status"] = "cancelled"
        blocked = assess_spec_readiness(rows, "S2", run_id="run")
        self.assertEqual("blocked", blocked["status"])
        self.assertEqual("cancelled_predecessor", blocked["blockers"][0]["reason"])
        ready = assess_spec_readiness(rows, "S2", run_id="run", waived_dependencies=["S1"])
        self.assertEqual("ready", ready["status"])


if __name__ == "__main__":
    unittest.main()
