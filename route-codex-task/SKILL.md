---
name: route-codex-task
description: Select, create, and verify Codex task model/effort routes with host capability readback, same-or-stronger fallback, silent-drift rejection, and deterministic receipts. Use when another skill or workflow creates a Codex task/thread, predicts an implementation route, validates the task's applied model settings, or needs the shared gpt-5.6 routing policy.
---

# Route Codex Task

Own the complete routing boundary for a Codex task. Keep the caller's task-specific classification inputs and defaults; apply this skill's policy, fallback, readback, and receipt contract unchanged.

## 1. Build the route context

Identify the target host and task outcome. Record separate evidence for:

- model choice: capability difficulty, reliability requirement, and failure cost;
- effort choice: reasoning complexity, ambiguity, coupling, search space, and verification burden.

Read [the routing policy](references/routing-policy.md). Select model and effort independently. Treat `xhigh` as an exceptional slow path: record concrete evidence showing why `high` is inadequate before selecting it.

For a planning task that owns the complete `requirement understanding → SPEC decomposition → per-SPEC implementation-route prediction → ticket decomposition` sequence, use `gpt-5.6-sol` + `high` by default. Upgrade only the effort to `xhigh` when concrete complexity evidence proves `high` inadequate.

## 2. Read target-host capabilities

Immediately before creation, inspect the exact model/effort combinations advertised by the target host. Persist the host ID, non-empty readback evidence, and supported combinations in [the route contract](references/contract.md). A stale catalog, another host's settings, or an inferred combination is not capability readback.

Lock one advertised recommendation and at least one advertised fallback. Every fallback must be no weaker on either policy rank. Use a distinct pair whenever one exists; only the policy ceiling may repeat itself.

## 3. Create at bootstrap

Create exactly one task with the locked recommendation, target host, and task-specific prompt. Keep it bootstrap-only until route verification succeeds. If creation rejects the recommendation, explicitly select a locked same-or-stronger fallback for that same task creation attempt and record the substitution reason.

Never silently accept an arbitrary allow-listed pair or a weaker available pair.

## 4. Read back applied settings

Read the created task by ID from the target host. Persist the explicit request, actual applied model/effort, selection kind, optional substitution reason, and readback evidence. Treat creation arguments or a creation response without fresh task settings as request evidence, not applied-state readback.

Run:

```text
python <route-codex-task>/scripts/validate_route.py --record <route-record.json> --readback <route-readback.json> --expected-route-id <route-id> --expected-target <target> --expected-host-id <host-id> --expected-task-id <task-id> --receipt <route-receipt.json>
```

Continue only on exit code `0` and `decision: allow`. Requested and applied pairs must match exactly. On rejection, keep the task at bootstrap, repair the same route or select a locked fallback explicitly, capture a fresh readback, and rerun the gate.

## 5. Dispatch with the receipt

Persist the complete allow receipt beside the caller's lifecycle state. Bind ownership and later recovery to its route ID, target, host, task ID, selection, applied pair, locked route, source hashes, and `receipt_sha256`.

Send the task assignment only after receipt persistence succeeds. On recovery, revalidate the same receipt and task readback; any identity, route, hash, or applied-setting drift returns to bootstrap repair.

## Completion gate

Finish routing only when host capability readback, an allowed locked route, fresh applied-state readback, and the deterministic allow receipt all agree on the same host, task, and exact model/effort pair.

