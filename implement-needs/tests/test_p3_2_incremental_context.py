import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from context_budget import BENCHMARK_CASES, benchmark_delta_context, benchmark_delta_suite
from context_delta import ContextDeltaError, PHASE_COLLECTIONS, build_delta_context
from context_projection import read_history
from control_db import ControlDB
from phase_context import assemble_context


class IncrementalContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "run.db"
        self.db = ControlDB(self.path)
        self.db.create_run("run", "initiative", "requirement")
        self.db.add_spec("run", "S1", "First", 1, acceptance=["acceptance://S1"])
        assemble_context(self.db, "run", "planning")
        self.snapshot = self.db.read_snapshot("run")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_unchanged_collections_are_digest_summarized_and_reachable(self):
        before = self.db.business_version("run")
        delta = build_delta_context(self.db, "run", "planning", self.snapshot["event_cursor"], self.snapshot["state_version"])
        self.assertEqual("delta-v1", delta["context_version"])
        self.assertEqual(["acceptance", "decisions", "direct_dependencies"], sorted(PHASE_COLLECTIONS["planning"]))
        self.assertEqual({}, delta["collections"])
        self.assertEqual("snapshot", read_history(self.db, delta["base"]["pointer"])["entity_type"])
        self.assertEqual(before, self.db.business_version("run"))
        self.assertEqual(["acceptance://S1"], read_history(self.db, delta["base"]["pointer"])["record"]["acceptance"])

    def test_changed_events_are_returned_as_delta_and_pointer(self):
        self.db.update_spec("S1", "ready")
        delta = build_delta_context(self.db, "run", "recovery", self.snapshot["event_cursor"])
        self.assertEqual("changed", delta["collections"]["events"]["kind"])
        self.assertEqual(["spec_state_changed"], [item["event_type"] for item in delta["collections"]["events"]["value"]])
        self.assertEqual([], delta["collections"]["events"].get("pointer", []))

    def test_base_cursor_and_digest_mismatch_fail_closed(self):
        with self.assertRaises(ContextDeltaError) as cursor:
            build_delta_context(self.db, "run", "planning", self.snapshot["event_cursor"] - 1)
        self.assertEqual("delta_base_unavailable", cursor.exception.code)
        with self.assertRaises(ContextDeltaError) as digest:
            build_delta_context(self.db, "run", "planning", self.snapshot["event_cursor"], base_digests={"acceptance": "0" * 64})
        self.assertEqual("delta_base_digest_mismatch", digest.exception.code)

    def test_exception_pointer_is_current_and_no_state_write_occurs(self):
        before = self.db.business_version("run")
        self.db.record_exception("run", "fp-1", "network", "unavailable", "log://1")
        delta = build_delta_context(self.db, "run", "recovery", self.snapshot["event_cursor"])
        exceptions = delta["collections"]["unresolved_exceptions"]
        self.assertEqual("changed", exceptions["kind"])
        self.assertEqual("fp-1", exceptions["value"][0]["fingerprint"])
        self.assertGreater(self.db.business_version("run"), before)

    def test_delta_cli_is_public_and_rejects_missing_base(self):
        base_cursor = self.snapshot["event_cursor"]
        controller = Path(__file__).parents[1] / "scripts" / "controller.py"
        self.db.close()
        good = subprocess.run(
            [sys.executable, str(controller), "--db", str(self.path), "context", "--run-id", "run", "--phase", "planning", "--delta", "--base-event-cursor", str(base_cursor)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, good.returncode, good.stderr)
        self.assertEqual("delta", json.loads(good.stdout)["mode"])
        bad = subprocess.run(
            [sys.executable, str(controller), "--db", str(self.path), "context", "--run-id", "run", "--phase", "planning", "--delta", "--base-event-cursor", "0"],
            capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(0, bad.returncode)
        self.assertEqual("delta_base_unavailable", json.loads(bad.stdout)["error"])
        self.db = ControlDB(self.path)

    def test_fixed_delta_benchmark_is_safe_and_improves_nonempty_fixtures(self):
        fixture_runs = {}
        for index, case in enumerate(BENCHMARK_CASES):
            run_id = f"fixture-{index}"
            fixture_runs[case] = run_id
            self.db.create_run(run_id, "initiative", case)
            if case != "empty-run":
                self.db.add_spec(run_id, f"S-fixed-{index}", case, 1, acceptance=["acceptance://" + (case * 40)])
            if case == "recovery-exception":
                self.db.record_exception(run_id, "fp-1", "network", "unavailable", "log://1")
            assemble_context(self.db, run_id, "planning")
        report = benchmark_delta_suite(self.db, fixture_runs)
        self.assertEqual("allow", report["decision"])
        self.assertEqual(list(BENCHMARK_CASES), list(report["cases"]))
        self.assertTrue(all(item["safety"]["pointer_reachability"] for item in report["cases"].values()))
        self.assertTrue(any(item["savings"]["payload_bytes"] > 0 for item in report["cases"].values()))


if __name__ == "__main__":
    unittest.main()
