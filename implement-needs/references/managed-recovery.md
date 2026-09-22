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

An external create can fail between backend acceptance and Controller registration.
Recover that exact case with `recover-unregistered-bootstrap`, never with fabricated
archive evidence. The recovery records a distinct `tombstoned` lifecycle and
`backend_absent_after_create` outcome only when retained creation evidence, two
independent backend-absence readbacks, assignment absence, and managed-turn absence
are all present. This narrow tombstone may advance the attempt; no other outcome-only
claim may do so.

Terminal status is not completion evidence by itself. The matching persisted turn
must contain non-empty work output in `items`, `output`, `message`, or `result`.
An empty completed turn is `UNCERTAIN`; re-read it once for persistence lag, then
apply the persisted budget. Repeated empty completion with no new progress marker
is `no_progress` and cannot trigger another untracked continue.

The controller never retries a verified completed turn, never replays an uncertain
assignment, and never creates a replacement without an independently verified
archive and applied route. Missing formal identity, project, authorization,
history, useful output, or side-effect readback is `blocked`.

The durable `managed_turns` table stores the formal identity, previous turn,
failure class, terminal event, history readback, checkpoint, output evidence,
side-effect evidence, and operation-intent link. Its unique formal thread/turn
key makes repeated reconciliation idempotent across controller restarts.

For controller repair acceptance, use `scripts/continuation_gate.py` with the
before/after public controller snapshots and matching executed turn. Useful turn
output alone is insufficient: require a fresh completed turn, successful tool
results, a changed business frontier and version, and reconciled recovery and
cleanup readbacks. A capability-only probe is a valid probe, not a repaired run.
