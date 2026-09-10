# Route contract

Persist UTF-8 JSON. Before task creation, write:

```json
{
  "schema_version": 1,
  "route_id": "route-001",
  "target": "SPEC-01",
  "host_capability_readback": {
    "host_id": "host-001",
    "readback_evidence": ["thread-host://settings/readback"],
    "supported_routes": [
      {"model": "gpt-5.6-sol", "thinking": ["high", "xhigh"]}
    ]
  },
  "route": {
    "recommended": {"model": "gpt-5.6-sol", "thinking": "high"},
    "fallbacks": [{"model": "gpt-5.6-sol", "thinking": "xhigh"}],
    "rationale": "Model evidence; independent effort evidence."
  },
  "xhigh_evidence": []
}
```

After creation, write a fresh settings readback:

```json
{
  "schema_version": 1,
  "route_id": "route-001",
  "target": "SPEC-01",
  "task_id": "task-001",
  "requested": {"model": "gpt-5.6-sol", "thinking": "high"},
  "applied": {"model": "gpt-5.6-sol", "thinking": "high"},
  "selection": "recommended",
  "substitution_reason": null,
  "readback_evidence": ["thread://task-001/settings"]
}
```

Use `selection: fallback` only with an exact pair listed in `route.fallbacks` and a non-empty substitution reason. Record non-empty `xhigh_evidence` when the selected recommendation is `xhigh`; the evidence must state concrete complexity showing why `high` is inadequate. The validator rejects unsupported pairs, non-advertised pairs, weaker or arbitrary fallbacks, cross-host/task identity mismatches, silent applied-setting drift, malformed evidence, and non-deterministic route state.

An allow receipt is canonical compact JSON. Its `receipt_sha256` hashes the receipt body before that field is added; its source hashes bind the exact route record and post-create readback bytes.

