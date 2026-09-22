# Spec Runner Progress

The implementation branch is `codex/sr-01`.

Implemented in the isolated `spec-runner/` package:

- SR-01: installable CLI, official SDK adapter, two-stage verification, archive readback.
- SR-02: SQLite operations, writer leases, detached launcher, controls, drive/recovery, durable events.
- SR-03: local and GitHub tracker read/publish/readback with explicit relation evidence.
- SR-04: locked Skill sources, deterministic prompt rendering, bounded Grill, SpecPlan/TicketPlan validation.
- SR-05/06: isolated Git workspaces, candidate receipts, review validation, local merge, GitHub PR/check/merge adapter.
- SR-09: read-only arbitrary-stage inventory, deterministic frontier planning, SQLite takeover records.
- SR-07: public-CLI deterministic fault matrix, runtime diagnostics, release-evidence validation.
- SR-08: legacy database read-only observation and `/implement-needs` handoff script.

Verified locally: 28 unit/integration tests, compile checks, wheel build, fresh-venv CLI install, Chinese/space paths, temporary Git candidate/merge, and deterministic fault matrix.

Not verified in this environment: live SDK process restart/active-turn handoff, live GitHub sandbox writes/merge, Windows native kill/restart matrix, and upstream Matt Skill invocation. These remain explicitly `not_verified`; deterministic evidence does not substitute for them.
