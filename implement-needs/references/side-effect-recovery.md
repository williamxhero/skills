# Side-effect and recovery safety

Every external mutation begins as an operation intent. Its normalized parameters,
input digest, target, generation, idempotency key and stable external request ID are
stored before execution. A network retry reuses the intent and request ID. Changed
parameters, target, or generation creates a new logical intent. Reusing an explicit
idempotency key with different intent is rejected.

An executor starts an intent only while it is `prepared` or after a complete
reconciliation proved `not_found`. A lost response is `outcome_unknown`; it cannot
be sent again until an authoritative readback records `not_found`, `succeeded`, or
`failed` with evidence. A local record never claims end-to-end exactly-once delivery.

An action has one atomic SQLite claim. Its owner must pass the live-lease effect
guard before an external side effect. An expired claim cannot be taken over unless
the backend explicitly supports fencing and the previous outcome has been
reconciled. Shared repository, worktree, merge, and deployment keys use a separate
coordination database so different runs cannot coordinate only inside their own
SQLite ledgers.

Dependencies are satisfied by a delivery proof or a narrowly scoped, evidenced
waiver. `closed`, `cancelled`, `failed`, and `abandoned` are lifecycle states; only
the first with proof, or an explicit waiver, satisfies a dependency. Unknown and
forward dependencies require repair and never silently reorder a fixed queue.

Recovery fingerprints combine failure category, normalized error, code digest and
environment digest. One retry owner controls the persisted attempt/time budget.
Repeated identical strategy, progress and evidence is escalated as no progress;
budget exhaustion is `paused`. Resuming requires a terminal archived old attempt and
new strategy or progress evidence, then returns the original action to `pending`.
