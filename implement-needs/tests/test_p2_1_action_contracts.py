import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from action_contracts import ACTION_CONTRACTS, ActionContract, ActionContractError, contract_for, validate_contracts


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
        incomplete = ActionContract("bad", "preflight", (), (), "", (), (), "", "", "", "", ())
        with self.assertRaises(ActionContractError) as raised:
            validate_contracts({"bad": incomplete})
        self.assertEqual("action_contract_incomplete", raised.exception.code)
        with self.assertRaises(ActionContractError) as raised:
            validate_contracts({"one": base})
        self.assertEqual("action_contract_invalid", raised.exception.code)


if __name__ == "__main__": unittest.main()
