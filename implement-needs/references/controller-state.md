# Controller-state contract

Read this contract at startup, recovery, and immediately before terminal validation. The [JSON Schema](controller-state.schema.json) is the structural authority; [`validate_controller_terminal.py`](../scripts/validate_controller_terminal.py) is the semantic and terminal authority.

## Artifacts

Keep these files together under `.scratch/<initiative>/`:

- `delivery-map.md`: human-readable decisions and evidence.
- `task-tree.json`: the latest normalized JSON snapshot of every planning, SPEC, and repair task, sorted by task ID, plus the implementation ownership ledger.
- `controller-state.json`: the machine-readable controller record described below.
- `terminal-receipt.json`: the most recent validator decision.

Generate `run_id` once and retain it across resumptions. Increment `state_revision` after each observed or performed controller action. After updating the delivery map and task-tree snapshot, hash their exact bytes with SHA-256 and store both lowercase digests in `freshness`. Write controller state last so a changed source makes the previous state deterministically stale.

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
| `implementation_ownership` | The single SPEC implementation ownership ledger. Each SPEC names exactly one implementation task, that task's route readback, every ticket owned by that task with blockers, commits, tests, and tracker state, empty ticket-level implementation artifact collections, and role-limited helper tasks. |
| `pending_specs` | Ordered SPEC IDs not yet verified merged. |
| `unverified_handoffs` | Exact set of child IDs in `handoff_received`. |
| `unarchived_tasks` | Exact set of child IDs whose lifecycle is not `archived`. |
| `test_state` | Test-train status, exact candidate revision, and evidence pointers. |
| `release_state` | Release status, exact candidate revision, and evidence pointers. |
| `next_action` | The one action executable now; required only while `active`. |
| `resume_action` | Exact continuation after an accepted blocker or user stop; otherwise `null`. |
| `freshness` | SHA-256 of the current delivery map and task-tree snapshot. |
| `terminal` | Terminal reason and evidence, plus machine-checkable blocker/user-stop assertions; otherwise `null`. |

Child lifecycle is one of `queued`, `active`, `paused`, `handoff_received`, `verified`, or `archived`. Move a returned child to `handoff_received`, then `verified` only after independent verification, then `archived` only after archival succeeds.

An action has exactly `kind`, `target`, and `instruction`. `kind` is one of `wait`, `verify`, `archive`, `repair`, `dispatch`, `advance`, `resume`, `refresh_state`, or `repair_state`. Gate-specific failures still persist through this canonical vocabulary: route drift, planning handoff repair, and receipt-write repair use `repair` with the task, gate, or artifact named in `target`. `instruction` is directly executable rather than a status description.

Test status is `pending`, `running`, `passed`, or `blocked`. Release status is `pending`, `building`, `packaged`, `deployed`, `not_applicable`, or `blocked`.

## Implementation ownership

`implementation_ownership.specs` records one entry per dispatched SPEC. `implementation_task_id` must equal the matching `child_tasks` SPEC task ID, and `route.task_id` must be that same task for both the locked recommendation and any approved fallback. A fallback is a route substitution inside the existing SPEC task; it never creates a replacement owner.

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
        "route": {
          "target": "SPEC-02",
          "task_id": "thread-spec-02",
          "selection": "recommended",
          "evidence": ["receipt://SPEC-02-route"]
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
  "test_state": {"status": "running", "candidate_revision": null, "evidence": []},
  "release_state": {"status": "pending", "candidate_revision": null, "evidence": []},
  "next_action": {"kind": "wait", "target": "thread-spec-02", "instruction": "Wait for the current SPEC task snapshot."},
  "resume_action": null,
  "freshness": {
    "delivery_map_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "task_tree_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  },
  "terminal": null
}
```
