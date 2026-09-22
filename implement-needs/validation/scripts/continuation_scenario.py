"""Local continuation gate matrix; these fixtures are never live task evidence."""
from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from continuation_gate import verify_continuation

CASES = {"text_only", "active_only", "capability_probe_only", "counter_only",
         "wrong_thread", "stale_turn", "stale_recovery", "orphan_task", "progress"}


def fixture():
    identity = dict(formal_thread_id="fixture-controller", host_id="fixture-host",
                    run_id="fixture-run", project_id="fixture-project", cwd="fixture-cwd")
    before = dict(identity, turn_id="previous", business_version=461,
                  frontier={"action": "ticket_current_spec", "spec": "S1"}, evidence=["fixture:before"])
    after = dict(identity, turn_id="continuation", business_version=462,
                 frontier={"action": "dispatch_spec", "spec": "S1"}, evidence=["fixture:after"],
                 next_action={"kind": "dispatch_spec"}, recovery_action=None, lost_wakeup=False,
                 handoff_reconciled=True, no_orphans=True)
    turn = dict(identity, turn_id="continuation", status="completed", successful_tool_calls=1,
                execution_evidence=["fixture:tool-result"])
    return dict(before=before, after=after, turn=turn)


def run_continuation_matrix():
    base = fixture()
    receipts = {name: deepcopy(base) for name in CASES}
    receipts["text_only"]["turn"].update(successful_tool_calls=0, execution_evidence=[])
    receipts["active_only"]["turn"]["status"] = "inProgress"
    receipts["capability_probe_only"]["after"].update(business_version=461, frontier=base["before"]["frontier"])
    receipts["counter_only"]["after"]["frontier"] = base["before"]["frontier"]
    receipts["wrong_thread"]["turn"]["formal_thread_id"] = "another-controller"
    receipts["stale_turn"]["turn"]["turn_id"] = "previous"
    receipts["stale_recovery"]["after"]["recovery_action"] = "controller_interrupted"
    receipts["orphan_task"]["after"]["no_orphans"] = False
    cases = {}
    for name, receipt in sorted(receipts.items()):
        expected = "verified" if name == "progress" else "blocked"
        actual = verify_continuation(receipt)
        cases[name] = {"expected": expected, "actual": actual["decision"], "reasons": actual["reasons"]}
    return {"evidence_kind": "local_deterministic_replay", "resources_created": 0, "cases": cases}
