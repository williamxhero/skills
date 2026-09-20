## Summary

Implements P3-1 after the safety hardening program: compact the default phase-context transport envelope, preserve explicit history retrieval, and add fixed safety-equivalence/token benchmark coverage.

## Safety boundary

- Durable snapshots and business events remain unchanged.
- Completion, evidence, authorization, stale-state, recovery, and side-effect gates remain required.
- Missing provider Token/fee data remains `unknown`.
- Lower payload size is not accepted without correctness and pointer-reachability evidence.

## Delivery graph

- Closes #94
- Closes #95
- Closes #96
- Closes #97

## Validation

- Full regression suite
- Fixed context benchmark
- Safety-equivalence and read-only assertions
