# Release and recovery

Read the relevant section when a blocker appears, a test checkpoint is due, or all SPECs are merged. The Controller Kernel remains authoritative for state, stopping, and terminal messages.

## Development blockers

Apply `unblock-development` during discovery, Grill, tracker access, publication, implementation, Git/GitHub operations, CI, merge, build, packaging, deployment, and smoke tests. It is the single source of truth for blocker detection, repair routing, model/effort selection, nested blockers, verification, archival, and exact-step resumption.

Keep Implement Needs as the controlling parent. Record blocker fingerprints, repair task IDs, routing, evidence, integration results, and resumed steps in the delivery map and controller state.

## Test release train

Apply `test-release-train` as the single source of truth for acceptance-scope selection, ticket evidence, per-SPEC gates, 5–8-SPEC checkpoints, reusable artifacts and environments, performance budgets, observability, and the final gate.

The controller owns train state across children. Each SPEC child runs its narrow tests and selected per-SPEC layers, then returns evidence before merge. The controller verifies that packet, runs due checkpoints after merge, and runs the final train against the exact release candidate. Route test-infrastructure blockers through `unblock-development`; return product failures to the SPEC task that owns the behavior.

## Build, package, and deploy

After all SPECs are merged, all checkpoints are green, and no repair remains open:

Record an `owner` for every release event. SPEC children may emit only merge, test, review, and evidence events. The controller alone may emit `freeze`, `build`, `package`, `deploy`, and `smoke` events, in that order, after every SPEC child is archived. Reject and repair any evidence packet that crosses this boundary.

1. Derive build, packaging, release, deployment, and smoke commands from repository configuration and CI before prose documentation.
2. Freeze exact release-candidate revisions. Build each standard artifact once; record its version, checksum or immutable ID, and source commit.
3. Run the final `test-release-train` gate against those exact revisions and artifacts. Reuse the artifacts and isolated environments for installed-artifact checks, deterministic replays, packaging, deployment, and smoke verification.
4. Deploy through the configured non-interactive path to the designated default target. Reuse existing credentials and bindings; do not create or guess a target.
5. Run configured smoke tests or the smallest representative user journey plus health checks. Confirm the deployed revision or artifact ID matches the tested artifact.
6. Record train, release, deployment, and smoke evidence before refreshing controller state and invoking the terminal validator. The final evidence packet names completed SPECs, child IDs with applied model/effort, merge commits, artifacts, deployment target, and verification results.

When no deployment target exists by design, finish after the verified package and record deployment as `not_applicable` with evidence. When deployment is expected but its target or credentials are absent, complete safe preceding work and enter the blocker protocol.

## Recovery and stopping

Continue through ordinary test failures, merge conflicts, CI failures, review findings, tool failures, and network failures using `unblock-development`. Permit one immediate retry for a plausibly transient operation; if it remains red, let that protocol open or reuse its repair task. Use a documented automated rollback when deployment fails and its target is unambiguous; otherwise stop release mutation and preserve evidence.

An objective hard blocker requires unavailable authority or external state, no safe in-scope action, and satisfaction of the blocker protocol's stopping rule. Record its fingerprint, evidence, paused tasks, and exact `resume_action` before proposing `terminal_blocked`. Missing preferences use defaults and are not blockers.

An explicit user pause, cancellation, or stop applies to the controller itself. Stop or pause active children, reconcile their lifecycle, preserve the exact `resume_action`, and propose `user_stopped`. A side question or status request is not a stop.
