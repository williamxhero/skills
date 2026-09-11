---
name: implement-needs
description: 'Autonomously orchestrate a broad software requirement end to end: default-grill it, partition it into scoped specs, create tickets, launch difficulty-routed Codex tasks for each spec and any blocker, merge, then build and deploy. Use when the user explicitly requests this no-confirmation controller workflow or says "implement needs".'
---

# Implement Needs

## Non-negotiable execution contract

This skill is a controller protocol, not a suggestion list. Execute phases in this order
and fail closed at every boundary:

```text
bootstrap -> planning-child -> Grill -> umbrella-SPEC -> child-SPECs -> tickets
          -> SPEC-thread-1 -> verify/merge/archive -> ...
          -> checkpoint-L4 -> final-gate -> build/package/deploy -> commit-n-push
```

The controller must never implement product code, tests, packaging code, or deployment
changes itself. It may inspect read-only state, persist controller artifacts, dispatch
tasks, answer standing defaults, verify evidence, and perform only the explicitly
controller-owned final release steps. If a required child or artifact is missing, do
not compensate by doing the work in the controller; repair or resume the missing phase.

Before the first product/test mutation, require observable evidence of: a completed
Grill with `frontier_empty: true`; one umbrella SPEC; ordered child SPECs; every child
SPEC's GitHub Parent issue read back to the umbrella; every child ticket's Parent issue
read back to its child SPEC; one validated route receipt per child SPEC; and a validated
release-train record. Missing, inferred, or prose-only evidence fails the gate.

Before every implementation mutation, require a fresh task created specifically for the
next child SPEC, a validated route readback for that exact task ID, and a persisted
`decision: allow` receipt. The task owns the whole SPEC and all its tickets. No
ticket-level implementation task/thread/worktree/branch/PR may be created. Keep exactly
one SPEC implementation child active; archive it only after independent verification
and merge, then create the next child from the resulting default branch.

The controller has exactly one active leaf. A child final is never a user-facing final:
move it to `handoff_received`, verify it, archive it, update state, and continue the
recorded action. A progress summary, idle child, failed tool, token pressure, or “继续”
is not evidence that a phase is complete.

If any phase appears skipped, reconstruct missing evidence from the same task where
possible. Otherwise return to the last verified boundary and resume or repair that
task. Never publish a retrospective claim and proceed.

## Turn-exit interlock

Treat `final` as a privileged state transition, never as a progress-message channel. While `controller_state` is `active`, emit progress only as Chinese commentary and execute the persisted `next_action` in the same turn. After every state write and every child observation, run `scripts/validate_controller_active.py`; only exit code `0` permits the next wait, verification, archival, repair, dispatch, or phase action. A failed active-state gate is itself the next repair action.

Before emitting any `final`, the immediately preceding tool result must be a fresh `validate_controller_terminal.py` receipt with `decision: allow` for the exact terminal state and current artifact hashes. No receipt means continue supervision. Elapsed time, many Grill rounds, a child final, an idle child, a progress summary, or token pressure never authorizes turn exit.

While active, do not end a turn after commentary. Perform the recorded next action in
that same turn: send the compact default answer, wait on the child, verify its handoff,
archive it, dispatch the next child, or route a blocker. During active waits, use compact
task snapshots at least once per minute; persist each snapshot and immediately issue
the next recorded action. Never wait for the user to say “继续”.

## Controller Kernel (authoritative)

The invoking task is the **controller**. This kernel is the single authority for controller state, message phase, supervision, recovery, child handoffs, and terminal completion. Phase references may specialize delivery work but may not redefine these rules.

At startup, resolve the source protocols, create or recover the delivery map, and read [the controller-state contract](references/controller-state.md) before dispatching work. Persist its machine-readable sibling as `.scratch/<initiative>/controller-state.json`, plus the current task-tree snapshot used by the terminal gate. On every resumed invocation, resolve protocols again, read these artifacts, reconnect to recorded child tasks, and execute the stored `next_action` before creating a task. Recovery reuses task IDs; it never reconstructs liveness from chat memory alone.

`controller_state` is exactly one of `active`, `terminal_success`, `terminal_blocked`, or `user_stopped`. Every non-terminal condition maps to `active`, including an executable next action; an active, queued, paused, returned-but-unverified, or unarchived child; a pending SPEC; or incomplete test or release work. Keep the complete active phase, task stack, child lifecycle, pending SPECs, unverified handoffs, unarchived tasks, test state, release state, and exactly one executable `next_action` in controller state.

Treat message phase as control flow. Every non-terminal user message is concise Chinese `commentary`, immediately followed in the same turn by the recorded wait, verification, archival, repair dispatch, or phase action. Answer side questions in Chinese commentary and then resume the same stored action. Exact identifiers, commands, paths, logs, field names, and quoted evidence may remain untranslated. A child final is only a handoff claim: verify it independently, update state, archive or resume that child, and continue the controller loop.

Run this supervision loop while state is `active`:

1. Refresh the delivery map, task-tree snapshot, and controller state after every external observation or mutation.
2. Execute the one recorded `next_action`: wait on active children, verify a handoff, archive a verified child, route a blocker, dispatch the eligible child, or advance the phase.
3. Reconcile every child and pending gate into state, choose one next action, persist it, and repeat.

At every phase transition, persist and validate this evidence before advancing:

| Transition | Required evidence |
| --- | --- |
| bootstrap -> planning | protocol registry, run ID, controller state, planning route receipt |
| planning -> implementation | empty Grill frontier, umbrella/child SPECs, Parent issue readbacks, tickets, child routes, planning handoff receipt |
| implementation -> next SPEC | all tickets closed, SPEC tests green, merge reachable from default branch, child independently verified and archived |
| ten SPECs -> checkpoint | exact ten ordered child SPEC IDs and candidate revisions, L4 green |
| all SPECs -> release | final tail checkpoint green, no active/unverified/unarchived child, exact release candidate |
| release -> terminal success | final tests, artifact checksum, deployment/smoke evidence, commit-n-push local/remote equality, terminal receipt |

Never advance based on narrative text such as “规划完成” or “已实现”; the artifact
and readback must exist and validate.

Do not leave a child frontier parked. When a Grill child returns questions, reply in the controller only with `全部采用推荐选项/答案`, send that same compact command to the child without repeating its frontier, persist acceptance and `next_action: wait`, run the active-state gate, and call the task wait operation in that same controller turn.

Before any proposed controller `final_answer`, refresh both fingerprinted source artifacts and run the deterministic gate:

```text
python <implement-needs>/scripts/validate_controller_terminal.py --state .scratch/<initiative>/controller-state.json --delivery-map .scratch/<initiative>/delivery-map.md --task-tree .scratch/<initiative>/task-tree.json --expected-run-id <run-id> --proposed-state <terminal_success|terminal_blocked|user_stopped> --goal-status <unchanged|complete|blocked> --receipt .scratch/<initiative>/terminal-receipt.json
```

Only exit code `0` with `decision: allow` authorizes that exact terminal state. Cite the receipt and terminal evidence in the Chinese final. A rejection fails closed: keep or restore `controller_state: active`, emit commentary, execute the returned `next_action`, and rerun the gate only after state changes. Use the same receipt to set a goal `complete` only for `terminal_success` and `blocked` only for `terminal_blocked`; `user_stopped` leaves goal status unchanged.

Deliver without interviews or approval checkpoints. Invocation authorizes the Codex child-task lifecycle and normal repository/configured-deployment mutations required by this workflow: task creation and archival, branches, tracker items, commits, pull requests, merges, builds, releases, and deployment. It does not authorize invented credentials, bypassed access controls, weakened required checks, or destructive recovery outside the requirement. Apply defaults for viable choices; only validator-approved success, an objectively exhausted blocker, or an explicit user stop may end the controller turn.

When a required protocol is unavailable, a child cannot be created, GitHub cannot be
reached, a route cannot be read back, or a gate cannot run, invoke `unblock-development`
with the exact failed probe. Do not skip the phase, manually simulate its artifacts, or
continue direct implementation in the parent.

## Phase router

- **Startup or protocol refresh:** read [protocol resolution and defaults](references/protocols-and-defaults.md) before resolving dependencies, choosing projects, or dispatching children.
- **Any Codex task route:** resolve and invoke `route-codex-task`; keep its policy, capability/readback gate, fallback rules, and receipt as the routing authority.
- **Grill, SPECs, or tickets:** resolve and apply `grill-2-tickets`, then read [planning](references/planning.md) for Implement Needs routing and release-train extensions.
- **SPEC implementation:** read [spec delivery](references/spec-delivery.md) before creating, supervising, verifying, or archiving an implementation task.
- **Blockers, test checkpoints, or release:** read [release and recovery](references/release-and-recovery.md) when a blocker appears, a test-train gate is due, or all SPECs are merged. After deployment and smoke verification, apply `commit-n-push` as the mandatory final synchronization phase before terminal success.
- **Controller protocol changes:** read [behavioral acceptance](references/behavioral-acceptance.md) before accepting regression or forward-test evidence.

Preserve unrelated user changes. Work from isolated branches or worktrees when the current tree is dirty. Never fold pre-existing changes into the delivery.
