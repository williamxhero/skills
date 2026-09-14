---
name: implement-needs
description: 'Autonomously orchestrate a broad software requirement end to end: delegate Grill-to-tickets planning, implement one whole SPEC per task, run the release train, merge, package, deploy, and synchronize repositories. Use when the user explicitly requests this no-confirmation controller workflow or says "implement needs".'
---

# Implement Needs

`implement-needs` is the lifecycle controller for a broad requirement. It coordinates
specialist Skills and Codex tasks; it is not the executor of planning or product code.
The controller owns sequencing, standing defaults, supervision, evidence verification,
child-task closure, recovery, release gates, and terminal success.

## Standing authorization

The user authorizes the controller to perform every action required to complete the
requirement when both conditions hold:

1. The action is necessary for the requested lifecycle, including planning, issue
   publication, implementation, testing, review, branch/worktree management, PR
   creation and updates, merging, packaging, deployment, smoke checks, archival, and
   repository synchronization.
2. The action is non-destructive, or any destructive effect is recoverable through
   version control, a worktree, an archive, a backup, a documented rollback, or an
   equivalent recovery path.

This standing authorization removes confirmation pauses for those actions. Continue
through routine implementation choices, review fixes, test retries, merge operations,
task archival, and release steps without asking the user again. Prefer the smallest
recoverable operation and record its evidence.

Stop and ask the user only when an action is both material to the outcome and cannot be
made recoverable, or when it is outside the requested requirement and its necessary
lifecycle. A missing preference is not a reason to pause: inspect repository rules,
configuration, history, and the applicable protocol, then use the documented default.
Do not treat an ordinary PR merge, branch deletion after verification, task archival,
deployment, or final push as requiring another confirmation when it has a recovery
path and is necessary for the requested outcome.

## Authority boundary

Keep each responsibility in one place:

| Concern | Authority | Controller responsibility |
| --- | --- | --- |
| Grill, umbrella SPEC, child SPECs, tickets, Parent issue, planning routes | `grill-2-tickets` | Dispatch once, inject standing defaults, supervise, independently verify, archive |
| Task model/effort selection and applied-settings readback | `route-codex-task` | Supply difficulty evidence and require its route receipt |
| SPEC implementation and merge | `implement-spec` | Create one task per whole SPEC and verify its handoff |
| Any development or delivery blocker | `unblock-development` | Route the exact failed probe and resume the recorded action |
| Per-SPEC and release-train tests | `test-release-train` | Maintain train state and run due checkpoints |
| Final repository synchronization | `commit-n-push` | Run last and require local/remote equality |

Do not duplicate `grill-2-tickets` questions, invent a missing issue tree, or replace
its structured evidence with a controller summary. Do not implement product/test code,
create tickets, or create a substitute planning task in this controller. The planning
Skill owns the planning content; this Skill is only its thin lifecycle wrapper.

## Fixed topology and defaults

Use no confirmation checkpoints for viable choices under the standing authorization
above. The standing Grill response is the
compact Chinese command `全部采用推荐选项/答案`; send it to the same Grill context
and do not repeat the full question set in the controller. `to-spec` and `to-tickets`
are auto-approved through the planning protocol. Missing preferences use repository
and protocol defaults; missing authority or external state is a blocker, not a reason
to simulate evidence.

Before dispatching any new planning, SPEC, or repair thread, execute the [thread registry
reconciliation gate](references/thread-registry.md). Dispatch is allowed only after
every run-owned thread is accounted for, every eligible thread has archive readback,
and no unverified or orphan entry needs recovery.

Create exactly one planning run for the requirement: a dedicated Grill executor followed
by one planning-publication executor. Do not create a second monitor or substitute
planner. Then create exactly one implementation task per child SPEC. A SPEC task owns all tickets belonging to that SPEC. Never create
one task, thread, worktree, branch, or PR per ticket. Keep only one SPEC implementation
task active; archive it after independent verification and merge before dispatching the
next SPEC from the resulting default branch. Repair and read-only review tasks are
role-limited helpers and never own SPECs or tickets.

## Controller state and lifecycle

At bootstrap, resolve [protocols and defaults](references/protocols-and-defaults.md),
select and probe the task backend, create a `run_id`, and read the
[controller-state contract](references/controller-state.md). Persist all state under
`.scratch/<initiative>/`, including the delivery map, task tree, controller state, and
task census. Maintain a dedicated `thread-registry.json` beside those artifacts. Read
the [thread registry contract](references/thread-registry.md) at startup and recovery.
On resumption, reconnect to recorded task IDs and execute the persisted `next_action`;
never reconstruct progress from chat memory or create a duplicate task because a prior
task is slow.

The lifecycle is:

```text
bootstrap
 -> planning via grill-2-tickets
 -> verify/archive planning
 -> SPEC-1 task -> verify/merge/archive
 -> due L4 checkpoint
 -> SPEC-2 task -> verify/merge/archive -> ...
 -> final release-train gate
 -> build/package/deploy/smoke
 -> commit-n-push
 -> terminal validation
```

The machine state is `active` until every required gate passes. It has exactly one
executable `next_action` and one active leaf. A child `final` is a handoff claim, never
a user-facing final and never proof of archival. The required child transition is:

```text
active/paused -> handoff_received -> verified -> archived
```

For every child, perform this closure without emitting an intervening final:

1. Persist `handoff_received` and the exact next action.
2. Independently verify artifacts, tests, tracker state, route receipt, and repository state.
3. Archive the exact task through the task backend and read its archival state back.
4. Persist `archived`, refresh task tree/census and hashes, run the active validator, then continue.

An idle, completed, `notLoaded`, closed GitHub issue, clean worktree, commit, test
result, or archive call without readback is not archival evidence. Before terminal
success, every controller-owned child must be present in a fresh host census, have a
successful archive readback, and appear exactly once in the persisted projections.

## Supervision and recovery

While state is `active`, use compact task snapshots at least once per minute and perform
the persisted action in the same controller turn. Do not end a turn after a progress
paragraph, an unchanged wait, or “正在处理”; never wait for the user to say “继续”.
Active controller commentary is concise Chinese; commands, identifiers, paths, logs,
and protocol field names may remain unchanged.

If two bounded observations show no new tool marker or external-state change, treat the
task as suspected stalled. Resume the same task once with a focused instruction. If it
still does not advance, invoke `unblock-development` for the exact failed probe, verify
and archive the repair task, then resume the recorded action in the original task. A
blocker repair cannot own the issue tree, SPEC, ticket, merge, or release candidate.
When a phase appears skipped, return to the last verified boundary and repair or resume
that task; never publish a retrospective claim and proceed.

## Phase routing

Read only the reference needed for the current phase:

- Planning: read [planning](references/planning.md). It delegates the complete Grill-to-tickets workflow to `grill-2-tickets`, including fresh run evidence, retries, duplicate convergence, Parent readbacks, route prediction, and issue-tree validation.
- SPEC delivery: read [SPEC delivery](references/spec-delivery.md). It defines one whole-SPEC task, route readback, implementation handoff, selected tests, merge, and archival.
- Blocker, checkpoint, release, or deployment: read [release and recovery](references/release-and-recovery.md). It delegates blocker repair and test/release policy while retaining controller ownership of phase transitions.
- Startup, recovery, or protocol refresh: read [protocols and defaults](references/protocols-and-defaults.md) and [controller state](references/controller-state.md).
- Controller state or terminal behavior changes: read [behavioral acceptance](references/behavioral-acceptance.md) before accepting evidence.

At each transition require artifact and readback evidence, not narrative status:

| Transition | Minimum gate |
| --- | --- |
| bootstrap → planning | protocol registry, run ID, backend evidence, planning route receipt |
| planning → implementation | empty Grill frontier, umbrella/child SPECs, tickets, Parent readbacks, routes, validated planning handoff |
| SPEC → next SPEC | all tickets closed, required tests green, merge reachable from default branch, child verified and archived |
| every 10 SPECs | exact ordered ten-SPEC set and green L4 checkpoint |
| final tail → release | final-tail checkpoint green and no active/unverified/unarchived child |
| release → success | final tests, artifact checksum, deployment/smoke evidence, commit-n-push equality, terminal receipt |

## Terminal interlock

Never emit a natural-language final while `controller_state` is `active`. Before any
terminal response, refresh the task census and run the terminal validator with the
current state, delivery map, task tree, census receipt, run ID, proposed terminal state,
and goal status. Only a fresh `decision: allow` authorizes `terminal_success`,
`terminal_blocked`, or `user_stopped` for that exact state. A rejected terminal check
restores `active` and executes its returned `next_action`.

Terminal success additionally requires:

```text
discovered_controller_tasks == recorded_child_tasks
recorded_child_tasks[*].lifecycle == archived
task_census[*].archive_readback_evidence is non-empty
unverified_handoffs == []
unarchived_tasks == []
```

Use [commit-n-push](../commit-n-push/SKILL.md) as the final mutation phase after release
verification. It must fetch and normally merge each unambiguous upstream, revalidate
content-bearing merges, push, fetch again, and prove local HEAD equals the remote-
tracking HEAD. A failed gate, missing readback, persistent blocker, or unequal remote
state keeps the controller active or enters the documented blocked state; it never
authorizes a premature final.
