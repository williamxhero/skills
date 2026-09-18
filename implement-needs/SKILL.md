---
name: implement-needs
description: 'Run a requirement through Grill, incremental SPEC/ticket delivery, sequential implementation, merge, release, and repository synchronization.'
---

# Implement Needs

`implement-needs` is a thin controller. SQLite owns state, ordering, idempotency, and
recovery; delegated skills own semantic work.

The task backend is the only task/thread boundary. Use `scripts/task_backend.py` for
creation, discovery, formal readback, route readback, assignment, archive, and
archive-state readback. The adapter contract is in `references/task-backend.md`.
Backend selection requires a live capability response. A command-line flag is not
capability evidence.

## Fixed flow

1. Open or resume `.scratch/<initiative>/implement-needs.db`.
2. Select the first usable live backend with `scripts/select_task_backend.py`. Use
   the configured Host Task API bridge when available, then the MCP/Desktop bridge,
   then a probed `codex-app-server-jsonrpc`. Each connector must answer its
   capability handshake and prove every operation, formal identity, and route
   readback. Without an allow receipt, invoke `IN: Unblock Development` and keep
   implementation blocked. A standard MCP bridge must complete MCP initialize,
   tools/list, and real tools/call probes; app-server fallback must probe every
   read and dry-run mutation operation, not only initialize. For issue #444, build
   the 33-ticket ledger from GitHub root/issue readbacks and run
   `import-ticket-ledger` before controller recovery.
3. Run reconciliation through the selected backend before every create, after each
   status-changing wait, before archive, and before terminal validation. Recover the
   oldest pending action; render the unique next action with `scripts/dispatch.py`.
5. Invoke `grilling`; accept viable AI recommendations automatically.
6. Invoke `to-spec` for one umbrella SPEC and ordered child SPECs with SPEC-level dependencies.
7. Select the first unblocked SPEC.
8. Invoke `to-tickets` for that SPEC only; record the ticket graph and GitHub readbacks.
9. In the default `whole-spec` mode, invoke `implement-spec` for the whole SPEC: one managed task, branch, worktree,
   and PR. Mint `task_id`, `run_id`, `attempt_id`, and `nonce`; create the task with
   its canonical title token, register it in SQLite, perform formal identity and
   route readbacks, then send its assignment in the same controller turn.
10. Verify commits, tests, review, PR, merge, ticket closure, and thread archival.
11. Repeat from step 7 until all SPECs are closed.
12. Invoke `test-release-train`, package/deploy when configured, then `IN: Commit n Push`.

SPECs run strictly sequentially in `whole-spec` mode. Tickets never receive their own
implementation task, thread, branch, worktree, or PR in that mode.

For a requirement that explicitly supplies a ticket queue, select
`execution_mode: single-ticket-line`. The queue is global across SPECs and is ordered
by `queue_position`; a ticket may advance only after every blocker is terminal. The
controller executes exactly one ticket action at a time inside the designated
controller task. It does not call `implement-spec`, create a ticket writer, or create
ticket-level branches, worktrees, or PRs. For issue #444 the only controller task is
`codex://threads/01a09a58-dfcd-72b0-a5d7-c359eefce9a2`; a missing or different task
identity is a repair/blocker, never permission to create a replacement.

Recovery queries `formal_thread_id + host_id`, then `client_thread_id`, then the title
token. The title token is only a candidate index. Rebind only one candidate whose
formal readback matches identity, owner, cwd, project, and retained evidence.
Ambiguous or incomplete discovery blocks replacement creation. Advance an attempt
only after its previous backend task is terminal and archived.
For single-ticket recovery, ledger completeness, full formal identity, and live
applied-route readback are one barrier: persist the controller binding only after
all three pass.

When a pre-existing app-server thread lacks managed identity metadata, use the
bridge's explicit enrollment path only after an authenticated task-management
readback supplies `task_id`, `run_id`, `attempt_id`, `owner_id`, `host_id`, `cwd`,
and `project_id`. Enrollment verifies the native thread id, cwd, and project id
before writing metadata. A null or missing native project id, or any missing
managed field, is an external-state blocker; do not mint a replacement identity
from the title, session id, or current process.

## Automatic decisions

After invocation, accept the AI recommendation or repository default for every viable
choice. Record `controller_approved`, the selected value, recommendation, evidence,
and rationale in SQLite. Continue without asking the user.

## Thread lifecycle

Register every thread before sending work. A new thread must receive route readback and
its assignment in the same controller turn. If that cannot happen, record
`cancelled_before_start` or `abandoned_after_bootstrap`, archive it, and read back the
archive before dispatching anything else. Never wait on an idle bootstrap thread.

Persist every operation receipt and readback in SQLite before marking local success.
Keep `identity_evidence`, `capability_evidence`, `configured_route_evidence`, and
`execution_evidence` separate. Requested model/effort is request evidence; the
default gate requires post-create configured-route readback. Claim actual provider
execution only with turn/request-level evidence.

## Controller rules

Code must validate every state transition and execute exactly one action at a time.
External actions use an idempotency key, then a readback before local success is stored.
Only close a ticket with commit and test evidence. Only start the next SPEC after the
current SPEC is merged, its tickets are closed, and its implementation thread is
archived. Terminal success requires every run-owned thread archived and repository
synchronization verified.

Use Matt Pocock skills `grilling`, `to-spec`, `to-tickets`, `implement-spec`, and
`code-review` through the outer workflow without modifying them. Use the self-maintained
`route-codex-task`, `test-release-train`, `IN: Unblock Development`, and
`IN: Commit n Push` skills for their narrow responsibilities.

External skill/tool results must follow [the receipt contract](references/external-actions.md)
and be recorded with `controller.py record-observation` before state advances. SQLite
primitives are in `scripts/control_db.py` and legal transitions are in
`scripts/transitions.py`. Use `scripts/export_status.py` only for read-only snapshots;
the database remains the runtime source of truth.

Legacy rows without `task_id` remain audit-only (`legacy_untracked`) and are excluded
from identity matching, replacement creation, and terminal success gates.
