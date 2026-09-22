# Spec Runner Progress

The implementation branch is `codex/spec-runner-delivery-hardening`, based on
the merged SR-01 through SR-09 implementation commits. This file records
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
  guarded local merge, and GitHub PR/check/merge adapters.
- SR-09: read-only arbitrary-stage inventory, deterministic frontier planning,
  SQLite takeover records, explicit `wait_then_takeover` /
  `interrupt_then_takeover` handover states, and a continuation entry into the
  normal Runner loop.
- SR-07: public-CLI deterministic fault matrix, evidence-backed runtime
  diagnostics, release report construction/validation, and wheel inspection.
- SR-08: read-only legacy database observation and the thin
  `/implement-needs` handoff script.

Verified on 2026-09-22 in this branch: 47 unit/integration tests with one
authentication-dependent SDK test skipped, compile checks, Chinese and
space-containing paths, temporary Git candidate/merge, deterministic fault
matrix, public-CLI process restart recovery after both artifact boundaries,
public-CLI pause/resume at a stage boundary, structured GitHub read failures,
and release-report negative cases.

The current `0.1.0` wheel was rebuilt from merged `master`, installed into a
fresh isolated target without source `PYTHONPATH`, and verified through the
installed public CLI: `--version` returned `0.1.0`, `diagnose package` passed,
and all eight deterministic fault cases passed. The wheel contained no
database, log, token, secret, or environment members.

The follow-up handover contract adds a public negative matrix for active source
writers and raises the deterministic test count to 41 passed with one skipped.

The deterministic evidence is not final whole-route qualification. Remaining
acceptance gaps are the real three-SPEC delivery loop, arbitrary-stage mixed
progress with actual issue/PR/cleanup side effects, live SDK active-turn
handoff, live GitHub sandbox/merge-queue behavior, upstream Matt Skill
invocation, and a native Windows kill/restart matrix. These are explicitly
`not_verified`; no deterministic receipt is promoted to live evidence.
