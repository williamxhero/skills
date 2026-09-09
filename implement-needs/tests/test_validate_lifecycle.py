from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "scripts" / "validate_lifecycle.py"
S = importlib.util.spec_from_file_location("lifecycle", P)
M = importlib.util.module_from_spec(S)
S.loader.exec_module(M)


class LifecycleTests(unittest.TestCase):
    def run_events(self, events):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "events.json"
            p.write_text(json.dumps({"events": events}), encoding="utf-8")
            return M.evaluate(p)

    def test_complete_controller_owned_sequence(self):
        events = [
            {"kind": "dispatch_spec", "owner": "controller", "task": "s1"},
            {
                "kind": "side_question",
                "owner": "controller",
                "prior_action": "wait:s1",
                "resumed_action": "wait:s1",
            },
            {"kind": "child_final", "owner": "s1", "task": "s1"},
            {"kind": "verify", "owner": "controller", "task": "s1"},
            {"kind": "archive", "owner": "controller", "task": "s1"},
        ]
        events += [{"kind": k, "owner": "controller"} for k in M.RELEASE]
        events += [
            {
                "kind": "controller_final",
                "owner": "controller",
                "state": "terminal_success",
            }
        ]
        payload, code = self.run_events(events)
        self.assertEqual(0, code)
        self.assertEqual("allow", payload["decision"])

    def test_overlap_duplicate_recovery_and_side_question_drift_reject(self):
        events = [
            {"kind": "dispatch_spec", "task": "s1"},
            {"kind": "dispatch_spec", "task": "s2"},
            {"kind": "recover", "created_duplicate": True},
            {
                "kind": "side_question",
                "prior_action": "wait:s1",
                "resumed_action": "dispatch:s2",
            },
        ]
        payload, code = self.run_events(events)
        self.assertEqual(1, code)
        self.assertGreaterEqual(len(payload["reasons"]), 3)

    def test_child_cannot_release_and_controller_cannot_release_early(self):
        events = [
            {"kind": "dispatch_spec", "task": "s1"},
            {"kind": "build", "owner": "s1"},
            {"kind": "smoke", "owner": "controller"},
        ]
        payload, code = self.run_events(events)
        self.assertEqual(1, code)
        self.assertIn("event_1_release_owner", payload["reasons"])
        self.assertIn("release_order", payload["reasons"])

    def test_final_requires_explicit_verify_then_archive(self):
        payload, code = self.run_events(
            [
                {"kind": "dispatch_spec", "task": "s1"},
                {"kind": "child_final", "task": "s1"},
                {"kind": "archive", "task": "s1"},
            ]
        )
        self.assertEqual(1, code)
        self.assertIn("event_2_archive_without_verify", payload["reasons"])

    def test_terminal_requires_full_release(self):
        payload, code = self.run_events(
            [
                {
                    "kind": "controller_final",
                    "owner": "controller",
                    "state": "terminal_success",
                }
            ]
        )
        self.assertEqual(1, code)
        self.assertIn("terminal_before_complete_release", payload["reasons"])

    def test_utf8_bom_event_file_is_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "events.json"
            p.write_text(json.dumps({"events": []}), encoding="utf-8-sig")
            payload, code = M.evaluate(p)
            self.assertEqual(0, code)
            self.assertEqual("allow", payload["decision"])


if __name__ == "__main__":
    unittest.main()
