# Release and recovery

Read the relevant section when a blocker appears, a test checkpoint is due, or all SPECs are merged. The Controller Kernel remains authoritative for state, stopping, and terminal messages.

## Development blockers

Apply `unblock-development` during discovery, Grill, tracker access, publication, implementation, Git/GitHub operations, CI, merge, build, packaging, deployment, and smoke tests. It is the single source of truth for blocker detection, repair routing, model/effort selection, nested blockers, verification, archival, and exact-step resumption.

Keep Implement Needs as the controlling parent. Record blocker fingerprints, repair task IDs, routing, evidence, integration results, and resumed steps in the delivery map and controller state.

## Test release train

Apply `test-release-train` as the single source of truth for acceptance-scope selection, ticket evidence, per-SPEC gates, fixed `checkpoint_size = 10` L4 checkpoints plus one final tail, reusable artifacts and environments, performance budgets, observability, and the final gate.

The controller owns train state across children. Each SPEC child runs its narrow tests and selected per-SPEC layers, then returns evidence before merge. The controller verifies that packet, runs due checkpoints after each completed ten-SPEC segment and after the final tail, and runs the final train against the exact release candidate. A due or failed checkpoint blocks the next segment and terminal success. Route test-infrastructure blockers through `unblock-development`; return product failures to the SPEC task that owns the behavior.

## Build, package, and deploy

After all SPECs are merged, all fixed ten-SPEC checkpoints and the final tail checkpoint are green, and no repair remains open:

Record an `owner` for every release event. SPEC children may emit only merge, test, review, and evidence events. The controller alone may emit `freeze`, `build`, `package`, `deploy`, and `smoke` events, in that order, after every SPEC child is archived. Reject and repair any evidence packet that crosses this boundary.

1. Derive build, packaging, release, deployment, and smoke commands from repository configuration and CI before prose documentation.
2. Freeze exact release-candidate revisions. Build each standard artifact once; record its version, checksum or immutable ID, and source commit.
3. Run the final `test-release-train` gate against those exact revisions and artifacts. Reuse the last L4 checkpoint only when its candidate revision set exactly matches the frozen release candidate; otherwise rerun final L4 and record the new evidence. Reuse the artifacts and isolated environments for installed-artifact checks, deterministic replays, packaging, deployment, and smoke verification.
4. Deploy through the configured non-interactive path to the designated default target. Reuse existing credentials and bindings; do not create or guess a target.
5. Run configured smoke tests or the smallest representative user journey plus health checks. Confirm the deployed revision or artifact ID matches the tested artifact.
6. After successful deployment and smoke verification, invoke `commit-n-push` as the final mutation phase over every project-owned repository. It commits any remaining controller-owned release metadata, fetches each unambiguous upstream, normally merges remote changes, revalidates content-bearing merges, pushes, fetches again, and proves each local HEAD equals its remote-tracking HEAD. If a fetched merge changes any frozen release-candidate revision, invalidate the prior final gate and loop through affected tests, build, package, deployment, and smoke against the new revision before rerunning `commit-n-push`; terminal evidence must describe the ultimately synchronized and deployed revision. Route conflicts, network failures, permissions, branch protection, or validation failures through `unblock-development`; resume this exact synchronization step afterward.
7. Record train, release, deployment, smoke, and repository-synchronization evidence before refreshing controller state and invoking the terminal validator. The final evidence packet names completed SPECs, child IDs with their exact persisted route receipts, merge commits, artifacts, deployment target, verification results, and per-repository local/remote HEAD equality.

When no deployment target exists by design, record deployment as `not_applicable` with evidence and still run `commit-n-push`. When deployment is expected but its target or credentials are absent, complete safe preceding work and enter the blocker protocol before repository synchronization.

## Recovery and stopping

Continue through ordinary test failures, merge conflicts, CI failures, review findings, tool failures, and network failures using `unblock-development`. Permit one immediate retry for a plausibly transient operation; if it remains red, let that protocol open or reuse its repair task. Use a documented automated rollback when deployment fails and its target is unambiguous; otherwise stop release mutation and preserve evidence.

An objective hard blocker requires unavailable authority or external state, no safe in-scope action, and satisfaction of the blocker protocol's stopping rule. Record its fingerprint, evidence, paused tasks, and exact `resume_action` before proposing `terminal_blocked`. Missing preferences use defaults and are not blockers.

An explicit user pause, cancellation, or stop applies to the controller itself. Stop or pause active children, reconcile their lifecycle, preserve the exact `resume_action`, and propose `user_stopped`. A side question or status request is not a stop.
