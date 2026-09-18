import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
CONTROLLER = ROOT / "scripts" / "controller.py"


def run_controller(db: Path, *args: str) -> dict:
    completed = subprocess.run(
        [sys.executable, str(CONTROLLER), "--db", str(db), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


class RuntimeContractCLITests(unittest.TestCase):
    def test_snapshot_context_advance_and_tool_boundaries_are_public(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "run.db"
            run_controller(db, "init", "--run-id", "run-1", "--initiative", "demo", "--requirement", "req")
            run_controller(db, "add-spec", "--run-id", "run-1", "--spec-id", "S1", "--title", "one", "--position", "1", "--acceptance", '["tested"]')
            advanced = run_controller(db, "advance", "--run-id", "run-1", "--max-actions", "1")
            self.assertEqual("needs_llm", advanced["boundary"])
            context = run_controller(db, "context", "--run-id", "run-1", "--phase", "planning")
            self.assertIn("acceptance", context)
            self.assertIn("state_version", context)
            envelope = run_controller(db, "tool-success", "--result", '{"revision":"r1"}', "--identifiers", '{"spec":"S1"}', "--version", str(context["state_version"]), "--evidence-uri", "evidence://r1")
            self.assertEqual("succeeded", envelope["status"])
            denied = run_controller(db, "host-operation", "--operation", "deploy", "--required-capability", "deploy", "--capabilities", "[]")
            self.assertEqual("capability_required", denied["status"])


if __name__ == "__main__":
    unittest.main()
