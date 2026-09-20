## SPEC delivery

P1-6 is complete in dependency order:

1. P1-6.1 — minimal phase/entity context projections and history pointers — commit `432abd7`
2. P1-6.2 — context freshness gate and structured stale-state refresh — commit `48305a6`
3. P1-6.3 — context measurement CLI, coverage, and restart regression — commit `37acfa5`

All child tickets were implemented, verified, commented with evidence, and closed. The final P1-6 regression suite passed 281 tests. Measurements remain telemetry only and do not weaken or advance safety gates.
