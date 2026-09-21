---
name: implement-needs
description: 'Start or take over a requirement at any lifecycle stage, reconcile existing planning and delivery artifacts, then continue through implementation, merge, release, and repository synchronization.'
---

# Implement Needs

Before a normal run, require a matching `QUALIFIED` report from
`validation/reports/index.json` for the current Skill and validation harness
digests. Check this with
`python validation/scripts/qualification_gate.py --scenario whole-spec-v1 --backend thread`.
If no matching report exists, run only the qualification preflight and stop
before GitHub or task mutation. Read `validation/README.md` for the
qualification commands and evidence contract. Qualification uses a new managed
task with a formally read-back non-empty `project_id`; a `requestId`, title
token, or `codex://threads/...` URL never supplies task identity. For the native
app-server fallback, pass the saved-project readback explicitly as
`project_id_source="saved_project_readback"`, with its non-empty evidence and
`project_canonical_path == cwd`; the bridge then omits Desktop-only `projectId`
from `thread/start` and verifies the returned formal thread by native `cwd` plus
the controller sidecar. This is controller saved-project identity, not a claim
that app-server natively returned project membership.

`implement-needs` is a thin controller. SQLite owns state, ordering, idempotency, and
recovery; delegated skills own semantic work.

The task backend is the only task/thread boundary. Use `scripts/task_backend.py` for
creation, discovery, formal readback, route readback, assignment, archive, and
archive-state readback. The adapter contract is in `references/task-backend.md`.
For managed turn failure classification, checkpointed continue, capacity
replacement, and side-effect reconciliation, read `references/managed-recovery.md`
and use `scripts/managed_recovery.py`.
Never accept terminal status alone as a handoff. A completed turn must contain
persisted work output in `items`, `output`, `message`, or `result`; otherwise classify
it as `UNCERTAIN`, re-read once for persistence lag, and apply the bounded managed
recovery policy. Repeated empty completion without a new progress marker is
`no_progress`, not permission for another untracked continuation.
Backend selection requires a live capability response. A command-line flag is not
capability evidence.

Read `references/startup-and-scope.md` before creating or resuming a run. Record and
verify the startup contract before phase work; do not replace a missing or mismatched
dependency with a same-looking Skill or an unverified host.

## Entry and takeover

Every invocation begins with read-only lifecycle discovery, even when the request
looks new. Read `references/takeover.md`, collect the requirement identity plus
controller, planning, ticket, implementation, PR/merge, cleanup, release, and
repository-sync readbacks, then run `scripts/takeover.py --inventory <file>`.

If a managed run exists, reconcile and resume its exact `next_action`. Otherwise,
initialize a run from the verified inventory and record existing artifacts through
the controller's formal import, identity, receipt, and reconciliation APIs. Continue
from the planner's unique entry action. Completed stages are evidence, not work to
repeat: create only artifacts absent from the readback, and never recreate an Issue,
task, branch, worktree, commit, PR, release, or deployment that already exists.

An incomplete or contradictory inventory is a blocker. Resolve it with additional
readback; never guess an earlier stage or silently restart from Grill.

## Continuation flow

1. Open the discovered managed database, or initialize `.scratch/<initiative>/implement-needs.db`
   and adopt the verified external inventory.
2. Select the first usable live backend with `scripts/select_task_backend.py`. Use
   the configured Host Task API bridge when available, then the MCP/Desktop bridge,
   then a probed `codex-app-server-jsonrpc`. Each connector must answer its
   capability handshake and prove every operation, formal identity, and route
   readback. Without an allow receipt, invoke `IN: Unblock Development` and keep
   implementation blocked. A standard MCP bridge must complete MCP initialize,
   tools/list, and real tools/call probes; app-server fallback must probe every
   read and dry-run mutation operation, not only initialize. Capability selection
   reuses an existing managed probe target and must not create a capability-probe
   task. A fresh live lifecycle probe is qualification-only; register it as a
   run-owned `probe` thread and require archive plus archive readback in a finally
   cleanup path before accepting its receipt. For issue #444, build
   the 33-ticket ledger from GitHub root/issue readbacks and run
   `import-ticket-ledger` before controller recovery.
3. Run reconciliation through the selected backend before every create, after each
   status-changing wait, before archive, and before terminal validation. Recover the
   oldest pending action; render the unique next action with `scripts/dispatch.py`.
4. Execute the takeover planner's unique entry action. Invoke `grilling`, `to-spec`,
   or `to-tickets` only when the corresponding verified state is absent or partial.
5. Select the first unblocked SPEC that is not already terminal.
6. In the default `whole-spec` mode, invoke `implement-spec` for the whole SPEC: one managed task, branch, worktree,
   and PR. Mint `task_id`, `run_id`, `attempt_id`, and `nonce`; create the task with
its canonical title token, register it in SQLite, perform formal identity and
   route readbacks, then send its assignment in the same controller turn. Carry
   the verified project readback fields into every bootstrap create.
7. Verify or reconcile commits, tests, review, PR, merge, ticket closure, and thread archival.
8. Repeat from step 5 until all SPECs are closed.
9. Invoke or reconcile `test-release-train`, package/deploy when configured, then
   `IN: Commit n Push`.

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
Capability selection only reads an existing target and exercises mutations as dry runs.
An explicitly requested live lifecycle probe is a managed `probe` thread: successful,
failed, and interrupted probe paths all archive it and require `archived: true` readback;
without that receipt, the probe is blocked rather than allowed.

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
