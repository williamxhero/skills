import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from control_db import ControlDB
from startup_contract import StartupContractError


def contract(run_id="run"):
    return {
        "schema_version": 1,
        "run_id": run_id,
        "runtime": {"python": "3.12.4", "platform": "windows"},
        "skill_root": "C:/Users/will/.codex/skills",
        "target_repository": {"id": "repo://skills", "path": "C:/Users/will/.codex/skills", "branch": "master"},
        "tracker": {"mode": "github", "repository": "williamxhero/skills"},
        "host": {"id": "local", "cwd": "C:/Users/will/.codex/skills", "capabilities": ["sqlite", "python"]},
        "permissions": {"allowed_actions": ["read", "write-scoped"]},
        "dependencies": [{"canonical_name": "implement-needs", "aliases": ["implement needs"]}],
    }


AVAILABLE = [{
    "canonical_name": "implement-needs",
    "aliases": ["implement needs"],
    "path": "C:/Users/will/.codex/skills/implement-needs",
    "digest": "sha256:abc",
    "version": "1.0.0",
    "adapter": "local-python",
}]


class StartupContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "run.db"
        self.db = ControlDB(self.path)
        self.addCleanup(self.db.close)
        self.db.create_run("run", "initiative", "requirement")

    def test_valid_contract_is_resolved_persisted_and_restartable(self):
        version = self.db.business_version("run")
        result = self.db.record_startup_contract("run", contract(), AVAILABLE, version)
        self.assertTrue(result["changed"])
        self.assertEqual("verified", result["status"])
        saved = self.db.startup_contract("run")
        self.assertEqual("verified", saved["status"])
        self.assertEqual("implement-needs", saved["payload"]["dependencies"][0]["canonical_name"])
        self.assertEqual(["sqlite", "python"], saved["payload"]["host"]["capabilities"])
        self.assertEqual(result["contract_digest"], saved["contract_digest"])
        self.db.close()
        reopened = ControlDB.open_existing(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(saved, reopened.startup_contract("run"))

    def test_same_contract_is_idempotent_and_changed_contract_is_immutable(self):
        version = self.db.business_version("run")
        first = self.db.record_startup_contract("run", contract(), AVAILABLE, version)
        second = self.db.record_startup_contract("run", contract(), AVAILABLE, self.db.business_version("run"))
        self.assertFalse(second["changed"])
        self.assertEqual(first["business_version"], second["business_version"])
        with self.assertRaises(StartupContractError) as raised:
            changed = contract()
            changed["host"] = {"id": "other-host", "capabilities": ["sqlite", "python"]}
            self.db.record_startup_contract("run", changed, AVAILABLE, self.db.business_version("run"))
        self.assertEqual("startup_contract_immutable", raised.exception.code)
        self.assertEqual(2, len(self.db.snapshot("run")["events"]))

    def test_dependency_failures_reject_without_business_side_effect(self):
        before = self.db.snapshot("run")
        with self.assertRaises(StartupContractError) as raised:
            self.db.record_startup_contract("run", contract(), [], before["run"]["business_version"])
        self.assertEqual("blocked_missing_dependency", raised.exception.code)
        after = self.db.snapshot("run")
        self.assertEqual(before["run"]["business_version"], after["run"]["business_version"])
        self.assertEqual(len(before["events"]), len(after["events"]))

    def test_wrong_run_and_ambiguous_dependency_are_rejected(self):
        wrong = contract("other")
        with self.assertRaises(StartupContractError) as raised:
            self.db.record_startup_contract("run", wrong, AVAILABLE, self.db.business_version("run"))
        self.assertEqual("startup_run_mismatch", raised.exception.code)
        ambiguous = AVAILABLE + [{**AVAILABLE[0], "path": "C:/other/implement-needs"}]
        with self.assertRaises(StartupContractError) as raised:
            self.db.record_startup_contract("run", contract(), ambiguous, self.db.business_version("run"))
        self.assertEqual("blocked_ambiguous_dependency", raised.exception.code)

    def test_dependency_digest_path_version_and_adapter_mismatch_is_rejected(self):
        requested = contract()
        requested["dependencies"][0].update({"path": "C:/wrong", "digest": "sha256:wrong", "version": "9.9.9", "adapter": "wrong"})
        with self.assertRaises(StartupContractError) as raised:
            self.db.record_startup_contract("run", requested, AVAILABLE, self.db.business_version("run"))
        self.assertEqual("dependency_mismatch", raised.exception.code)

    def test_cli_startup_check_returns_structured_contract(self):
        controller = Path(__file__).parents[1] / "scripts" / "controller.py"
        payload = json.dumps(contract(), ensure_ascii=False)
        available = json.dumps(AVAILABLE, ensure_ascii=False)
        version = str(self.db.business_version("run"))
        command = [sys.executable, str(controller), "--db", str(self.path), "startup-contract", "--run-id", "run", "--contract", payload, "--available-dependencies", available, "--expected-version", version]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.db.close()
        checked = subprocess.run([sys.executable, str(controller), "--db", str(self.path), "startup-check", "--run-id", "run"], capture_output=True, text=True, check=False)
        self.assertEqual(0, checked.returncode, checked.stderr)
        self.assertEqual("verified", json.loads(checked.stdout)["status"])


if __name__ == "__main__":
    unittest.main()
