"""Black-box lifecycle scenarios reproducing the premature-final audit failure."""
from __future__ import annotations
import json, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LIFECYCLE=ROOT/"scripts"/"validate_lifecycle.py"

class FixtureController:
    def __init__(self): self.events=[]; self.next_action="dispatch:planning"; self.finals=[]; self.created=[]
    def dispatch(self,task): self.created.append(task); self.events.append({"kind":"dispatch_spec","owner":"controller","task":task}); self.next_action=f"wait:{task}"
    def running(self,task): self.events.append({"kind":"commentary","owner":"controller","language":"zh"}); self.events.append({"kind":"wait","owner":"controller","task":task}); self.next_action=f"wait:{task}"
    def side_question(self): self.events.append({"kind":"side_question","owner":"controller","prior_action":self.next_action,"resumed_action":self.next_action})
    def handoff(self,task): self.events += [{"kind":"child_final","owner":task,"task":task},{"kind":"verify","owner":"controller","task":task},{"kind":"archive","owner":"controller","task":task}]; self.next_action="advance"
    def recover(self,task): self.events.append({"kind":"recover","owner":"controller","task":task,"created_duplicate":False}); self.next_action=f"wait:{task}"
    def release(self): self.events += [{"kind":k,"owner":"controller"} for k in ("freeze","build","package","deploy","smoke")]; self.next_action=None; self.finals.append("terminal_success")

class BehavioralAcceptance(unittest.TestCase):
    def validate(self,events):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"events.json"; p.write_text(json.dumps({"events":events}),encoding="utf-8")
            return subprocess.run([sys.executable,str(LIFECYCLE),"--events",str(p)],capture_output=True,text=True,encoding="utf-8")
    def test_running_child_emits_chinese_heartbeat_then_wait_and_no_final(self):
        c=FixtureController(); c.dispatch("spec-1"); c.running("spec-1"); self.assertEqual("zh",c.events[-2]["language"]); self.assertEqual("wait",c.events[-1]["kind"]); self.assertEqual([],c.finals)
    def test_side_question_resumes_without_continue_message(self):
        c=FixtureController(); c.dispatch("spec-1"); before=c.next_action; c.side_question(); self.assertEqual(before,c.next_action); self.assertEqual(before,c.events[-1]["resumed_action"])
    def test_child_final_is_verified_and_archived_not_forwarded(self):
        c=FixtureController(); c.dispatch("spec-1"); c.handoff("spec-1"); self.assertEqual(["child_final","verify","archive"],[e["kind"] for e in c.events[-3:]]); self.assertEqual([],c.finals)
    def test_recovery_reconnects_without_duplicate_dispatch(self):
        c=FixtureController(); c.created=["spec-1"]; c.recover("spec-1"); self.assertEqual(["spec-1"],c.created); self.assertEqual("wait:spec-1",c.next_action)
    def test_implementation_cannot_own_release(self):
        result=self.validate([{"kind":"build","owner":"spec-1"}]); self.assertNotEqual(0,result.returncode); self.assertIn("release_owner",result.stdout)
    def test_complete_trace_has_one_final_after_controller_release(self):
        c=FixtureController(); c.dispatch("spec-1"); c.running("spec-1"); c.handoff("spec-1"); c.release(); result=self.validate(c.events); self.assertEqual(0,result.returncode,result.stdout); self.assertEqual(["terminal_success"],c.finals)
    def test_audit_failure_trace_is_rejected(self):
        trace=[{"kind":"dispatch_spec","owner":"controller","task":"spec-1"},{"kind":"build","owner":"spec-1"},{"kind":"smoke","owner":"controller"}]
        result=self.validate(trace); self.assertNotEqual(0,result.returncode); payload=json.loads(result.stdout); self.assertIn("event_1_release_owner",payload["reasons"]); self.assertIn("event_1_release_with_active_spec",payload["reasons"])

if __name__=="__main__": unittest.main()
