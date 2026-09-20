## Parent

[#94](https://github.com/williamxhero/skills/issues/94)

## What to build

Make the public default phase-context response compact: retain the run identity, phase, business-version/event cursor, actionable acceptance and dependency summaries, unresolved exceptions, and stable references, while omitting the duplicated full snapshot from the transport envelope. The durable snapshot and all completion/evidence semantics must remain unchanged.

## Acceptance criteria

- [ ] Default phase-context output no longer embeds the complete snapshot payload.
- [ ] Required metadata, actionable summaries, incremental events, unresolved exceptions, and stable references remain available.
- [ ] Persisted runtime snapshot and business event/version records are unchanged by context assembly.
- [ ] Existing false-completion, evidence Gate, stale-state, dependency, recovery, and synchronization tests remain green.
- [ ] The public CLI and programmatic context boundary return the same compact contract.

## Blocked by

None (can start immediately).
