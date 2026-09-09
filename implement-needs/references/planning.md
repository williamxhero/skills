# Planning

Read this reference when the controller enters Grill or planning delegation. The Controller Kernel remains authoritative for state and message phase.

## Default-grill the requirement

Apply the `grilling` design-tree and frontier discipline. Invocation supplies a standing answer of **“接受默认”** for every round. Formulate the complete current frontier, then show every numbered question in Chinese commentary with its recommended answer and concise evidence or rationale. Split a large frontier across consecutive commentary updates instead of omitting questions. Label it as automatically answered under the standing instruction.

After each visible round, immediately adopt every recommendation and recompute the frontier without waiting for a reply. Record these as controller-accepted defaults, not fabricated user-authored messages. When the frontier is empty, give a concise Chinese commentary summary and proceed to the delivery map.

Treat facts as evidence, not defaults. Investigate repository state, configuration, behavior, history, and integrations wherever a question depends on them. Prefer a reversible seam or the smallest backward-compatible behavior when several answers remain viable.

Persist `.scratch/<initiative>/default-grill.md` with every visible question, adopted recommendation, rationale, evidence pointer, and downstream decision. The file and Chat must account for the same questions. Apply `domain-modeling` as decisions settle: update canonical terms in the appropriate `CONTEXT.md`, and create an ADR only for a consequential, surprising, hard-to-reverse trade-off.

The frontier is complete only when no requirement branch remains silently assumed. That state replaces the normal Grill confirmation. The accepted decisions become authoritative planning input.

## Delegate SPEC and ticket planning

Create the delivery-map skeleton with the requirement, `protocol_registry`, Grill evidence, a pointer to `controller-state.json`, and the planning model/effort. Create exactly one fresh task titled `Implement Needs Plan: <initiative>`. It owns the complete multi-SPEC decomposition and ticket graphs; the controller owns verification and scheduling.

Point the task to the requirement, Grill record, repository instructions, domain vocabulary and ADRs, tracker configuration, default branch, protocol registry, default policy, `test-release-train`, and currently advertised model/effort combinations. Require it to:

1. Produce the minimum ordered set of scoped SPECs. Split at domain, user outcome, integration, migration, or release boundaries; keep tightly coupled behavior together.
2. Apply `to-spec` with the documented auto-approval fields, publish each SPEC, and change no product or test code.
3. Apply `to-tickets`, skip its human quiz, verify tracer-bullet granularity and blocking edges, and publish every ticket graph.
4. Lock one concrete implementation model/effort recommendation for every SPEC.
5. Initialize `test-release-train`: owners, repositories, acceptance scopes, public-contract/environment flags, baselines, and 5–8-SPEC checkpoint boundaries.
6. Complete the delivery map with ordered dependencies, artifact references, auto-approval evidence, implementation routing, task placeholders, blocker fields, train state, and release state.

Follow the planning task with compact waits. Answer preference requests from the default policy and route blocker packets through `unblock-development`. Its final is a claim. Verify that every requirement belongs to exactly one SPEC, dependencies are acyclic, each SPEC can merge before the next, every ticket graph is complete, every SPEC has supported routing and one checkpoint, all artifacts were published, and no product or test code changed. Return failures to the same task. Archive it only after all conditions pass, then record its archived lifecycle in controller state.
