# External action receipt contract

The controller does not infer completion from a child message or a tool call. The
outer workflow executes the delegated skill or external operation, then records its
structured receipt with `controller.py record-observation`.

Every receipt contains `run_id`, `entity_type`, `entity_id`, `operation`, `status`,
`observed_at`, and `evidence`. A successful receipt must include the external ID or
revision that was read back. Failed receipts retain the error and become the next
recovery action. The same `idempotency_key` is reused when retrying an uncertain action.

Required receipts include:

- `to-spec`: published SPEC IDs, Parent relationships, acceptance criteria;
- `to-tickets`: ticket IDs, Parent relationships, dependency graph and generation;
- `implement-spec`: PR, commits, tests, review result and merge revision;
- thread operations: thread ID, lifecycle, archive operation and archive readback;
- release operations: candidate revision, artifact, deployment and smoke result;
- repository synchronization: fetch, merge, push and local/remote equality.

The controller advances only after the receipt is persisted and the corresponding
external readback is present.
