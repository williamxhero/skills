## Parent

[#94](https://github.com/williamxhero/skills/issues/94)

## What to build

Create a fixed before/after benchmark and safety-equivalence gate for the compact context. Report payload bytes and token estimates together with correctness, evidence coverage, stale-write rejection, pointer reachability, and unknown provider-data coverage. A lower Token estimate without equivalent safety evidence must remain inconclusive.

## Acceptance criteria

- [ ] Fixed benchmark fixtures cover empty run, active dependency chain, closed delivery, recovery exception, and release context.
- [ ] Before/after reports use the same completion predicates and safety checks.
- [ ] Reports include payload bytes, estimated/observed Token coverage, correctness outcomes, evidence coverage, and pointer reachability.
- [ ] Missing provider Token or fee data is reported as unknown, never as zero or savings.
- [ ] The metric/benchmark path is read-only with respect to completion and external side effects.
- [ ] Full regression and the fixed safety benchmark pass before the ticket can close.

## Blocked by

- #96 — P3-1.2: explicit history retrieval boundary
