## Summary

Implements P3-3 after P3-2: versioned read-only context budget admission, safe delta fallback, and fail-closed overflow behavior.

## Delivery graph

- Closes #101
- Closes #106
- Closes #107
- Closes #108

## Safety boundary

- Existing context and delta contracts remain available.
- Required actionable facts, evidence coverage, business version, event cursor, and pointer reachability are checked before allow.
- Overflow never truncates required fields, records completion, advances business state, or invokes external actions.
- Provider Token/fee data remains unknown when unavailable.

## Validation

- Public read-only budget CLI
- Normal, near-limit, overflow, malformed, stale, and unreachable fallback cases
- Five fixed P3 fixtures
- Full #57 safety regression suite
