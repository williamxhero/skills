# Thread registry

The `threads` table in `.scratch/<initiative>/implement-needs.db` is the operational
index for every thread created by one
`implement-needs` run. Controller state answers “what action is next”; the registry
answers “which threads exist and what must happen to each one.”

The old `thread-registry.json` name refers only to a read-only export. Each database
entry records the legacy-compatible `thread_id` (the current backend thread ID),
`kind`, optional `spec_id`, the managed identity tuple (`task_id`, `run_id`,
`attempt_id`, `nonce`), backend handles (`client_thread_id`, `formal_thread_id`,
`host_id`), binding context (`owner_id`, `cwd`, `project_id`), and `title_token`,
along with `lifecycle`,
`outcome`, `last_observed_at`, `next_action`, `archive_operation_evidence`, and
`archive_readback_evidence`. Registry `outcome` may be `completed`,
`cancelled_before_start`, `abandoned_after_bootstrap`, `repaired`, or `unknown`.
Controller task lifecycle remains `queued`, `active`, `paused`, `handoff_received`,
`verified`, or `archived`; cancellation is represented by `outcome` plus the final
`archived` lifecycle, so the controller schema stays compatible.

Rows without `task_id` are legacy audit rows (`legacy_untracked`). They remain
exportable but are excluded from managed-task identity matching and must never be
used as evidence for creating, rebinding, or replacing a task. Recovery looks up
`formal_thread_id` plus `host_id` first, then `client_thread_id`, then the title
token as a candidate index. Every candidate still requires a fresh formal readback
matching identity, owner, cwd, and project before rebinding. A single-ticket-line
controller must also persist the applied route readback on the same registry row;
formal identity without model/effort route evidence is not recoverable.

## Reconciliation gate

Run this gate before every `create_thread`, after every status-changing wait, and before
terminal validation:

1. List all threads owned by the current run from the task backend.
2. Upsert their latest lifecycle, timestamp, and next action into the registry.
3. For `handoff_received`, verify artifacts, tests, tracker state, and repository state.
4. For verified threads, archive the exact thread and read its archival state back.
5. Persist archive operation and readback evidence before setting `lifecycle` to `archived`.
6. Reconcile every host ID absent from the registry and every registry ID absent from the
   host inventory before dispatching; never create a replacement while the original may
   be recoverable.
7. Dispatch only when no entry is `handoff_received`, `verified`, or `orphan`, and the
   one-active-SPEC rule remains true.

## Bootstrap barrier

Every newly created planning, SPEC, or repair thread must cross its bootstrap barrier
in the same controller turn: record it in the registry, read back its applied route,
and send `ROUTE_VERIFIED` (or the task-specific equivalent) before waiting or creating
another thread. A bootstrap-only thread that cannot cross this barrier is not useful
work. Record `outcome: cancelled_before_start` when route approval is unavailable, or
`outcome: abandoned_after_bootstrap` when the parent fails to continue it, then archive
the exact thread and read back archival state. Never leave such a thread idle, queued,
or waiting for an implicit future message.

An `active` or `paused` thread is retained and resumed or waited on according to its
`next_action`. `idle`, `completed`, or `notLoaded` is an observation, not an archive
result. An archive response without readback is incomplete.

Retain archived entries permanently for run auditability. A registry is complete only
when its entries agree with the fresh host inventory and all applicable evidence exists.
