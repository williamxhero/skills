# Issue-tree contract

Persist UTF-8 JSON with this hierarchy:

```text
{
  "schema_version": 1,
  "grill": {"frontier_empty": true, "round_evidence": ["..."]},
  "umbrella_spec": {"id": "...", "artifact": "..."},
  "specs": [{
    "id": "...", "artifact": "...", "difficulty": "easy|standard|hard|extreme", "xhigh_evidence": [],
    "route": {
      "recommended": {"model": "<route-codex-task model>", "thinking": "<route-codex-task effort>"},
      "fallbacks": [{"model": "...", "thinking": "..."}],
      "rationale": "model capability evidence; effort complexity evidence"
    },
    "parent_issue": {"parent_id": "<umbrella id>", "evidence": ["GitHub readback..."]},
    "tickets": [{"id": "...", "artifact": "...", "parent_issue": {"parent_id": "<owning SPEC id>", "evidence": ["GitHub readback..."]}}]
  }]
}
```

The umbrella SPEC is not a child SPEC and has no tickets or implementation route. IDs are unique at each level. Every relationship evidence list is non-empty and identifies a GitHub readback. Build each whole-SPEC route through `route-codex-task`; tickets inherit it. Keep `xhigh_evidence` empty unless the recommendation uses `xhigh`, then record concrete evidence showing why `high` is inadequate. `scripts/validate_issue_tree.py` imports the shared route policy and validates the difficulty floor, pair allow-list, fallback ordering, and `xhigh` gate.
