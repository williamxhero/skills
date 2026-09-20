## Summary

Implements P3-2 after P3-1: versioned phase-aware delta context, digest/cursor summaries, explicit collection history pointers, and fail-closed validation.

## Delivery graph

- Closes #100
- Closes #102
- Closes #103
- Closes #104

## Safety boundary

- P3-1 compatibility envelope remains available.
- Required phase facts, business version, event cursor, evidence coverage, and unresolved exceptions remain protected.
- Durable snapshots/events/evidence are not deleted or rewritten.
- Malformed, stale, mismatched, and unreachable delta references reject without business or external side effects.
- Provider Token/fee data remains unknown when unavailable.

## Validation

- Public context/delta/history CLI tests
- Five fixed P3 fixtures
- #57 false-completion, evidence, stale-write, recovery, synchronization, and read-only regression suite
- Deterministic payload/digest and safety-equivalence benchmark
