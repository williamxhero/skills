# Deterministic advance runtime

`controller.py advance` owns only local transitions whose next state is uniquely
determined by the SQLite controller state. It records each transition as an action,
commits it independently, and may continue to the next state in the same call.
It never holds a transaction while waiting for a host task or network operation.

The call stops at exactly one boundary: `needs_llm`, `waiting_external`, `blocked`,
or `completed`. Semantic dispatch, ticket publication, review, merge, and external
task operations return `needs_llm` with the action packet. Existing wait actions
return `waiting_external`; repair and invalid dependency actions return `blocked`.
An empty frontier is `completed` only when every persisted SPEC is closed.

Every result includes the current SQLite event cursor as a state version, the action
packet, processed local actions, and an evidence reference. The event cursor is a
read boundary; versioned stale-write rejection is provided by the snapshot/context
contract in the next phase of SPEC #15.
