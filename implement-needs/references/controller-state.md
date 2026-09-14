# Controller-state contract (legacy export)

The SQLite database at `.scratch/<initiative>/implement-needs.db` is now the runtime
source of truth. The JSON state described below is retained only as a compatibility
export for older validators and audit readers; it must never drive execution.

Read this contract at startup, recovery, and immediately before terminal validation. The [JSON Schema](controller-state.schema.json) is the structural authority; [`validate_controller_terminal.py`](../scripts/validate_controller_terminal.py) is the semantic and terminal authority.

## Artifacts

Keep these files together under `.scratch/<initiative>/`:

- `delivery-map.md`: human-readable decisions and evidence.
- `task-tree.json`: the latest normalized JSON snapshot of every planning, SPEC, and repair task, sorted by task ID, plus the implementation ownership ledger.
- `thread-registry.json`: the complete run-owned thread inventory, latest lifecycle and observation, next action, and archive operation/readback evidence. Reconcile it before every new thread creation.
- `controller-state.json`: the machine-readable controller record described below.
- `terminal-receipt.json`: the most recent validator decision.

Generate `run_id` once and retain it across resumptions. Increment `state_revision` after each observed or performed controller action. After updating the delivery map and task-tree snapshot, hash their exact bytes with SHA-256 and store both lowercase digests in `freshness`. Write controller state last so a changed source makes the previous state deterministically stale.

Immediately validate every active-state write with `scripts/validate_controller_active.py` and retain its receipt. Execute the returned `next_action` only after `decision: continue`. This catches malformed or stale state during supervision rather than deferring detection until terminal completion.

The lifecycle log begins with `planning_archived.data.planning_record_sha256`. Every `spec_dispatched` event carries the complete `route_receipt` emitted by the SPEC route gate, and every recovery `child_reconnected` event repeats that exact receipt for a SPEC child (`null` for non-SPEC children). Lifecycle replay validates the receipt's self-hash, planning-record identity, target, owner task, selection, applied pair, locked recommendation, and fallback rank before dispatch or recovery can continue.

Normalize the task snapshot to exactly `run_id`, `tasks`, and `implementation_ownership`. Each `tasks` entry uses the same `id`, `kind`, `spec_id`, and `lifecycle` fields as `child_tasks`:

```json
{"run_id":"payments-v2-20260909","tasks":[{"id":"thread-plan","kind":"planning","spec_id":null,"lifecycle":"archived"}],"implementation_ownership":{"specs":[],"ticket_implementation_artifacts":{"tasks":[],"threads":[],"worktrees":[],"branches":[],"pull_requests":[]},"role_limited_tasks":[]}}
```

Retain archived tasks. The validator requires the task-tree IDs and fields to match `child_tasks` exactly, so a child omitted from state or reported with a different lifecycle fails closed even when the source hash is current.

## Required state

| Field | Meaning |
| --- | --- |
| `schema_version` | Contract version; currently `1`. |
| `run_id` | Stable identity for this controller run. |
| `state_revision` | Positive, monotonically increasing persisted revision. |
| `controller_state` | `active`, `terminal_success`, `terminal_blocked`, or `user_stopped`. |
| `active_phase` | `bootstrap`, `planning`, `implementation`, `testing`, `release`, `blocked`, `stopped`, or `complete`. |
| `active_task_stack` | Ordered IDs on the current control path. Every queued, active, or paused child appears here. |
| `child_tasks` | Every planning, SPEC, and repair child with kind, optional SPEC ID, and lifecycle. Never delete a completed child; retain it as `archived`. |
| `implementation_ownership` | The single SPEC implementation ownership ledger. Each SPEC names exactly one implementation task, that task's complete validated route receipt, every ticket owned by that task with blockers, commits, tests, and tracker state, empty ticket-level implementation artifact collections, and role-limited helper tasks. |
| `pending_specs` | Ordered SPEC IDs not yet verified merged. |
| `unverified_handoffs` | Exact set of child IDs in `handoff_received`. |
| `unarchived_tasks` | Exact set of child IDs whose lifecycle is not `archived`. |
| `test_state` | Test-train status, exact candidate revision set, L4 checkpoint state, and evidence pointers. |
| `release_state` | Release status, exact candidate revision set, and evidence pointers. |
| `repository_sync` | Final `commit-n-push` status plus per-repository upstream and equal local/remote HEAD evidence. |
| `next_action` | The one action executable now; required only while `active`. |
| `resume_action` | Exact continuation after an accepted blocker or user stop; otherwise `null`. |
| `freshness` | SHA-256 of the current delivery map and task-tree snapshot. |
| `terminal` | Terminal reason and evidence, plus machine-checkable blocker/user-stop assertions; otherwise `null`. |

Child lifecycle is one of `queued`, `active`, `paused`, `handoff_received`, `verified`, or `archived`. Move a returned child to `handoff_received`, then `verified` only after independent verification, then `archived` only after archival succeeds.

For every child task, retain the task-tool archive result and fresh readback in the
sibling `task-census.json`; do not add undeclared fields to `child_tasks`. A child being
`completed`, `idle`, or `notLoaded` is not an archived lifecycle. Immediately before
terminal validation, validate a host task census whose discovered controller-owned task
IDs exactly equal the recorded IDs; an omitted orphan is a state-repair failure.
Run the census against both `controller-state.json` and `task-tree.json`. The task tree
is an independent persisted projection and must contain the same child IDs and archived
lifecycle. A controller cannot make an unarchived host task disappear by editing either
local file. The final census must include the fresh host enumeration source, the archive
operation result, and the post-archive host readback for every child.
Every census entry must contain `lifecycle: "archived"`; omission is invalid. The
controller writes that value only after the host readback proves archival. The terminal
receipt must reference the resulting census allow receipt and final host enumeration.

The lifecycle transition is a required sequence, not a reporting convention:
`active/paused -> handoff_received -> verified -> archived`. Never jump from
`active` to `archived`, and never let `handoff_received` coexist with a controller
final or next-SPEC dispatch. A returned child with no complete handoff remains
`active`/`paused`; an idle snapshot alone does not authorize either verification or
archival.

An action has exactly `kind`, `target`, and `instruction`. `kind` is one of `wait`, `verify`, `archive`, `repair`, `dispatch`, `advance`, `resume`, `refresh_state`, or `repair_state`. Gate-specific failures still persist through this canonical vocabulary: route drift, planning handoff repair, and receipt-write repair use `repair` with the task, gate, or artifact named in `target`. `instruction` is directly executable rather than a status description.

Test status is `pending`, `running`, `passed`, or `blocked`. `test_state.l4_checkpoints` records `checkpoint_size: 10`, ordered SPECs, completed count, deterministic checkpoint objects, pass/fail evidence, and repository candidate revision sets. `candidate_revisions` is a unique collection whose order has no meaning; final L4 reuse or rerun evidence is compared against that set. Release status is `pending`, `building`, `packaged`, `deployed`, `not_applicable`, or `blocked`.

## Implementation ownership

`implementation_ownership.specs` records one entry per dispatched SPEC. `implementation_task_id` must equal the matching `child_tasks` SPEC task ID, and `route.task_id` plus `route.receipt.task_id` must be that same task for both the locked recommendation and any approved fallback. The complete self-hashed route receipt retains the planning-record identity, readback identity, exact locked recommendation and fallbacks, selection, and applied pair. The terminal validator rejects an arbitrary allow-listed pair, a weaker or unlocked fallback, a changed receipt, or free-form evidence in place of the receipt. A fallback is a route substitution inside the existing SPEC task; it never creates a replacement owner.

Each ticket entry records the planned blocking edge, `owner_task_id`, retained commit evidence, retained test evidence, and tracker state. The owner is always the SPEC implementation task. Terminal success requires every ticket to be `closed` with non-empty commit and test evidence.

`ticket_implementation_artifacts.tasks`, `threads`, `worktrees`, `branches`, and `pull_requests` must remain empty. Any ticket-level implementation artifact is former ticket-worker topology and fails closed. Blocker repair, read-only exploration, and read-only review may appear only under `role_limited_tasks`; those entries cannot own tickets, write product code, or provide merge commits.

## State transitions

- `active` has a non-terminal phase, one `next_action`, no `resume_action`, and no terminal record.
- `terminal_success` has phase `complete`, no outstanding task/SPEC/handoff/archive work, a passed test train, a deployed or explicitly inapplicable release for the same candidate, evidence for both, no next/resume action, and success evidence.
- `terminal_blocked` has phase `blocked`, no queued or active child, no executable next action, one `resume_action`, blocker evidence, and `stopping_rule_met: true`.
- `user_stopped` has phase `stopped`, no queued or active child, no executable next action, one `resume_action`, evidence of the explicit stop, and `user_stop_recorded: true`.

Any malformed field, inconsistent aggregate, task-tree mismatch, source-hash mismatch, run-ID mismatch, or failed terminal condition rejects the proposed final. A valid active state returns its recorded `next_action`; stale state returns `refresh_state`; malformed or contradictory state returns `repair_state`. The validator never infers completion.

## Active-state example

```json
{
  "schema_version": 1,
  "run_id": "payments-v2-20260909",
  "state_revision": 12,
  "controller_state": "active",
  "active_phase": "implementation",
  "active_task_stack": ["thread-spec-02"],
  "child_tasks": [
    {"id": "thread-plan", "kind": "planning", "spec_id": null, "lifecycle": "archived"},
    {"id": "thread-spec-02", "kind": "spec", "spec_id": "SPEC-02", "lifecycle": "active"}
  ],
  "implementation_ownership": {
    "specs": [
      {
        "spec_id": "SPEC-02",
        "implementation_task_id": "thread-spec-02",
        "owners": ["payments"],
        "repositories": ["payments-api"],
        "route": {
          "target": "SPEC-02",
          "task_id": "thread-spec-02",
          "selection": "recommended",
          "model": "gpt-5.6-sol",
          "thinking": "high",
          "receipt": {
            "schema_version": 1,
            "decision": "allow",
            "gate": "task_route",
            "run_id": "payments-v2-20260909",
            "target": "SPEC-02",
            "task_id": "thread-spec-02",
            "selection": "recommended",
            "applied": {"model": "gpt-5.6-sol", "thinking": "high"},
            "locked_route": {
              "recommended": {"model": "gpt-5.6-sol", "thinking": "high"},
              "fallbacks": [{"model": "gpt-5.6-sol", "thinking": "xhigh"}]
            },
            "planning_record_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "route_readback_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "receipt_sha256": "1d547b758ee115a1c52c3556fea6c5549175a80f9abebbddd62fd4e0a0a89e2b"
          }
        },
        "tickets": [
          {
            "id": "T-02-01",
            "owner_task_id": "thread-spec-02",
            "blocked_by": [],
            "commits": [],
            "test_evidence": [],
            "tracker_state": "active"
          }
        ]
      }
    ],
    "ticket_implementation_artifacts": {
      "tasks": [],
      "threads": [],
      "worktrees": [],
      "branches": [],
      "pull_requests": []
    },
    "role_limited_tasks": []
  },
  "pending_specs": ["SPEC-02", "SPEC-03"],
  "unverified_handoffs": [],
  "unarchived_tasks": ["thread-spec-02"],
  "test_state": {
    "status": "running",
    "candidate_revision": null,
    "evidence": [],
    "l4_checkpoints": {
      "checkpoint_size": 10,
      "ordered_specs": ["SPEC-02", "SPEC-03"],
      "completed_spec_count": 0,
      "checkpoints": [
        {
          "id": "checkpoint-2",
          "start_spec_index": 1,
          "end_spec_index": 2,
          "specs": ["SPEC-02", "SPEC-03"],
          "final_tail": true,
          "affected_owners": ["payments"],
          "affected_repositories": ["payments-api"],
          "status": "pending",
          "revision": null,
          "candidate_revisions": [],
          "evidence": []
        }
      ],
      "release_l4": null
    }
  },
  "release_state": {"status": "pending", "candidate_revision": null, "evidence": []},
  "repository_sync": {"status": "pending", "repositories": [], "evidence": []},
  "next_action": {"kind": "wait", "target": "thread-spec-02", "instruction": "Wait for the current SPEC task snapshot."},
  "resume_action": null,
  "freshness": {
    "delivery_map_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "task_tree_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  },
  "terminal": null
}
```
