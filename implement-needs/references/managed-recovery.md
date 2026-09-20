# Managed turn recovery

`scripts/managed_recovery.py` is the controller-side recovery protocol for a
managed task. The backend adapter supplies formal readbacks; the protocol makes
the recovery decision and persists the turn receipt.

## Lifecycle

1. Create and persist the operation intent before `turn/start`.
2. Send the assignment once and retain the formal `turn_id`.
3. Wait for `turn/completed` matching both `formal_thread_id` and `turn_id`.
4. Read persisted history and output before accepting completion.
5. On disconnect, reconcile the original turn and registered side effects.
6. Continue the same formal thread only with a checkpoint envelope containing
   task, run, attempt, previous turn, failure class, and verified receipts.
7. On model capacity, archive the failed attempt and verify archive state before
   selecting and reading back a fallback route and creating a replacement.

The controller never retries a completed turn, never replays an uncertain
assignment, and never creates a replacement without an independently verified
archive and applied route. Missing formal identity, project, authorization,
history, or side-effect readback is `blocked`.

The durable `managed_turns` table stores the formal identity, previous turn,
failure class, terminal event, history readback, checkpoint, output evidence,
side-effect evidence, and operation-intent link. Its unique formal thread/turn
key makes repeated reconciliation idempotent across controller restarts.
