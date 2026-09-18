from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("validate_route", ROOT / "scripts" / "validate_route.py")
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


class RoutePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.record_path = self.root / "route.json"
        self.readback_path = self.root / "readback.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def record(self) -> dict:
        return {
            "schema_version": 1,
            "route_id": "route-1",
            "target": "SPEC-1",
            "host_capability_readback": {
                "host_id": "host-1",
                "readback_evidence": ["host://host-1/settings"],
                "supported_routes": [
                    {"model": "gpt-5.6-luna", "thinking": ["light", "medium", "high", "xhigh"]},
                    {"model": "gpt-5.6-terra", "thinking": ["light", "medium", "high", "xhigh"]},
                    {"model": "gpt-5.6-sol", "thinking": ["light", "medium", "high", "xhigh"]},
                ],
            },
            "route": {
                "recommended": {"model": "gpt-5.6-sol", "thinking": "high"},
                "fallbacks": [{"model": "gpt-5.6-sol", "thinking": "xhigh"}],
                "rationale": "High failure cost needs sol; coupled verification needs high.",
            },
            "xhigh_evidence": [],
        }

    def readback(self) -> dict:
        return {
            "schema_version": 1,
            "route_id": "route-1",
            "target": "SPEC-1",
            "task_id": "task-1",
            "requested": {"model": "gpt-5.6-sol", "thinking": "high"},
            "applied": {"model": "gpt-5.6-sol", "thinking": "high"},
            "selection": "recommended",
            "substitution_reason": None,
            "readback_evidence": ["task://task-1/settings"],
        }

    def evaluate(self, record: dict, readback: dict):
        self.record_path.write_text(json.dumps(record), encoding="utf-8")
        self.readback_path.write_text(json.dumps(readback), encoding="utf-8")
        return VALIDATOR.evaluate(
            self.record_path,
            self.readback_path,
            expected_route_id="route-1",
            expected_target="SPEC-1",
            expected_host_id="host-1",
            expected_task_id="task-1",
        )

    @staticmethod
    def codes(payload: dict) -> set[str]:
        return {item["code"] for item in payload.get("reasons", [])}

    def test_exact_route_emits_stable_machine_receipt(self) -> None:
        first = self.evaluate(self.record(), self.readback())
        second = self.evaluate(self.record(), self.readback())
        self.assertEqual(first, second)
        payload, code = first
        self.assertEqual(0, code)
        self.assertEqual("allow", payload["decision"])
        self.assertEqual("host-1", payload["host_id"])
        self.assertEqual("gpt-5.6-sol", payload["applied"]["model"])
        self.assertEqual(64, len(payload["receipt_sha256"]))
        body = dict(payload)
        receipt_hash = body.pop("receipt_sha256")
        self.assertEqual(VALIDATOR.decision_hash(body), receipt_hash)

    def test_cli_persists_the_exact_printed_receipt(self) -> None:
        self.record_path.write_text(json.dumps(self.record()), encoding="utf-8")
        self.readback_path.write_text(json.dumps(self.readback()), encoding="utf-8")
        receipt_path = self.root / "receipt.json"
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = VALIDATOR.main(
                [
                    "--record", str(self.record_path),
                    "--readback", str(self.readback_path),
                    "--expected-route-id", "route-1",
                    "--expected-target", "SPEC-1",
                    "--expected-host-id", "host-1",
                    "--expected-task-id", "task-1",
                    "--receipt", str(receipt_path),
                ]
            )
        self.assertEqual(0, code)
        self.assertEqual(output.getvalue(), receipt_path.read_text(encoding="utf-8"))

    def test_policy_allows_only_three_models_and_four_efforts(self) -> None:
        policy = VALIDATOR.validate_route.__globals__["POLICY"]
        self.assertEqual(["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"], policy["models"])
        self.assertEqual(["light", "medium", "high", "xhigh"], policy["efforts"])

    def test_silent_drift_and_wrong_host_fail_closed(self) -> None:
        readback = self.readback()
        readback["applied"] = {"model": "gpt-5.6-terra", "thinking": "high"}
        record = self.record()
        record["host_capability_readback"]["host_id"] = "other-host"
        payload, code = self.evaluate(record, readback)
        self.assertEqual(1, code)
        self.assertIn("silent_route_drift", self.codes(payload))
        self.assertIn("host_id_mismatch", self.codes(payload))

    def test_fallback_must_be_locked_same_or_stronger(self) -> None:
        record = self.record()
        record["route"]["fallbacks"] = [{"model": "gpt-5.6-terra", "thinking": "xhigh"}]
        payload, code = self.evaluate(record, self.readback())
        self.assertEqual(1, code)
        self.assertIn("fallback_weaker", self.codes(payload))

        readback = self.readback()
        readback.update(
            requested={"model": "gpt-5.6-terra", "thinking": "medium"},
            applied={"model": "gpt-5.6-terra", "thinking": "medium"},
            selection="fallback",
            substitution_reason="Requested recommendation unavailable.",
        )
        payload, code = self.evaluate(self.record(), readback)
        self.assertEqual(1, code)
        self.assertIn("fallback_not_preapproved", self.codes(payload))

    def test_xhigh_recommendation_requires_complexity_evidence(self) -> None:
        record = self.record()
        record["route"]["recommended"]["thinking"] = "xhigh"
        record["route"]["fallbacks"] = [{"model": "gpt-5.6-sol", "thinking": "xhigh"}]
        readback = self.readback()
        readback["requested"]["thinking"] = "xhigh"
        readback["applied"]["thinking"] = "xhigh"
        payload, code = self.evaluate(record, readback)
        self.assertEqual(1, code)
        self.assertIn("xhigh_without_evidence", self.codes(payload))

        record["xhigh_evidence"] = ["Cross-repository compatibility proof has an exceptionally large verification search space; high is inadequate."]
        payload, code = self.evaluate(record, readback)
        self.assertEqual(0, code)

    def test_xhigh_fallback_also_requires_complexity_evidence(self) -> None:
        readback = self.readback()
        fallback = {"model": "gpt-5.6-sol", "thinking": "xhigh"}
        readback.update(
            requested=fallback,
            applied=copy.deepcopy(fallback),
            selection="fallback",
            substitution_reason="Recommendation could not be used.",
        )
        payload, code = self.evaluate(self.record(), readback)
        self.assertEqual(1, code)
        self.assertIn("xhigh_without_evidence", self.codes(payload))

        record = self.record()
        record["xhigh_evidence"] = [
            "A fragile compatibility proof makes high inadequate."
        ]
        payload, code = self.evaluate(record, readback)
        self.assertEqual(0, code)

    def test_non_advertised_pair_is_rejected(self) -> None:
        record = self.record()
        record["host_capability_readback"]["supported_routes"][-1]["thinking"] = ["xhigh"]
        payload, code = self.evaluate(record, self.readback())
        self.assertEqual(1, code)
        self.assertIn("route_not_advertised", self.codes(payload))


if __name__ == "__main__":
    unittest.main()
