# Context and tool boundaries

The controller keeps the complete SQLite event log and gives a semantic turn only a
phase-minimal context. `controller.py context --run-id RUN --phase PHASE` returns the
latest snapshot, the event cursor after that snapshot, unresolved exceptions, and
only events after the snapshot cursor. The context preserves the current acceptance
criteria, direct dependency interfaces, recorded decisions, worktree and version
metadata when present, and evidence references.

Use `controller.py save-snapshot` with `--expected-event-cursor` and, when replacing
an existing snapshot, `--expected-state-version`. If another action has emitted an
event or the stored snapshot has been replaced, the write fails with a stale cursor or
version error; refresh the context before retrying. This prevents an old summary from
authorizing a new mutation.

Tool calls use one of the compact envelopes:

- `tool-success`: result, key identifiers, state version, and a URI for complete evidence.
- `tool-failure`: error category, short diagnostic fragment, and a URI for the full log.
- `tool-waiting-external`: stable request ID, event cursor, wake condition, and next safe check time.

Host-only operations use `host-operation`. The envelope is `capability_required`
until the requested capability is present in the live capability set. An envelope is
an operation request or receipt; it is not authorization and cannot bypass the task
backend, identity, route, or receipt gates.

When a host wait is unavailable, persist an `external_waits` row and poll with a
bounded schedule. An unchanged poll returns `semantic_round: false` and does not
append an external-change event. A changed poll returns a compact receipt while the
full audit remains in SQLite and the referenced evidence store.
