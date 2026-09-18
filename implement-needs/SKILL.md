---
name: implement-needs
description: 'Run a requirement through Grill, incremental SPEC/ticket delivery, sequential implementation, merge, release, and repository synchronization.'
---

# Implement Needs

`implement-needs` is a thin controller. SQLite owns state, ordering, idempotency, and
recovery; delegated skills own semantic work.

## Fixed flow

1. Open or resume `.scratch/<initiative>/implement-needs.db`.
2. Run `scripts/reconcile.py` and recover the oldest pending action; render the unique
   next action with `scripts/dispatch.py`.
3. Invoke `grilling`; accept viable AI recommendations automatically.
4. Invoke `to-spec` for one umbrella SPEC and ordered child SPECs with SPEC-level dependencies.
5. Select the first unblocked SPEC.
6. Invoke `to-tickets` for that SPEC only; record the ticket graph and GitHub readbacks.
7. Invoke `implement-spec` for the whole SPEC: one task, branch, worktree, and PR.
8. Verify commits, tests, review, PR, merge, ticket closure, and thread archival.
9. Repeat from step 5 until all SPECs are closed.
10. Invoke `test-release-train`, package/deploy when configured, then `IN: Commit n Push`.

SPECs run strictly sequentially. Tickets never receive their own implementation task,
thread, branch, worktree, or PR.

## Automatic decisions

After invocation, accept the AI recommendation or repository default for every viable
choice. Record `controller_approved`, the selected value, recommendation, evidence,
and rationale in SQLite. Continue without asking the user.

## Thread lifecycle

Register every thread before sending work. A new thread must receive route readback and
its assignment in the same controller turn. If that cannot happen, record
`cancelled_before_start` or `abandoned_after_bootstrap`, archive it, and read back the
archive before dispatching anything else. Never wait on an idle bootstrap thread.

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
`scripts/transitions.py`. Runtime phase observations use the same controller entry
with an observation key and are projected into deterministic metrics by
`controller.py metrics`; see [runtime observability](references/runtime-observability.md).
External side effects use operation intents, reconciliation, action claims and
shared-resource coordination; dependency readiness and bounded recovery are defined
in [side-effect and recovery safety](references/side-effect-recovery.md).
Use `scripts/export_status.py` only for read-only snapshots; the database remains
the runtime source of truth.

When checking delivery evidence, load [delivery-evidence.md](references/delivery-evidence.md)
and use the versioned receipt commands. Keep worker claims separate from
controller-read CI receipts; reuse requires an exact applicability-key match.

For deterministic local work, use `controller.py advance`. It may cross multiple
SQLite-determined transitions, but it must stop at `needs_llm`, `waiting_external`,
`blocked`, or `completed`. A waiting result carries a stable external request ID,
event cursor, wake condition, and next safe check time. Polling an unchanged request
does not create a semantic event.

Load [context-and-tools.md](references/context-and-tools.md) only when entering the
snapshot, recovery-context, external-wait, or host-operation phase. Do not read every
reference at controller startup. Write snapshots with the expected event cursor so a
stale context is rejected and refreshed before any state-changing action.
