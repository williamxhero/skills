"""Policy validation, fake fault adapters, shadow execution and online backup."""
from __future__ import annotations
import hashlib, json, sqlite3
from pathlib import Path
from control_db import ControlDB, _canonical_json, now

def validate_policy(policy):
    required = ("workflow_version","skill_bundle_digest","backend_protocol_version","schema_version","rules_version","profile_id","profile_digest")
    if not isinstance(policy, dict) or any(not isinstance(policy.get(k), str) or not policy[k].strip() for k in required):
        return {"decision":"reject","reason":"policy_incomplete"}
    if policy["profile_id"] != "default" and policy.get("payload", {}).get("issue_specific") is not True:
        return {"decision":"reject","reason":"profile_scope_unvalidated"}
    return {"decision":"allow","policy_digest":hashlib.sha256(_canonical_json(policy,"policy").encode()).hexdigest()}

class FakeBackend:
    def __init__(self, failures=()): self.failures=list(failures); self.calls=[]
    def call(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        if self.failures and self.failures.pop(0) == operation: raise RuntimeError(f"injected:{operation}")
        return {"operation":operation,"status":"succeeded","request_id":f"fake:{len(self.calls)}"}

class ShadowBackend:
    def __init__(self): self.attempted=[]
    def call(self, operation, **kwargs):
        self.attempted.append((operation, kwargs)); raise AssertionError("shadow mutation attempted")

def shadow_decision(db, run_id, phase):
    from next_action import next_action
    policy=db.conn.execute("SELECT rules_version FROM run_policies WHERE run_id=?",(run_id,)).fetchone()
    action=next_action(db,run_id)
    return {"mode":"shadow","phase":phase,"rules_version":policy[0] if policy else "unknown","action":action,"reason":"shadow_no_mutation"}

def backup(db, destination: Path, evidence_uri: str):
    destination=Path(destination); destination.parent.mkdir(parents=True,exist_ok=True)
    target=sqlite3.connect(destination); db.conn.backup(target); target.close()
    digest=hashlib.sha256(destination.read_bytes()).hexdigest()
    manifest={"database":str(destination),"sha256":digest,"evidence_uri":evidence_uri,"observed_at":now()}
    manifest_path=destination.with_suffix(destination.suffix+".manifest.json"); manifest_path.write_text(json.dumps(manifest,sort_keys=True)+"\n",encoding="utf-8")
    return manifest
