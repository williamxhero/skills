# Recovery safety

Recovery records classify failures as `transient`, `unknown_outcome`,
`code_defect`, `no_progress`, or `capability_denied`. One `retry_owner` owns the
budget for a failure fingerprint; changing the owner is rejected.

The fingerprint includes the normalized error, code digest, environment digest,
and category. A repeated fingerprint with the same strategy, progress marker, and
evidence is escalated as no progress. Attempt and time budgets are persisted in
SQLite and survive process restart. Exhaustion enters `paused`; it is not a
successful or terminal delivery state.

Resuming a repair requires evidence that the previous attempt is terminal and
archived, plus a new strategy or progress marker after escalation. The original
action returns to `pending` and must be independently reverified. No recovery
helper claims that a local lease or token fences an external executor.
