# Thread registry

`thread-registry.json` is the operational index for every thread created by one
`implement-needs` run. Controller state answers “what action is next”; the registry
answers “which threads exist and what must happen to each one.”

Each entry records `thread_id`, `kind`, optional `spec_id`, `owner`, `lifecycle`,
`last_observed_at`, `next_action`, `archive_operation_evidence`, and
`archive_readback_evidence`. Lifecycle values are `queued`, `active`, `paused`,
`handoff_received`, `verified`, `archived`, or `orphan`.

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

An `active` or `paused` thread is retained and resumed or waited on according to its
`next_action`. `idle`, `completed`, or `notLoaded` is an observation, not an archive
result. An archive response without readback is incomplete.

Retain archived entries permanently for run auditability. A registry is complete only
when its entries agree with the fresh host inventory and all applicable evidence exists.
