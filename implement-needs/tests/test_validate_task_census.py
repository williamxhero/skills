import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "validate_task_census.py"


class TaskCensusTests(unittest.TestCase):
    def run_gate(self, tasks, tree_tasks=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            census = root / "census.json"
            tree = root / "tree.json"
            receipt = root / "receipt.json"
            state.write_text(json.dumps({"run_id": "run-1", "child_tasks": [
                {"id": "task-1", "kind": "spec", "spec_id": "S1", "lifecycle": "archived"}
            ]}), encoding="utf-8")
            census.write_text(json.dumps({"run_id": "run-1", "tasks": tasks}), encoding="utf-8")
            tree.write_text(json.dumps({"run_id": "run-1", "tasks": tree_tasks if tree_tasks is not None else [{"id": "task-1", "lifecycle": "archived"}]}), encoding="utf-8")
            result = subprocess.run([sys.executable, str(SCRIPT), "--state", str(state),
                "--task-tree", str(tree), "--census", str(census), "--expected-run-id", "run-1", "--receipt", str(receipt)],
                capture_output=True, text=True)
            return result.returncode, json.loads(receipt.read_text(encoding="utf-8"))

    def test_allows_exact_archived_inventory_with_readback(self):
        code, payload = self.run_gate([{"id": "task-1", "archived": True, "lifecycle": "archived",
            "archive_operation_evidence": ["set_thread_archived:ok"],
            "archive_readback_evidence": ["archived:true"]}])
        self.assertEqual(0, code)
        self.assertEqual("allow", payload["decision"])

    def test_rejects_idle_or_omitted_task(self):
        code, payload = self.run_gate([{"id": "task-1", "archived": False, "lifecycle": "idle",
            "archive_operation_evidence": [], "archive_readback_evidence": []},
            {"id": "orphan", "archived": True, "lifecycle": "archived", "archive_operation_evidence": ["ok"],
             "archive_readback_evidence": ["ok"]}])
        self.assertNotEqual(0, code)
        self.assertEqual("reject", payload["decision"])

    def test_rejects_state_claim_when_task_tree_omits_children(self):
        code, payload = self.run_gate([{"id": "task-1", "archived": True, "lifecycle": "archived",
            "archive_operation_evidence": ["ok"], "archive_readback_evidence": ["ok"]}],
            tree_tasks=[])
        self.assertNotEqual(0, code)
        self.assertIn("task-tree IDs", " ".join(payload["errors"]))


if __name__ == "__main__":
    unittest.main()
