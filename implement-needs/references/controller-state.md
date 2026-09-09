# Controller-state contract

Read this contract at startup, recovery, and immediately before terminal validation. The [JSON Schema](controller-state.schema.json) is the structural authority; [`validate_controller_terminal.py`](../scripts/validate_controller_terminal.py) is the semantic and terminal authority.

## Artifacts

Keep these files together under `.scratch/<initiative>/`:

- `delivery-map.md`: human-readable decisions and evidence.
- `task-tree.json`: the latest normalized JSON snapshot of every planning, SPEC, and repair task, sorted by task ID.
- `controller-state.json`: the machine-readable controller record described below.
- `terminal-receipt.json`: the most recent validator decision.

Generate `run_id` once and retain it across resumptions. Increment `state_revision` after each observed or performed controller action. After updating the delivery map and task-tree snapshot, hash their exact bytes with SHA-256 and store both lowercase digests in `freshness`. Write controller state last so a changed source makes the previous state deterministically stale.

Normalize the task snapshot to exactly `run_id` and `tasks`. Each `tasks` entry uses the same `id`, `kind`, `spec_id`, and `lifecycle` fields as `child_tasks`:

```json
{"run_id":"payments-v2-20260909","tasks":[{"id":"thread-plan","kind":"planning","spec_id":null,"lifecycle":"archived"}]}
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
| `pending_specs` | Ordered SPEC IDs not yet verified merged. |
| `unverified_handoffs` | Exact set of child IDs in `handoff_received`. |
| `unarchived_tasks` | Exact set of child IDs whose lifecycle is not `archived`. |
| `test_state` | Test-train status, exact candidate revision, L4 checkpoint state, and evidence pointers. |
| `release_state` | Release status, exact candidate revision, and evidence pointers. |
| `next_action` | The one action executable now; required only while `active`. |
| `resume_action` | Exact continuation after an accepted blocker or user stop; otherwise `null`. |
| `freshness` | SHA-256 of the current delivery map and task-tree snapshot. |
| `terminal` | Terminal reason and evidence, plus machine-checkable blocker/user-stop assertions; otherwise `null`. |

Child lifecycle is one of `queued`, `active`, `paused`, `handoff_received`, `verified`, or `archived`. Move a returned child to `handoff_received`, then `verified` only after independent verification, then `archived` only after archival succeeds.

An action has exactly `kind`, `target`, and `instruction`. `kind` is one of `wait`, `verify`, `archive`, `repair`, `dispatch`, `advance`, `resume`, `refresh_state`, or `repair_state`. Gate-specific failures still persist through this canonical vocabulary: route drift, planning handoff repair, and receipt-write repair use `repair` with the task, gate, or artifact named in `target`. `instruction` is directly executable rather than a status description.

Test status is `pending`, `running`, `passed`, or `blocked`. `test_state.l4_checkpoints` records `checkpoint_size: 10`, ordered SPECs, completed count, deterministic checkpoint objects, pass/fail evidence, exact candidate revisions, and final L4 reuse or rerun evidence. Release status is `pending`, `building`, `packaged`, `deployed`, `not_applicable`, or `blocked`.

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
  "next_action": {"kind": "wait", "target": "thread-spec-02", "instruction": "Wait for the current SPEC task snapshot."},
  "resume_action": null,
  "freshness": {
    "delivery_map_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "task_tree_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  },
  "terminal": null
}
```
