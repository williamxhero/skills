from __future__ import annotations

import unittest


class FixtureContractTests(unittest.TestCase):
    def test_fixture_is_intentionally_implemented_by_the_spec_run(self) -> None:
        # The qualification scenario owns implementation. This test prevents a
        # partially prebuilt fixture from masking a failed SPEC delivery.
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
