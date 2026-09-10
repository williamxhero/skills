# Issue-tree contract

Persist UTF-8 JSON with this hierarchy:

```text
{
  "schema_version": 1,
  "grill": {"frontier_empty": true, "round_evidence": ["..."]},
  "umbrella_spec": {"id": "...", "artifact": "..."},
  "specs": [{
    "id": "...", "artifact": "...",
    "parent_issue": {"parent_id": "<umbrella id>", "evidence": ["GitHub readback..."]},
    "tickets": [{"id": "...", "artifact": "...", "parent_issue": {"parent_id": "<owning SPEC id>", "evidence": ["GitHub readback..."]}}]
  }]
}
```

The umbrella SPEC is not a child SPEC and has no tickets. IDs are unique at each level. Every evidence list is non-empty and identifies a GitHub relationship readback.
