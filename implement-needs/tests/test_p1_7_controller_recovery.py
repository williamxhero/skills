import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from control_db import ControlDB
from controller_recovery import (active_action_invariant, reconcile_controller_interruption,
                                 stale_controller, watchdog)
from route_summary import RouteSummaryError, make_route_summary


class ControllerContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = ControlDB(Path(self.temp.name) / "state.db")
        self.db.create_run("run", "initiative", "requirement")

    def tearDown(self):
        self.db.close(); self.temp.cleanup()

    def test_empty_active_run_gets_one_durable_recovery_action(self):
        self.assertEqual("active_without_action", active_action_invariant(self.db, "run")["state"])
        first = reconcile_controller_interruption(self.db, "run")
        second = reconcile_controller_interruption(self.db, "run")
        self.assertEqual("recovery_required", first["decision"])
        self.assertEqual(first["action"]["action_id"], second["action"]["action_id"])
        row = self.db.conn.execute("SELECT current_action,recovery_action FROM runs WHERE run_id='run'").fetchone()
        self.assertEqual("controller_interrupted:run", row[0])
        self.assertEqual("controller_interrupted", row[1])

    def test_watchdog_does_not_misclassify_fresh_or_terminal_run(self):
        fresh = watchdog(self.db, "run", now_value=datetime.now(timezone.utc), stale_after_seconds=60)
        self.assertEqual("no_action", fresh["decision"])
        self.db.conn.execute("UPDATE runs SET updated_at=? WHERE run_id='run'", ((datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),))
        stale = watchdog(self.db, "run", now_value=datetime.now(timezone.utc), stale_after_seconds=60)
        self.assertEqual("recovery_requested", stale["decision"])
        self.assertFalse(stale_controller(self.db, "run", stale_after_seconds=60)["stale"])

    def test_route_summary_is_planned_not_ticket_overridable(self):
        self.db.add_spec("run", "S1", "one", 1)
        summary = make_route_summary(
            spec_id="S1", planning_version="plan-1", planned_model="gpt-5.6-sol",
            planned_effort="high", selection="complexity", strategy_version="policy-1",
            factors={"difficulty": "high", "risk": "medium", "coupling": "high", "ambiguity": "medium", "verification_burden": "high"},
            approved_fallbacks=[{"model": "gpt-5.6-terra", "effort": "xhigh"}], evidence=["route://policy-1"],
        )
        self.db.set_route_summary("S1", summary)
        self.assertEqual("verified", self.db.route_summary("S1")["status"])
        bad = dict(summary); bad["ticket_override"] = "gpt-5.6-luna"
        with self.assertRaises(RouteSummaryError):
            self.db.set_route_summary("S1", bad)


if __name__ == "__main__":
    unittest.main()
