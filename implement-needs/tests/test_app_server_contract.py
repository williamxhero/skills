import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class AppServerContractTests(unittest.TestCase):
    def test_protocol_reference_requires_runtime_confirmation(self):
        text = (ROOT / "references" / "app-server.md").read_text(encoding="utf-8")
        self.assertIn("initialize", text)
        self.assertIn("actual successful response", text)
        self.assertIn("fails\nclosed", text)

    def test_probe_has_machine_readable_fields(self):
        text = (ROOT / "scripts" / "probe_codex_app_server.ps1").read_text(encoding="utf-8")
        for field in ("transport", "initialize", "thread_start", "model_readback", "reasoning_readback", "turn_lifecycle", "decision"):
            self.assertIn(field, text)

    def test_backend_selector_fails_closed_without_allow_probe(self):
        script = ROOT / "scripts" / "select_task_backend.py"
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.json"
            result = subprocess.run(["python", str(script), "--receipt", str(receipt)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual("unblock-development", json.loads(receipt.read_text(encoding="utf-8"))["backend"])


if __name__ == "__main__":
    unittest.main()
