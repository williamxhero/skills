---
name: implement-needs
description: 'Autonomously orchestrate a broad software requirement end to end: default-grill it, partition it into scoped specs, create tickets, launch difficulty-routed Codex tasks for each spec and any blocker, merge, then build and deploy. Use when the user explicitly requests this no-confirmation controller workflow or says "implement needs".'
---

# Implement Needs

## Controller Kernel (authoritative)

The invoking task is the **controller**. This kernel is the single authority for controller state, message phase, supervision, recovery, child handoffs, and terminal completion. Phase references may specialize delivery work but may not redefine these rules.

At startup, resolve the source protocols, create or recover the delivery map, and read [the controller-state contract](references/controller-state.md) before dispatching work. Persist its machine-readable sibling as `.scratch/<initiative>/controller-state.json`, plus the current task-tree snapshot used by the terminal gate. On every resumed invocation, resolve protocols again, read these artifacts, reconnect to recorded child tasks, and execute the stored `next_action` before creating a task. Recovery reuses task IDs; it never reconstructs liveness from chat memory alone.

`controller_state` is exactly one of `active`, `terminal_success`, `terminal_blocked`, or `user_stopped`. Every non-terminal condition maps to `active`, including an executable next action; an active, queued, paused, returned-but-unverified, or unarchived child; a pending SPEC; or incomplete test or release work. Keep the complete active phase, task stack, child lifecycle, pending SPECs, unverified handoffs, unarchived tasks, test state, release state, and exactly one executable `next_action` in controller state.

Treat message phase as control flow. Every non-terminal user message is concise Chinese `commentary`, immediately followed in the same turn by the recorded wait, verification, archival, repair dispatch, or phase action. Answer side questions in Chinese commentary and then resume the same stored action. Exact identifiers, commands, paths, logs, field names, and quoted evidence may remain untranslated. A child final is only a handoff claim: verify it independently, update state, archive or resume that child, and continue the controller loop.

Run this supervision loop while state is `active`:

1. Refresh the delivery map, task-tree snapshot, and controller state after every external observation or mutation.
2. Execute the one recorded `next_action`: wait on active children, verify a handoff, archive a verified child, route a blocker, dispatch the eligible child, or advance the phase.
3. Reconcile every child and pending gate into state, choose one next action, persist it, and repeat.

Before any proposed controller `final_answer`, refresh both fingerprinted source artifacts and run the deterministic gate:

```text
python <implement-needs>/scripts/validate_controller_terminal.py --state .scratch/<initiative>/controller-state.json --delivery-map .scratch/<initiative>/delivery-map.md --task-tree .scratch/<initiative>/task-tree.json --expected-run-id <run-id> --proposed-state <terminal_success|terminal_blocked|user_stopped> --goal-status <unchanged|complete|blocked> --receipt .scratch/<initiative>/terminal-receipt.json
```

Only exit code `0` with `decision: allow` authorizes that exact terminal state. Cite the receipt and terminal evidence in the Chinese final. A rejection fails closed: keep or restore `controller_state: active`, emit commentary, execute the returned `next_action`, and rerun the gate only after state changes. Use the same receipt to set a goal `complete` only for `terminal_success` and `blocked` only for `terminal_blocked`; `user_stopped` leaves goal status unchanged.

Deliver without interviews or approval checkpoints. Invocation authorizes the Codex child-task lifecycle and normal repository/configured-deployment mutations required by this workflow: task creation and archival, branches, tracker items, commits, pull requests, merges, builds, releases, and deployment. It does not authorize invented credentials, bypassed access controls, weakened required checks, or destructive recovery outside the requirement. Apply defaults for viable choices; only validator-approved success, an objectively exhausted blocker, or an explicit user stop may end the controller turn.

## Phase router

- **Startup or protocol refresh:** read [protocol resolution and defaults](references/protocols-and-defaults.md) before resolving dependencies, choosing projects, or dispatching children.
- **Grill, SPECs, or tickets:** resolve and apply `grill-2-tickets`, then read [planning](references/planning.md) for Implement Needs routing and release-train extensions.
- **SPEC implementation:** read [spec delivery](references/spec-delivery.md) before creating, supervising, verifying, or archiving an implementation task.
- **Blockers, test checkpoints, or release:** read [release and recovery](references/release-and-recovery.md) when a blocker appears, a test-train gate is due, or all SPECs are merged.
- **Controller protocol changes:** read [behavioral acceptance](references/behavioral-acceptance.md) before accepting regression or forward-test evidence.

Preserve unrelated user changes. Work from isolated branches or worktrees when the current tree is dirty. Never fold pre-existing changes into the delivery.
