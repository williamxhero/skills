## Parent

[#94](https://github.com/williamxhero/skills/issues/94)

## What to build

Add an explicit history retrieval boundary for the details omitted from the compact context. A worker or auditor must be able to follow a stable pointer to the persisted snapshot/history, while malformed, stale, or unreachable pointers fail closed and never cause the controller to infer missing state.

## Acceptance criteria

- [ ] Compact context emits a stable, auditable pointer for omitted history where needed.
- [ ] Explicit history retrieval returns the persisted source record without changing business state.
- [ ] Invalid, mismatched, and unreachable pointers produce structured rejection errors.
- [ ] Retrieval preserves evidence reachability for active and closed work.
- [ ] No state transition, completion gate, or external action can be authorized solely by a missing or inferred pointer.

## Blocked by

- #95 — P3-1.1: compact default phase-context envelope
