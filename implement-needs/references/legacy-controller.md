# Legacy controller compatibility

This document defines the narrow compatibility boundary for runs created before
SR-08. It is not a new-run workflow.

Use the legacy controller only after read-only discovery identifies the exact
pre-SR-08 run and the caller explicitly requests compatibility continuation.
Preserve its existing database, artifacts, task identities, and recovery
evidence. Do not route a new requirement into it.

The independent Runner owns all new execution. Its control root, SQLite store,
writer lease, workers, and receipts are separate from the legacy controller.
The Runner's `legacy inspect` command is the supported bridge: it opens the old
database read-only and emits takeover inventory. It does not write, migrate,
repair, or delete the old database.

An identified legacy run may finish through its existing compatibility path
while migration evidence is gathered. A new Runner run must never attach to
that legacy lease or reuse its mutable state. Once the Runner has its own
verified receipts, SR-08 may retire the old entry path according to the release
decision; retirement must not remove historical run data.
