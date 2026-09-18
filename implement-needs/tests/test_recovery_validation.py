import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]/"scripts"))
from control_db import ControlDB
from recovery_validation import FakeBackend, ShadowBackend, backup, shadow_decision, validate_policy

POLICY={"workflow_version":"w1","skill_bundle_digest":"skill-a","backend_protocol_version":"b1","schema_version":"s1","rules_version":"r1","profile_id":"default","profile_digest":"p1","payload":{}}
class RecoveryValidationTests(unittest.TestCase):
 def test_policy_drift_and_fault_adapter(self):
  self.assertEqual("allow",validate_policy(POLICY)["decision"]); self.assertEqual("reject",validate_policy({**POLICY,"profile_id":"issue-444"})["decision"])
  backend=FakeBackend(["archive"]); self.assertRaises(RuntimeError,backend.call,"archive"); self.assertEqual("succeeded",backend.call("archive")["status"])
 def test_shadow_and_online_backup(self):
  with tempfile.TemporaryDirectory() as d:
   db=ControlDB(Path(d)/"run.db"); db.create_run("r","demo","req"); db.pin_policy("r",POLICY)
   result=shadow_decision(db,"r","planning"); self.assertEqual("shadow",result["mode"]); self.assertEqual([],db.conn.execute("SELECT * FROM actions").fetchall())
   self.assertRaises(AssertionError,ShadowBackend().call,"deploy")
   manifest=backup(db,Path(d)/"backup.db","evidence://backup"); self.assertEqual(64,len(manifest["sha256"])); db.close()
if __name__=="__main__": unittest.main()
