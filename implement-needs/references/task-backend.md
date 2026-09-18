# Task backend contract

`scripts/task_backend.py` is the single boundary for managed tasks. Logical backend
operations are `create_thread`, `list_tasks`, `read_thread`, `read_applied_route`,
`send_message_to_thread`, `set_thread_archived`, and `read_archive_state`.

`list_tasks` returns `reconciliation_status: inconclusive` when bounded pagination
ends with a cursor. That state blocks replacement creation. Recovery may call the
live backend itself with `formal_thread_id`, `client_thread_id`, and `title_prefix`
filters; the controller no longer depends on a caller-supplied inventory.

`mcp-task-connector` is either a normalized JSON-lines executable bridge or a
standard MCP stdio server selected with `--mcp-protocol mcp-stdio`. The standard
MCP path must complete `initialize`, `notifications/initialized`, `tools/list`,
and real `tools/call` requests. It requires a `capabilities` tool and normalized
tools for every operation below. A custom JSON-lines bridge must answer
`{"operation":"capabilities"}` with a formal probe target, then pass actual
`list_tasks`, `read_thread`, `read_applied_route`, and `read_archive_state` calls.
The mutating operations must accept `dry_run: true` and return `dry_run: true`; a
capability name alone is insufficient. The identity probe must contain task, run,
attempt, owner, cwd, and project fields, and route/archive reads must carry backend
evidence. The preferred native fallback is `app-server-bridge`: it calls real
app-server thread and turn methods, skips JSON-RPC notifications until the matching
response, and normalizes their records. Its controller-owned metadata sidecar supplies
only fields app-server does not expose (`task_id`, `run_id`, `attempt_id`, `owner_id`,
and host identity); every sidecar record must match native thread id, cwd, and project
id. Missing or mismatched metadata is a protocol failure. The legacy raw
`codex-app-server-jsonrpc` method-map path remains fail-closed. A flag that merely
names either backend is rejected.

The app-server probe must execute `list_tasks`, formal identity read,
applied-route read, archive read, and dry-run create/send/archive operations.
An initialize response or method map alone is not a capability receipt. The
method map must include a formal `probe_target`.

Select the native bridge with `--app-server-command "codex app-server --stdio"`
and `--app-server-metadata <identity.json>`, or reconcile with
`--backend-protocol app-server-bridge --backend-command "codex app-server --stdio"
--app-server-metadata <identity.json>`. The metadata file contains a `threads` array;
each record has all of `formal_thread_id`, `host_id`, `task_id`, `run_id`,
`attempt_id`, `owner_id`, `cwd`, and `project_id`. It is controller evidence and
cannot be synthesized from a title token.

`import-ticket-ledger` accepts only a GitHub readback envelope containing the exact
queue, `expected_ticket_count`, root GitHub evidence, and one issue readback per
ticket. Explicitly closed tickets also require structured commit/test evidence.
For #444 the count is exactly 33. GitHub `CLOSED` alone is source metadata, not
controller delivery evidence.

Bootstrap order:

1. Mint the identity and canonical title.
2. Create the backend task.
3. Register the attempt in SQLite before sending work.
4. Read formal identity and applied route.
5. Persist both readbacks.
6. Send `ROUTE_VERIFIED` and the assignment.

Every operation needs an operation receipt and a readback before local success is
stored. A failed or incomplete step enters reconciliation. The original attempt and
its evidence are retained; a new attempt is allowed only after terminal archival.

The CLI reconciliation entry point can query a live bridge:

```text
python implement-needs/scripts/controller.py --db <db> reconcile-backend --run-id <run-id> --backend-command <bridge>
```

The inventory-file form remains an offline test/import boundary only.

The inventory file is the selected adapter's normalized `list_tasks` result. Exit
code `0` means the inventory is complete and every registered managed task is bound;
exit code `1` means repair is required and replacement creation is blocked.
