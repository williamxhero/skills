---
name: implement-needs
description: 'Autonomously orchestrate a broad software requirement end to end: default-grill it, partition it into scoped specs, create tickets, launch difficulty-routed Codex tasks for each spec and any blocker, merge, then build and deploy. Use when the user explicitly requests this no-confirmation controller workflow or says "implement needs".'
---

# Implement Needs

## Task/thread backend selection

Resolve the child-task backend at bootstrap and persist the result in the controller
state. Use this order: Host Task API, configured MCP/task connector, then the local
`codex app-server --stdio`. The absence of a Task API is not a blocker. If the local
app-server exists and passes its capability probe, it is mandatory to use it for child
creation, supervision, readback, and lifecycle evidence. Only after all three backends
fail may the controller invoke `unblock-development`.

Never implement product code in the controller because a task API is missing, and never
invent task, route, turn, or archive evidence. Read [the app-server protocol](references/app-server.md)
and run `scripts/probe_codex_app_server.ps1` before selecting the fallback. Record the
selected backend with `scripts/select_task_backend.py`; a repair selection is the only
valid outcome when all probes fail.

The backend is an implementation detail; all children use the same route receipt,
controller-state, task-tree, task-census, handoff, and archive-readback contracts. An
app-server thread without a native archive operation is archived only when its completed
turn, independent verification, final thread readback, and persisted controller archive
record all exist. Do not label a completed turn as a Host Task API archive.

## Non-negotiable execution contract

This skill is a controller protocol, not a suggestion list. Execute phases in this order
and fail closed at every boundary:

```text
bootstrap -> Grill-child -> archive -> planning-child -> umbrella-SPEC -> child-SPECs -> tickets
          -> SPEC-thread-1 -> verify/merge/archive -> ...
          -> checkpoint-L4 -> final-gate -> build/package/deploy -> commit-n-push
```

The controller must never implement product code, tests, packaging code, or deployment
changes itself. It may inspect read-only state, persist controller artifacts, dispatch
tasks, answer standing defaults, verify evidence, and perform only the explicitly
controller-owned final release steps. If a required child or artifact is missing, do
not compensate by doing the work in the controller; repair or resume the missing phase.

Before the first product/test mutation, require observable evidence of: a completed
dedicated Grill child with `frontier_empty: true` and archive readback; a separate
planning child handoff; one umbrella SPEC; ordered child SPECs; every child
SPEC's GitHub Parent issue read back to the umbrella; every child ticket's Parent issue
read back to its child SPEC; one validated route receipt per child SPEC; and a validated
release-train record. Missing, inferred, or prose-only evidence fails the gate.

Before every implementation mutation, require a fresh task created specifically for the
next child SPEC, a validated route readback for that exact task ID, and a persisted
`decision: allow` receipt. The task owns the whole SPEC and all its tickets. No
ticket-level implementation task/thread/worktree/branch/PR may be created. Keep exactly
one SPEC implementation child active; archive it only after independent verification
and merge, then create the next child from the resulting default branch.

The controller has exactly one active leaf. Grill and SPEC/ticket planning are separate
children with separate route receipts and archive readbacks. A child final is never a user-facing final:
move it to `handoff_received`, verify it, archive it, update state, and continue the
recorded action. A progress summary, idle child, failed tool, token pressure, or “继续”
is not evidence that a phase is complete.

For the planning segment, `grill-2-tickets` is authoritative and Implement Needs is a
thin orchestration wrapper. Do not duplicate its Grill questions, synthesize a missing
issue tree, or forward the planning assignment through a second monitor task. The
controller supplies the standing default acceptance, supervises the exact executor,
verifies fresh run-scoped artifacts and GitHub readbacks, archives the executor, and
then advances. If the executor stalls or a tracker operation fails, use the recorded
resume/blocker path; never convert an unchanged status paragraph into a handoff.

### Mandatory child-handoff closure

When any planning, SPEC, or repair child returns a final/handoff, the controller's next
operation is always the closure sequence below. Do not send a prose summary or `final`
between these operations:

1. Persist the child as `handoff_received` and persist the exact next action.
2. Run the active-state validator.
3. Independently verify the handoff's claimed artifacts, tests, tracker state, route
   receipt, and repository state from the controller environment.
4. If and only if verification passes, archive the child through the task tool and read
   back archival.
5. Persist the child as `archived`, refresh the task tree and hashes, run the active-state
   validator again, and execute the next action.

For a SPEC child, “commit created”, “tests passed”, or “child says complete” never closes
the SPEC. The mandatory remaining sequence is: verify every ticket -> verify required
tests -> verify merge reachable from the default branch -> archive child -> update train
-> run due L4/checkpoint -> dispatch the next SPEC. If any item is missing, the state
remains `active` and the only valid output is Chinese commentary followed by the next
wait, verify, repair, archive, or dispatch operation.

Treat a controller final immediately after a child final, before this closure sequence
and a terminal validator receipt, as a protocol violation. Before every natural-language
final, inspect the persisted state: if it is `active`, use commentary and perform its
recorded action; never convert the handoff into a final merely because the child became
idle or the current turn has accumulated a long history.

### Codex task census and archival gate

GitHub Issue closure and Codex task archival are separate facts. Before terminal release
or terminal success, enumerate the task tree from the host task API, filter every task
whose delegation provenance names this controller run, and compare that inventory with
`child_tasks` and `role_limited_tasks`. Missing task IDs, unexplained extra tasks, or a
status other than archived fails closed.

For every discovered child, record an explicit archival operation result and a fresh
post-archive readback. Do not infer archival from `completed`, `idle`, `notLoaded`, a
closed GitHub Issue, a clean worktree, or a child message saying “已归档”. The terminal
evidence must contain one successful archive readback per planning, SPEC, and repair
task, including tasks that failed to start or were replaced after a blocker. A task
created during this run but absent from controller state is an immediate state-repair
blocker, not an ignorable orphan.

The final census must satisfy:

```text
discovered_controller_tasks == recorded_child_tasks
recorded_child_tasks[*].lifecycle == archived
task_census[*].archive_readback_evidence is non-empty
unverified_handoffs == []
unarchived_tasks == []
```

### Mandatory external closeout protocol

Use this exact closeout sequence for every planning, SPEC, and repair child:

```text
receive handoff
-> independently verify
-> call archive for the exact task ID
-> read that task ID back from the host
-> require archived=true or the backend's documented equivalent
-> record operation and readback in task-census
-> refresh task-tree from the host
-> update controller-state and unarchived_tasks
-> run validate_task_census.py with state + task-tree + census
-> only after allow dispatch the next child or run terminal validation
```

Never write `lifecycle: archived` before the host readback proves archival. Child final,
turn completion, idle status, GitHub issue closure, clean worktree, commit, test result,
or an archive call without readback is not archival evidence. A failed archive call,
missing readback, stale task-tree, or census mismatch keeps the controller `active` and
sets the next action to archive or repair.

The final host enumeration must contain the run ID, task ID, task kind, host ID, observed
status, archive result, post-archive readback, and observation timestamp. Compare its
child set with both persisted projections. Extra tasks are orphan repairs; missing tasks
are census repairs. The controller task is excluded only when explicitly identified as
the controller and is never recorded as a child.

Before any final, require fresh task-census `allow`, terminal-validator `allow`, and a
final host task readback in the same turn. The terminal command must pass
`--task-census-receipt`; omitting it is a workflow error even if compatibility code
accepts the older validator interface.

Persist the census as `.scratch/<initiative>/task-census.json` and run
`scripts/validate_task_census.py` against both the current controller state and the
current task tree, for example with `--state ... --task-tree ... --census ...`. The
census must be built from a fresh host task enumeration, not copied from controller
state. Only an allow receipt permits the controller to close remaining tracker
containers and run the terminal validator. If the host cannot expose archival state or read it back,
invoke `unblock-development`; never claim completion from the GitHub issue graph.

The controller task itself is not a child and must remain outside `child_tasks`; every
task created by this run must be tagged with the run id and appear exactly once in the
host enumeration. Before terminal success, compare host-discovered child IDs,
task-tree IDs, and controller-state IDs. A matching self-written lifecycle field is not
external proof. If any child remains visible as idle, completed, notLoaded, active, or
unarchived after the archive call, terminal success is forbidden and the next action is
archive/readback repair.

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
python <implement-needs>/scripts/validate_controller_terminal.py --state .scratch/<initiative>/controller-state.json --delivery-map .scratch/<initiative>/delivery-map.md --task-tree .scratch/<initiative>/task-tree.json --task-census-receipt .scratch/<initiative>/task-census-receipt.json --expected-run-id <run-id> --proposed-state <terminal_success|terminal_blocked|user_stopped> --goal-status <unchanged|complete|blocked> --receipt .scratch/<initiative>/terminal-receipt.json
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
