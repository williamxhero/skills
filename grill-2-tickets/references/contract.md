# Issue-tree contract

Persist UTF-8 JSON with this hierarchy:

```text
{
  "schema_version": 1,
  "grill": {"frontier_empty": true, "round_evidence": [{"round": 1, "questions": [{"number": 1, "question": "...", "recommendation": "...", "rationale": "..."}], "acceptance_command": "全部采用推荐选项/答案", "acceptance_evidence": ["..."], "resume_evidence": ["..."], "frontier_empty": true, "frontier_empty_evidence": ["..."]}]},
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

Operationally, each record belongs to one fresh planning run. Keep the run id, source
fingerprint, baseline issue census, and canonical/duplicate issue decisions in the
task's surrounding evidence directory, even though those fields are not part of the
minimal JSON shape above. A record from another run, a record whose ids are absent from
the current GitHub census, or a record that was not updated after the latest external
operation must be rejected and rebuilt before handoff. Persist one item and one
Parent readback at a time so retries are idempotent and partial publication is
recoverable.
