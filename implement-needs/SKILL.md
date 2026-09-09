---
name: implement-needs
description: 'Autonomously orchestrate a broad software requirement end to end: default-grill it, partition it into scoped specs, create tickets, launch difficulty-routed Codex tasks for each spec and any blocker, merge, then build and deploy. Use when the user explicitly requests this no-confirmation controller workflow or says "implement needs".'
---

# Implement Needs

The invoking task is the **controller**. It stress-tests the requirement, delegates high-reasoning planning, dispatches and verifies planning, spec, and blocker tasks, then owns the final build and deployment. It is a medium-cost supervisor: it does not author the SPEC/ticket decomposition or implement a spec's product code itself. One fresh planning task owns the complete multi-SPEC and ticket design; each spec then gets one fresh implementation task that owns implementation through merge. Any issue blocking forward progress gets a focused repair task. Archive each task after its outcome is verified, then resume its parent workflow.

Deliver without interviews or approval checkpoints. A run is not complete until every spec is merged into the default branch, the complete `test-release-train` gate is green, and the resulting revision is built, deployed when the repository has a configured deployment target, and smoke-tested.

Invocation explicitly requests and authorizes the Codex child-task lifecycle, including dedicated blocker-repair tasks, and the normal repository and configured deployment mutations required by this workflow: task creation and archival, branches, tracker items, commits, pull requests, merges, builds, releases, and deployment. It does not authorize inventing credentials, bypassing access controls, weakening required checks, or destructive recovery outside the stated requirement.

## Source protocols

Resolve every protocol at controller startup and read it in full before its phase begins. The current session's Available Skills catalog is the first lookup surface, not an availability boundary: Ask Matt intentionally marks user-invoked skills such as `to-tickets` and `implement-spec` with `disable-model-invocation: true`, so their absence from that catalog is expected.

Use this resolution order:

1. Use the canonical-name entry in the current session's Available Skills catalog when present.
2. Locate the package lock by checking the parent of each advertised `.agents/skills` root, then the configured agent home, then the user's `.agents/.skill-lock.json`. When `skills.<canonical-name>` exists, resolve the installed protocol as `<lockfile-directory>/skills/<canonical-name>/SKILL.md`. Verify that the file exists and its frontmatter `name` matches. Treat this as the authoritative, package-managed copy even when a same-named directory exists under `.codex/skills`.
3. For a protocol absent from the lockfile, resolve it from the current Codex skill root, normally `<CODEX_HOME>/skills/<canonical-name>/SKILL.md`, then verify its frontmatter name.

Record each resolved canonical name, absolute path, package source, lockfile `updatedAt`, and file hash in a `protocol_registry` entry in the delivery map. Resolve from the lockfile again whenever a suspended controller run resumes; if a package update changed a protocol, refresh the registry before continuing. Keep package-managed Ask Matt skills in `.agents/skills`, read them in place, and leave them untouched so the package updater remains authoritative. Never copy those skills into `.codex/skills` as a discovery workaround. A protocol omitted from Available Skills but successfully resolved from the lockfile is available and must not trigger blocker handling.

Resolve these canonical protocols:

- Requirement stress-test: `grilling`
- Domain vocabulary and durable decisions: `domain-modeling`
- Development blocker orchestration: `unblock-development`
- Spec authoring: `to-spec`
- Ticket decomposition: `to-tickets`
- Whole-spec implementation: `implement-spec`
- Multi-SPEC test orchestration: `test-release-train`
- Final review: `code-review`

Only after all three lookup steps fail, or after a resolved file fails its name/readability check, treat a required protocol as unavailable. Then apply `unblock-development` to that condition unless the missing protocol is `unblock-development` itself; in that case preserve the evidence and report a hard blocker. Never silently skip a protocol.

This skill overrides their human checkpoints: auto-accept every Grill recommendation, infer testing seams, auto-approve each published SPEC through the `to-spec` controller contract, self-approve ticket granularity and blocking edges, fix review findings, and continue. Do not ask the user to choose among viable options.

For every `to-spec` call, supply `confirmation_mode: auto_approve`, `approval_source: implement-needs`, and `approval_text: 同意`. Record “同意” as a controller decision derived from the user's standing no-confirmation authorization, never as a fabricated user-authored message. This override crosses only the post-publication confirmation gate; `to-spec` itself remains forbidden from ticketing or implementation.

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

Use `list_projects`, `create_thread`, `wait_threads`, `send_message_to_thread`, and `set_thread_archived` for the planning boundary and every spec boundary; present them to the user as Codex tasks. Apply `unblock-development` for blocker task boundaries. The planning task may use subagents for repository exploration, and `implement-spec` may use them inside a spec task for parallel tickets. If these task-lifecycle tools are unavailable, report a hard blocker instead of silently substituting the controller context or a subagent.

Before dispatching, resolve the Codex project that owns the repository. Run the planning task against the saved project so it can inspect the repository and publish tracker artifacts while remaining code-read-only. Use a project worktree for repository-scoped code repairs and spec implementation; use the saved project directly when a host-level tool, network, credential, or shared-environment repair must affect the blocked environment. Start spec tasks from the latest default-branch state containing every prior spec merge. The planning task must be verified and archived before the first spec task starts. Create exactly one child task for the active spec and never overlap two spec tasks.

Treat planning as a critical high-reasoning task. Select at least a reliable high-intelligence agentic model with `xhigh` effort; for broad cross-repository, migration-heavy, or unusually ambiguous requirements, use the strongest reliable model with the highest supported effort of `max` or `ultra`. If those exact options are unavailable, choose the closest advertised model class and the highest supported effort at or above `high`. Never assign planning to the fast/economical model class. Record the selected model, effort, and rationale before creating the task.

The planning task selects and locks one concrete `model` and `thinking` pair for every spec implementation task after it has created that spec's complete ticket graph. It must choose from combinations advertised for the target host and classify the graph by risk and coupling; ticket count alone does not determine difficulty. The planning model's high floor applies only to planning, not to implementation tasks. Use the following routing table so easy implementations may remain economical while difficult work receives stronger models:

| Difficulty | Typical shape | Model class | Reasoning effort |
| --- | --- | --- | --- |
| Easy | Localized, known-pattern product change | Fast, economical coding model | `medium` |
| Standard | Several modules or an unfamiliar but bounded integration | Balanced agentic coding model | `high` |
| Hard | Cross-cutting behavior, migrations, concurrency, security, or performance work | Reliable agentic workhorse | `xhigh` |
| Extreme | Multi-repository coordination or unusually fragile compatibility and release constraints | Strongest reliable model available | Highest supported of `max` or `ultra` |

Record each implementation task's exact recommended model, effort, difficulty, rationale, and fallback class in the delivery map. The controller executes that recommendation without reclassifying the work. If the exact pair is unavailable at task creation, use the nearest currently advertised pair in the same or stronger class and record the substitution. If the spec or ticket graph changed materially, invalidate the recommendation and create a high-reasoning planning revision task; verify and archive it before implementation. Let `unblock-development` classify and route repair tasks independently when blockers actually arise.

## Message phase and controller liveness

Treat the controller's message phase as control flow, not presentation. `final_answer` yields and ends the controller's current turn; never use it as a progress heartbeat. A polished progress recap is still `commentary` while work remains.

Write every controller message addressed to the user in Chinese, including Grill rounds, progress updates, blocker reports, answers to side questions, and the terminal response. Keep exact identifiers, commands, paths, logs, protocol field names, and quoted evidence in their original language when translation would reduce precision. Internal reasoning, child-task prompts, and task-to-task handoffs may use English or whichever language is most token-efficient. Summarize child-task results in Chinese instead of forwarding an English handoff as the controller's user-facing update.

Maintain these top-level fields in the delivery map and refresh them before every controller update: `controller_state`, `active_phase`, `active_task_stack`, `planning_task_state`, `pending_specs`, `test_train_state`, `release_state`, and `next_action`. `controller_state` is one of `active`, `terminal_success`, `terminal_blocked`, or `user_stopped`. Any executable `next_action`, active or paused child task, incomplete or unarchived planning task, pending spec, unverified merge, open repair, incomplete test-train gate, or incomplete release gate requires `controller_state: active`.

The controller may emit `final_answer` only in one of these terminal states:

1. `terminal_success`: the planning task is verified and archived, every spec and ticket is verified complete and merged, every spec and repair task is archived, the test release train is green for the exact candidate, the default branch is healthy, and build, package, deployment or explicit deployment-not-applicable, and smoke-test evidence are recorded.
2. `terminal_blocked`: the same objective blocker has met the hard-blocker stopping rule, no safe in-scope action remains, and the exact resume point is recorded.
3. `user_stopped`: the user explicitly pauses, cancels, or stops the controller itself, and the exact resume point is recorded.

All other controller messages must use `commentary`, including status recaps, answers to side questions during an active run, phase-completion announcements, child-task completion announcements awaiting verification, and any state described by words such as "working", "uploading", "remaining", "not yet", "pending", "active", "next", “正在”, “尚未”, “剩余”, “待处理”, or “下一步”. After such commentary, immediately continue with the next tool call, task wait, verification, repair, or dispatch in the same turn. Never emit a controller final merely because one phase finished, a child task emitted its own final, or the user asked why progress paused.

Run this preflight immediately before every proposed controller final:

1. Re-read the delivery map and current task tree.
2. Assert that no planning, spec, or repair task is active, paused, queued, unarchived, or awaiting independent verification, unless entering `terminal_blocked` or `user_stopped`.
3. Assert that `pending_specs` is empty and `next_action` is empty.
4. Assert that `test_train_state` and `release_state` are terminal and supported by evidence.
5. Name the terminal state and its evidence in the final response.

If any assertion fails, reject the final, set `controller_state: active`, emit only a concise commentary update, and execute `next_action`. Apply the same gate to goal status: mark a goal complete only for `terminal_success`, and blocked only for `terminal_blocked`; otherwise leave it active.

A planning, spec, or repair child task may use its final response only as a structured handoff to the controller: `completed` with evidence for its assigned outcome, or `needs_repair` with a blocker packet. That child final is an event inside the active controller run. The controller must verify it, archive or resume the child as appropriate, and continue; it must never forward the child final as the controller's final response.

## Development blockers

Apply `unblock-development` in every phase: requirement discovery, Grill, tracker access, spec and ticket publication, implementation, Git/GitHub operations, CI, merge, build, packaging, deployment, and smoke testing. Treat it as the single source of truth for blocker detection, repair-task routing, model and effort selection, nested blockers, verification, archival, and exact-step resumption.

Keep the Implement Needs controller as the controlling parent. Record blocker fingerprints, repair task IDs, routing choices, evidence, integration results, and resumed steps in the delivery map.

## Test release train

Apply `test-release-train` as the single source of truth for acceptance-scope selection, per-ticket evidence, per-SPEC gates, 5-8-SPEC checkpoints, reusable wheel and environment setup, performance budgets, observability, and the final release gate.

The Implement Needs controller owns the train state across child tasks. Each SPEC child runs narrow ticket tests and the per-SPEC layers selected by `test-release-train`, then returns its evidence packet before merge. The controller verifies and records that packet, runs each due checkpoint after the SPEC is merged, and runs the final train gate against the exact release candidate. Route testing infrastructure blockers through `unblock-development`; return product failures to the SPEC task that owns the behavior.

## 0. Default-grill the requirement

Apply the `grilling` design-tree and frontier discipline in the controller before partitioning specs. Invocation supplies a standing answer of **“接受默认”** for every Grill round. For each round, formulate the complete current frontier, then emit a Chinese `commentary` update before adoption. Number every frontier question and show its question or a faithful concise summary, recommended answer, and brief evidence or rationale. Represent every question individually; when a frontier is large, split it across consecutive commentary updates rather than omitting questions. Label the round as automatically answered under the standing “接受默认” instruction so visibility cannot be mistaken for a request to pause.

After the round is visible in Chat, immediately adopt every recommendation and recompute the frontier without waiting for a reply. This is semantic auto-acceptance: record the decisions as accepted defaults without fabricating user-authored messages. When the frontier becomes empty, emit a concise Chinese Grill completion summary of the adopted decisions, then continue directly into the delivery map.

Facts are still evidence, not defaults. Investigate repository state, configuration, existing behavior, history, and available integrations wherever a question depends on them. Build recommendations from that evidence and the default policy. Prefer a reversible seam or the smallest backward-compatible behavior when several answers remain viable.

Persist `.scratch/<initiative>/default-grill.md` with each visible round's questions, adopted recommendations, rationale, evidence pointers, and downstream decisions. The persisted record and Chat updates must account for the same question set. Apply `domain-modeling` as decisions settle: update canonical domain terms in the appropriate `CONTEXT.md`, and create an ADR only when the decision is hard to reverse, surprising without context, and the result of a real trade-off.

Continue until the design-tree frontier is empty and no requirement branch is silently assumed. Treat that state as the confirmation normally required by `grilling`; do not request a final user confirmation. The accepted decisions become the authoritative input to the delivery map and every subsequent spec.

## 1. Delegate SPEC and ticket planning

Create a skeleton `.scratch/<initiative>/delivery-map.md` containing the requirement, `protocol_registry`, Grill evidence, controller-liveness fields, and the planning task's selected model and effort. Then create exactly one fresh planning task titled `Implement Needs Plan: <initiative>`. This task owns the complete multi-SPEC decomposition and every ticket graph; the controller owns only evidence-based verification and scheduling.

Give the planning task context pointers to the requirement, visible Grill record, repository instructions, domain vocabulary and ADRs, tracker configuration, default branch, protocol registry, default policy, `test-release-train`, and the target host's currently advertised model/effort combinations. Its outcome is to:

1. Translate the requirement into the minimum ordered set of scoped specs. Split at domain, user outcome, integration, migration, or release boundaries; keep tightly coupled behavior together and avoid an omnibus spec.
2. Apply `to-spec` to every scope with the documented auto-approval fields, publish each SPEC, and verify that planning changed no product or test code.
3. Apply `to-tickets` to every SPEC, skip its human quiz, self-verify tracer-bullet granularity and blocking edges, and publish every ticket graph.
4. Assign and lock one concrete implementation `model + effort` recommendation for every SPEC using the routing policy above.
5. Initialize `test-release-train`: resolve owners, repositories, acceptance scopes, public-contract and environment flags, fixed baselines, and 5-8-SPEC checkpoint boundaries.
6. Complete the delivery map with ordered specs, dependencies, published artifact references, auto-approval evidence, locked implementation routing, status, future implementation task fields, blocker evidence fields, test-train state, and release state.

Follow the planning task with compact waits. Answer preference requests from the default policy; route blocker packets through `unblock-development`. Treat its final as a claim. Verify that every requirement is owned by exactly one spec, dependencies are acyclic, every spec can merge safely before the next, every ticket graph is complete, every SPEC has a supported implementation routing pair and one checkpoint, all artifacts were published, and no product or test code changed. Return any failure to the same planning task. Archive it only after every condition passes, then record `planning_task_state: verified_archived`.

## 2. Dispatch and deliver each spec

Process specs sequentially. Begin the next spec only after the current spec is merged and the default branch is healthy.

For each spec:

1. Refresh from the default branch, mark the spec active, and verify that its published SPEC and ticket graph still match the locked planning artifacts. If scope changed materially, run a planning revision task before continuing.
2. Read the locked implementation model and effort from the delivery map, resolve only an availability substitution when necessary, record the exact pair, and create a fresh project child task titled `Implement Needs <NN>: <spec title>` from the latest default branch.
3. Give the child a self-contained prompt with context pointers to the requirement, spec, every ticket and blocking edge, repository instructions, default branch, delivery-map entry, current test-train state and acceptance scope, blocker packet contract, and child handoff contract from the controller-liveness section. Its outcome is: apply `implement-spec`, implement every ticket with narrow ticket tests, apply the per-SPEC `test-release-train` gate, run the full review, rerun only the review-fix impact set, fix findings, merge the completed spec into the default branch, clean up its implementation worktrees, and return the PR, merge commit, closed tickets, test evidence packet, checks, and blocker evidence. Tell it to apply defaults without user checkpoints, stay within this one spec, and return a `needs_repair` blocker packet whenever `unblock-development` is required.
4. Follow the child with compact task-wait snapshots. When it requests a preference, answer from the default policy and continue it in the same task. When it returns a blocker packet, apply `unblock-development`, then resume this same spec task after the repair is verified and archived. When it stops before merge without a blocker, send a focused completion follow-up to the same task. Do not replace it merely to obtain a fresh context.
5. Treat the child's final message as a claim, then independently verify that every ticket is complete, the selected per-SPEC test layers and any public-contract L3 obligation are green, the PR or local integration is merged, and the reported merge commit is reachable from the current default branch. Validate the evidence packet against `test-release-train`; if any condition fails, return the evidence to the same child task and wait again.
6. After verification, close the spec and tickets, record the merge and per-SPEC test evidence, archive the child task, and record archival success. Update the train state. When this SPEC closes a checkpoint, run its affected-owner regression before starting the next SPEC. Start the next spec only after the current child is archived, every due checkpoint is green, and the refreshed default branch is healthy.

One child task owns one spec from implementation through merge. Never reuse it for another spec, and never let two spec child tasks overlap. The controller retains the delivery map and release context while completed implementation context disappears into archived tasks.

If a later spec reveals a defect in an already merged increment, add the smallest repair ticket to the current spec unless it changes the earlier spec's accepted behavior; in that case create and deliver a repair spec before continuing.

## 3. Build, package, and deploy

After all specs are merged, every checkpoint is green, and no repair task remains open, release the exact default-branch revision containing them. Apply `test-release-train` to final acceptance and `unblock-development` to release blockers:

1. Derive build, packaging, release, deployment, and smoke-test commands from repository configuration and CI before consulting prose documentation.
2. Freeze the exact release-candidate revisions. Produce each repository's standard artifact once and record its version, checksum or immutable identifier, and source commit.
3. Run the final `test-release-train` gate against those exact revisions and artifacts. Reuse the built artifacts and isolated environments for installed-artifact checks, deterministic replays, packaging, deployment, and smoke verification.
4. Deploy through the repository's configured non-interactive release path to its designated default target. Reuse existing credentials and environment bindings; never create a new target or substitute an environment by guesswork.
5. Run configured smoke tests or verify the smallest representative user journey and service health checks. Confirm that the deployed revision or artifact identifier matches the tested artifact.
6. Record the test-train, release, and deployment evidence in the delivery map and report the completed specs, child task IDs with model and effort, merge commits, artifacts, deployment target, and verification result.

When the repository has no deployment target by design, finish after the verified package and record deployment as not applicable. When deployment is expected but its target or credentials are absent, complete all safe preceding work and report a hard blocker rather than asking a confirmation question.

## Recovery and stopping conditions

Keep progressing through ordinary test failures, merge conflicts, CI failures, review findings, tool failures, and network failures by applying `unblock-development`. Permit one immediate retry for a plausibly transient operation; if it remains red, let that skill open or reuse its repair task. Use a documented automated rollback when deployment fails and the rollback target is unambiguous; otherwise stop further release mutation and preserve the evidence.

Stop only when continued work is impossible without unavailable authority or external state, such as required human review, missing credentials, an inaccessible deployment target, or a protected branch that cannot be merged by the available identity. Do not convert a missing preference into a blocker; apply the default policy. Report the exact completed boundary, evidence, and resumable next action without soliciting option approval.
