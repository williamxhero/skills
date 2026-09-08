---
name: implement-needs
description: 'Autonomously orchestrate a broad software requirement end to end: default-grill it, partition it into scoped specs, create tickets, launch difficulty-routed Codex tasks for each spec and any blocker, merge, then build and deploy. Use when the user explicitly requests this no-confirmation controller workflow or says "implement needs".'
---

# Implement Needs

The invoking task is the **controller**. It stress-tests the requirement, plans, creates specs and tickets, dispatches and verifies spec and blocker tasks, then owns the final build and deployment. It does not implement a spec's product code itself. Each spec gets one fresh Codex child task that owns implementation through merge; any issue blocking forward progress gets a focused repair task. Archive each task after its outcome is verified, then resume its parent workflow.

Deliver without interviews or approval checkpoints. A run is not complete until every spec is merged into the default branch and the resulting revision is built, deployed when the repository has a configured deployment target, and smoke-tested.

Invocation explicitly requests and authorizes the Codex child-task lifecycle, including dedicated blocker-repair tasks, and the normal repository and configured deployment mutations required by this workflow: task creation and archival, branches, tracker items, commits, pull requests, merges, builds, releases, and deployment. It does not authorize inventing credentials, bypassing access controls, weakening required checks, or destructive recovery outside the stated requirement.

## Source protocols

Resolve and read these protocols by canonical name from the current session's Available Skills catalog when their phase begins. Do not assume that they share this skill's filesystem root:

- Requirement stress-test: `grilling`
- Domain vocabulary and durable decisions: `domain-modeling`
- Blocker diagnosis: `diagnosing-bugs`
- Spec authoring: `to-spec`
- Ticket decomposition: `to-tickets`
- Whole-spec implementation: `implement-spec`
- Final review: `code-review`

If a required protocol is unavailable or unreadable, route that condition through the blocker repair stack. Do not silently skip the protocol or fall back to a stale relative path.

This skill overrides their human checkpoints: auto-accept every Grill recommendation, infer testing seams, self-approve ticket granularity and blocking edges, fix review findings, and continue. Do not ask the user to choose among viable options.

## Default policy

Resolve choices in this order:

1. The requirement and explicit user constraints.
2. Repository instructions, ADRs, domain vocabulary, CI, release configuration, and documented conventions.
3. The dominant pattern already used by neighboring code and recent merged work.
4. The smallest backward-compatible choice that satisfies the requirement.

Record consequential assumptions in the relevant spec. Prefer reversible choices and existing seams. If information is missing, inspect the codebase, tracker, Git history, and configuration before applying the policy.

Use the remote default branch; otherwise use `main`, then `master`, then the current repository's sole long-lived branch. Use the configured tracker when available and local files under `.scratch/<initiative>/` otherwise. Missing tracker setup is not a blocker.

Preserve unrelated user changes. Work from isolated branches or worktrees when the current tree is dirty. Never fold pre-existing changes into this delivery unless they are explicitly part of the requirement.

## Controller and child tasks

Use `list_projects`, `create_thread`, `wait_threads`, `send_message_to_thread`, and `set_thread_archived` for spec and blocker boundaries; present them to the user as Codex tasks. `implement-spec` may still use subagents inside a spec task for parallel tickets. If these task-lifecycle tools are unavailable, report a hard blocker instead of silently substituting the controller context or a subagent.

Before dispatching, resolve the Codex project that owns the repository. Use a project worktree for repository-scoped code repairs and spec implementation; use the saved project directly only when a host-level tool, network, credential, or shared-environment repair must affect the blocked environment. Start spec tasks from the latest default-branch state containing every prior spec merge. Create exactly one child task for the active spec and never overlap two spec tasks.

Choose every spec or repair task's model and reasoning effort from the combinations advertised for the target host at task-creation time. For a spec task, classify the complete ticket graph. For a repair task, classify the blocker by diagnostic uncertainty, blast radius, reversibility, systems and permissions involved, and verification difficulty. Ticket count or error-message length alone does not determine difficulty.

| Difficulty | Typical shape | Model class | Reasoning effort |
| --- | --- | --- | --- |
| Easy | Localized known-pattern change or a well-understood tool/configuration repair | Fast, economical coding model | `medium` |
| Standard | Several modules or an unfamiliar but bounded environment/integration failure | Balanced agentic coding model | `high` |
| Hard | Cross-cutting behavior, migrations, concurrency, security, performance, or difficult debugging | Reliable agentic workhorse | `xhigh` |
| Extreme | Multi-repository coordination, irreversible compatibility work, or unusually fragile release constraints | Strongest reliable model available | Highest supported of `max` or `ultra` |

Use the closest supported class when model names change. If the target effort is unsupported, use the nearest supported effort at or above it, or the model's highest supported effort. Pass the selection explicitly as the task's `model` and `thinking`; record both and a concise rationale in the delivery map before creating the task.

## Message phase and controller liveness

Treat the controller's message phase as control flow, not presentation. `final_answer` yields and ends the controller's current turn; never use it as a progress heartbeat. A polished progress recap is still `commentary` while work remains.

Maintain these top-level fields in the delivery map and refresh them before every controller update: `controller_state`, `active_phase`, `active_task_stack`, `pending_specs`, `release_state`, and `next_action`. `controller_state` is one of `active`, `terminal_success`, `terminal_blocked`, or `user_stopped`. Any executable `next_action`, active or paused child task, pending spec, unverified merge, open repair, or incomplete release gate requires `controller_state: active`.

The controller may emit `final_answer` only in one of these terminal states:

1. `terminal_success`: every spec and ticket is verified complete and merged, every spec and repair task is archived, the default branch is healthy, and build, package, deployment or explicit deployment-not-applicable, and smoke-test evidence are recorded.
2. `terminal_blocked`: the same objective blocker has met the hard-blocker stopping rule, no safe in-scope action remains, and the exact resume point is recorded.
3. `user_stopped`: the user explicitly pauses, cancels, or stops the controller itself, and the exact resume point is recorded.

All other controller messages must use `commentary`, including status recaps, answers to side questions during an active run, phase-completion announcements, child-task completion announcements awaiting verification, and any state described by words such as "working", "uploading", "remaining", "not yet", "pending", "active", "next", “正在”, “尚未”, “剩余”, “待处理”, or “下一步”. After such commentary, immediately continue with the next tool call, task wait, verification, repair, or dispatch in the same turn. Never emit a controller final merely because one phase finished, a child task emitted its own final, or the user asked why progress paused.

Run this preflight immediately before every proposed controller final:

1. Re-read the delivery map and current task tree.
2. Assert that no spec or repair task is active, paused, queued, or awaiting independent verification, unless entering `terminal_blocked` or `user_stopped`.
3. Assert that `pending_specs` is empty and `next_action` is empty.
4. Assert that `release_state` is terminal and supported by evidence.
5. Name the terminal state and its evidence in the final response.

If any assertion fails, reject the final, set `controller_state: active`, emit only a concise commentary update, and execute `next_action`. Apply the same gate to goal status: mark a goal complete only for `terminal_success`, and blocked only for `terminal_blocked`; otherwise leave it active.

A spec or repair child task may use its final response only as a structured handoff to the controller: `completed` with evidence for its assigned outcome, or `needs_repair` with a blocker packet. That child final is an event inside the active controller run. The controller must verify it, archive or resume the child as appropriate, and continue; it must never forward the child final as the controller's final response.

## Blocker repair stack

Apply this mechanism in every phase: requirement discovery, Grill, tracker access, spec and ticket publication, implementation, Git/GitHub operations, CI, merge, build, packaging, deployment, and smoke testing.

A **blocker** is a reproducible failure or unavailable capability that prevents the next completion criterion. A one-off transient that succeeds on one immediate retry is not a blocker. Once the failure remains, stop spending the parent task's context on broad repair work and use this stack:

1. Capture a redacted blocker packet: blocked outcome, exact failing command or probe, observed output, expected success signal, relevant host/project/branch/worktree, attempts already made, and why progress cannot continue around it. Build the tightest unattended red/green probe available.
2. Pause the blocked task without closing it. The controller fingerprints the blocker from its probe, symptom, environment, and suspected scope, then checks the delivery map for an active or completed equivalent repair.
3. If no reusable repair exists, classify the blocker, select model and effort, and create a focused task titled `Implement Needs Repair <ID>: <blocker>`. Only the controller creates and archives repair tasks. Keep exactly one active leaf task; the spec task and any ancestor repair tasks remain paused beneath it.
4. Give the repair task the blocker packet, parent task ID, integration target, available authority, default policy, and the `diagnosing-bugs` protocol. Its sole outcome is to identify the cause, apply the smallest durable fix, prove the original probe green, and return redacted evidence plus any commit, PR, configuration, or environment change. It must not take over the remaining SPEC.
5. Integrate a repository-owned repair into the narrowest correct target: the active spec integration branch when it is spec-specific, or the default branch when it is an independently required prerequisite. Apply a host/tool/network repair to the same blocked environment. Preserve security controls, TLS verification, credential boundaries, and unrelated user configuration.
6. Follow the repair task with compact wait snapshots. If it encounters a distinct blocker, push another repair task using this same protocol; pause the current repair task until its child repair is verified and archived.
7. Treat repair completion as a claim. Re-run the original probe from the blocked parent's environment and verify any merge or configuration evidence. Return failed verification to the same repair task rather than opening a duplicate.
8. After the probe is green, record root cause, repair evidence, model, effort, changed state, and reuse scope in the delivery map; archive the repair task; then send the result to the paused parent task and resume it at the exact blocked step.

Never open two repair tasks for the same blocker fingerprint. Reuse a verified durable repair when its assumptions still hold. If the same fingerprint remains red after two completed repair-task attempts, no red-capable probe can be built with available evidence, or the repair requires unavailable authority, record a hard blocker and stop the dependent path instead of spawning tasks indefinitely.

## 0. Default-grill the requirement

Apply the `grilling` design-tree and frontier discipline in the controller before partitioning specs. Invocation supplies a standing answer of **“接受默认”** for every Grill round: formulate the complete current frontier with a recommended answer for each decision, then immediately adopt every recommendation and recompute the frontier. This is semantic auto-acceptance; record the decisions as accepted defaults without fabricating user-authored messages or waiting for a reply.

Facts are still evidence, not defaults. Investigate repository state, configuration, existing behavior, history, and available integrations wherever a question depends on them. Build recommendations from that evidence and the default policy. Prefer a reversible seam or the smallest backward-compatible behavior when several answers remain viable.

Persist `.scratch/<initiative>/default-grill.md` with each round's questions, adopted recommendations, rationale, evidence pointers, and downstream decisions. Apply `domain-modeling` as decisions settle: update canonical domain terms in the appropriate `CONTEXT.md`, and create an ADR only when the decision is hard to reverse, surprising without context, and the result of a real trade-off.

Continue until the design-tree frontier is empty and no requirement branch is silently assumed. Treat that state as the confirmation normally required by `grilling`; do not request a final user confirmation. The accepted decisions become the authoritative input to the delivery map and every subsequent spec.

## 1. Build the delivery map

Translate the requirement into the minimum ordered set of **scoped specs** that keeps each delivery coherent and reviewable. Split at domain, user outcome, integration, migration, or release boundaries when those scopes can be implemented and merged independently. Keep tightly coupled behavior together. A small requirement may produce one spec; a broad requirement must not become one omnibus spec.

Each spec must:

- deliver an observable increment on the default branch;
- fit one `implement-spec` cycle and fresh implementation contexts per ticket;
- declare dependencies on earlier specs and exclude later scopes;
- leave the repository buildable and tests green after merge;
- include rollout or compatibility work needed for that increment.

Order specs topologically, using enabling architecture or compatibility expansions before dependent behavior and cleanup after all migrations. Persist a resumable delivery map at `.scratch/<initiative>/delivery-map.md` with the requirement, ordered specs, dependencies, status, child task ID, model, effort, branch or PR, merge commit, archive status, blocker fingerprints and repair-task evidence, release status, and the controller-liveness fields required above. Resume from this map instead of duplicating completed work.

The delivery map is complete when every requirement is owned by exactly one spec, cross-spec dependencies are acyclic, and every spec can merge safely before the next begins.

## 2. Dispatch and deliver each spec

Process specs sequentially. Begin the next spec only after the current spec is merged and the default branch is healthy.

For each spec:

1. Refresh from the default branch and mark the spec active in the delivery map.
2. Apply the `to-spec` protocol to this scope only. Infer the highest practical existing test seam and publish the spec without requesting confirmation.
3. Apply the `to-tickets` protocol. Produce tracer-bullet tickets with explicit blocking edges; skip its quiz, verify the graph yourself, and publish it.
4. Assess the spec-ticket graph, select and record its model and effort, then create a fresh project child task titled `Implement Needs <NN>: <spec title>` from the latest default branch.
5. Give the child a self-contained prompt with context pointers to the requirement, spec, every ticket and blocking edge, repository instructions, default branch, delivery-map entry, blocker packet contract, and child handoff contract from the controller-liveness section. Its outcome is: apply `implement-spec`, implement every ticket, run the full review and required checks, fix findings, merge the completed spec into the default branch, clean up its implementation worktrees, and return the PR, merge commit, closed tickets, checks, and blocker evidence. Tell it to apply defaults without user checkpoints, stay within this one spec, and pause with a `needs_repair` blocker packet whenever the repair stack is required.
6. Follow the child with compact task-wait snapshots. When it requests a preference, answer from the default policy and continue it in the same task. When it returns a blocker packet, invoke the blocker repair stack, then resume this same spec task after the repair is verified and archived. When it stops before merge without a blocker, send a focused completion follow-up to the same task. Do not replace it merely to obtain a fresh context.
7. Treat the child's final message as a claim, then independently verify that every ticket is complete, required checks passed, the PR or local integration is merged, and the reported merge commit is reachable from the current default branch. If any condition fails, return the evidence to the same child task and wait again.
8. After verification, close the spec and tickets, record the merge evidence, archive the child task, and record archival success. Start the next spec only after the current child is archived and the refreshed default branch is healthy.

One child task owns one spec from implementation through merge. Never reuse it for another spec, and never let two spec child tasks overlap. The controller retains the delivery map and release context while completed implementation context disappears into archived tasks.

If a later spec reveals a defect in an already merged increment, add the smallest repair ticket to the current spec unless it changes the earlier spec's accepted behavior; in that case create and deliver a repair spec before continuing.

## 3. Build, package, and deploy

After all specs are merged and no repair task remains open, release the exact default-branch revision containing them. Route release blockers through the same repair stack:

1. Derive build, packaging, release, deployment, and smoke-test commands from repository configuration and CI before consulting prose documentation.
2. Run the clean release build and full release gate. Produce the repository's standard artifact and record its version, checksum or immutable identifier when available, and source commit.
3. Deploy through the repository's configured non-interactive release path to its designated default target. Reuse existing credentials and environment bindings; never create a new target or substitute an environment by guesswork.
4. Run configured smoke tests or verify the smallest representative user journey and service health checks. Confirm that the deployed revision or artifact identifier matches the built one.
5. Record the release and deployment evidence in the delivery map and report the completed specs, child task IDs with model and effort, merge commits, artifact, deployment target, and verification result.

When the repository has no deployment target by design, finish after the verified package and record deployment as not applicable. When deployment is expected but its target or credentials are absent, complete all safe preceding work and report a hard blocker rather than asking a confirmation question.

## Recovery and stopping conditions

Keep progressing through ordinary test failures, merge conflicts, CI failures, review findings, tool failures, and network failures through the blocker repair stack. Permit one immediate retry for a plausibly transient operation; if it remains red, open or reuse its repair task. Use a documented automated rollback when deployment fails and the rollback target is unambiguous; otherwise stop further release mutation and preserve the evidence.

Stop only when continued work is impossible without unavailable authority or external state, such as required human review, missing credentials, an inaccessible deployment target, or a protected branch that cannot be merged by the available identity. Do not convert a missing preference into a blocker; apply the default policy. Report the exact completed boundary, evidence, and resumable next action without soliciting option approval.
