## SPEC delivery

P1-5 is complete in the required order:

1. P1-5.1 — immutable policy identity and migration validation — commit `c6978b5`
2. P1-5.2 — backup/restore manifest and external-action reconciliation — commit `66cc75a`
3. P1-5.3 — policy/restore CLI, fail-closed errors, and restart regression — commit `8562ae3`

All child tickets were implemented, tested, commented with evidence, and closed. The final verification suite passed 273 tests. Local restore remains explicitly distinct from external rollback, and unresolved external outcomes block readiness.
