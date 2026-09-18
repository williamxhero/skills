import sys
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from action_contracts import ACTION_CONTRACTS, ActionContract, ActionContractError, contract_for, render_cli_help, render_derived_checklist, validate_contracts
from control_db import ControlDB


class ActionContractTests(unittest.TestCase):
    def test_registry_is_complete_and_each_contract_has_safety_fields(self):
        self.assertTrue(validate_contracts())
        self.assertGreaterEqual(len(ACTION_CONTRACTS), 20)
        for contract in ACTION_CONTRACTS.values():
            self.assertTrue(contract.authorization)
            self.assertTrue(contract.evidence)
            self.assertTrue(contract.recovery)
            self.assertTrue(contract.idempotency)
            self.assertTrue(contract.references)

    def test_unknown_duplicate_and_incomplete_contracts_fail_closed(self):
        with self.assertRaises(ActionContractError) as raised:
            contract_for("undeclared")
        self.assertEqual("action_contract_unknown", raised.exception.code)
        base = contract_for("run_preflight")
        incomplete = ActionContract("bad", "preflight", (), (), "", (), (), "", "", "", "", (), "")
        with self.assertRaises(ActionContractError) as raised:
            validate_contracts({"bad": incomplete})
        self.assertEqual("action_contract_incomplete", raised.exception.code)
        with self.assertRaises(ActionContractError) as raised:
            validate_contracts({"one": base})
        self.assertEqual("action_contract_invalid", raised.exception.code)

    def test_derived_outputs_are_contract_sourced_and_unknown_cli_action_rejects(self):
        checklist = render_derived_checklist()
        self.assertIn("GENERATED FROM action_contracts.py", checklist)
        self.assertIn("run_preflight", checklist)
        self.assertEqual("action_contracts.py", render_cli_help("run_preflight")["source"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.db"
            db = ControlDB(path)
            db.create_run("run", "initiative", "requirement")
            db.close()
            controller = Path(__file__).parents[1] / "scripts" / "controller.py"
            result = subprocess.run([sys.executable, str(controller), "--db", str(path), "action-contract", "--action", "not-registered"], capture_output=True, text=True, check=False)
            self.assertNotEqual(0, result.returncode)
            self.assertEqual("action_contract_unknown", json.loads(result.stdout)["error"])


if __name__ == "__main__": unittest.main()
