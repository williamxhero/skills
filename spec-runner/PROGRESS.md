# Spec Runner Progress

The implementation is based on the merged SR-01 through SR-09 implementation
commits. This file records
implementation and evidence separately; a completed code slice is not treated
as final release qualification.

Implemented slices in the isolated `spec-runner/` package:

- SR-01: installable CLI, pinned official SDK adapter, two-stage verification,
  and archive readback.
- SR-02: SQLite operations, writer leases, detached launcher, controls, drive
  recovery, and idempotent durable events. Recovery now records explicit
  `recovery_detected`, `step_resumed`, `step_verified`, `next_stage_started`,
  and `cleanup_readback` evidence.
- SR-03: local and GitHub tracker read/publish/readback with explicit relation
  evidence and unknown-outcome reconciliation.
- SR-04: locked Skill sources, deterministic prompt rendering, bounded Grill,
  and SpecPlan/TicketPlan validation.
- SR-05/06: isolated Git workspaces, candidate receipts, review validation,
  guarded local merge, GitHub PR/check/merge adapters, and a dependency-ordered
  local multi-SPEC delivery loop wired into the normal `start` run.
- SR-09: read-only arbitrary-stage inventory, deterministic frontier planning,
  SQLite takeover records, explicit `wait_then_takeover` /
  `interrupt_then_takeover` handover states, and a continuation entry into the
  normal Runner loop.
- SR-07: public-CLI deterministic fault matrix, evidence-backed runtime
  diagnostics, release report construction/validation, and wheel inspection.
- SR-08: read-only legacy database observation, the thin
  `/implement-needs` handoff script, and an explicit legacy compatibility
  boundary. New requests and explicit takeovers route only through the public
  Runner CLI; the old controller remains available only for identified
  pre-SR-08 runs.

Verified on 2026-09-23 in this branch: 63 collected `spec-runner`
unit/integration tests (62 passed, one skipped) plus 3 `/implement-needs`
handoff isolation tests, with one
authentication-dependent SDK test skipped, compile checks, Chinese and
space-containing paths, temporary Git candidate/merge, deterministic fault
matrix, public-CLI process restart recovery after both artifact boundaries,
public-CLI pause/resume at a stage boundary, structured GitHub read failures,
and release-report negative cases.

The SDK adapter now persists the formal thread and turn IDs at the published
`Thread.turn()` boundary before waiting for the result. A durable
`worker_turn_started` event makes an active live worker visible to status and
recovery instead of leaving it indistinguishable from a pending worker. A
cross-platform GitHub Actions contract workflow was added for the two supported
OS families; it runs the tests, builds the wheel, installs that wheel without
source `PYTHONPATH`, and executes the public fault matrix.

Release subjects now bind the qualification contract dimensions: build, SDK
package/version, Matt lock, prompt/schema/validator digests, configuration,
OS, trust mode, and scenario version. Their canonical digest is verified on
readback. A required evidence kind that is `not_verified` or `skipped` now
returns `eligible: false`; it cannot be represented as a release pass.

The multi-SPEC loop, SR-08 handoff isolation tests, live-turn identity
boundary, and active-control workflow contract raise the combined deterministic
suite to 58 passed with one
skipped. A temporary three-SPEC chain was delivered through real local Git
worktrees and merges, then replayed after simulating a crash after merge but
before receipt finalization; ancestor reconciliation completed without a
second merge. The same plan also passed through the public `start` entry.

The current `0.1.0` wheel was rebuilt from merged `master`, installed into a
fresh isolated target without source `PYTHONPATH`, and verified through the
installed public CLI: `--version` returned `0.1.0`, `diagnose package` passed,
and all ten deterministic fault cases passed. The wheel contained no
database, log, token, secret, or environment members.

A native Windows Win32 share-denied probe also used a Chinese/space path and
confirmed that replacement is blocked while an exact detached holder owns an
artifact file, then succeeds after that holder is terminated. This is a
single-file lock boundary, not the full DB/log/artifact cleanup matrix.

Local delivery now persists cleanup separately from merge: a simulated locked
managed worktree remains `cleanup_pending`, and the next drive retries only
cleanup from the durable receipt without re-running implementation or merge.
A native Windows probe then confirmed the same behavior through the installed
public CLI on a Chinese/space path using a delete-denying file handle.

Native cleanup regressions now hold Win32 delete-denying handles on tracked
SQLite, log, and artifact-directory resources in a Chinese/space path. Each
returns `pending` while locked and reaches `cleaned` through the same retry
path after the exact handle is released. This narrows, but does not erase, the
remaining cross-process Windows cleanup qualification gap.

The follow-up handover contract adds a public negative matrix for active source
writers and raises the deterministic test count to 41 passed with one skipped.

The deterministic evidence is not final whole-route qualification. A live
public SDK probe now completed both stages after active pause/resume, and a
separate live cancellation probe archived an interrupted second-stage worker.
Remaining acceptance gaps are GitHub-mode three-SPEC issue/PR/cleanup side
effects, arbitrary-stage mixed progress with actual issue/PR/cleanup side
effects, live GitHub sandbox/merge-queue behavior, upstream Matt Skill
invocation, and the broader native Windows file-lock cleanup matrix. These are explicitly
`not_verified`; no deterministic receipt is promoted to live evidence.
