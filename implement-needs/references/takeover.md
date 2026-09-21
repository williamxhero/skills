# Requirement takeover

Use this contract before mutating a requirement at any lifecycle stage. Its purpose
is to identify the first unfinished boundary without duplicating external work.

## Inventory

Collect current readbacks for these sections:

- `requirement`: stable requirement/Issue identity and evidence;
- `controller`: matching managed DB/run, terminal state, and current next action;
- `planning`: umbrella/child SPEC graph and dependencies;
- `tickets`: Issue identities, relationships, state, and delivery evidence;
- `implementation`: tasks, branches, worktrees, commits, tests, and handoffs;
- `merge`: PR identity, review, merge commit, and target-branch readback;
- `cleanup`: task/archive requests and `archived: true` readbacks;
- `release`: release-train, package, install, deploy, and smoke-test receipts;
- `synchronization`: local/remote branch equality and final repository readback.

Write the inventory as JSON with `schema_version: 1`. Every non-absent section must
carry a non-empty `evidence` list. Supported statuses are defined by
`scripts/takeover.py`; use its CLI as the executable validator:

```text
python scripts/takeover.py --inventory <takeover-inventory.json>
```

The planner is read-only. Its `resources_to_create` must be empty. It returns one
entry stage and one next action, or blocks on contradictory/missing evidence.

## Adoption

Prefer a matching managed run over external reconstruction. Reconcile its backend,
unknown intents, thread identities, side effects, and archive state, then execute the
persisted next action.

When no managed run exists, initialize one controller run and adopt verified facts
through existing formal APIs: planning/spec records, ticket ledger imports, task
identity enrollment, delivery receipts, phase receipts, and synchronization
readbacks. Preserve external IDs and idempotency keys. Record the takeover inventory
as business evidence before advancing.

Use each existing artifact once. Partial planning is completed in place; existing
tickets are reconciled before new tickets are created; active implementation is
resumed; completed implementation is verified; merged work proceeds to cleanup;
completed cleanup proceeds to final verification/release; synchronized work receives
terminal readback. A contradictory frontier remains blocked until another authoritative
readback resolves it.
