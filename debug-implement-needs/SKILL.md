---
name: debug-implement-needs
description: "Diagnose and repair a stalled or failed implement-needs task when the user supplies a target task/thread ID; reproduce the Skill bug in qualification, fix and requalify it, then resume the target task."
---

# Debug Implement Needs

Use this skill for an explicit request such as:

```text
/debug-implement-needs codex://threads/<target-thread-id>
```

The target is a Codex task/thread, not a GitHub issue. Treat the supplied URL as an input locator only. The formal task identity comes from live task readback and must include the native thread ID, host, task/run/attempt identity when managed, cwd, and non-empty project identity. A URL, request ID, title token, client ID, or display title is never sufficient identity.

This is a repair loop with a hard evidence gate:

```text
inspect target -> establish red repro -> extend qualification -> run red
-> patch implementation -> run green repro -> run whole qualification
-> independent verify -> register only QUALIFIED -> resume target -> read back
```

## 1. Inspect the target and establish scope

Announce that `diagnosing-bugs` is being used for the failure analysis and read its
`SKILL.md` before changing code. Read the current `implement-needs/SKILL.md`,
`implement-needs/validation/README.md`, and the relevant managed-recovery reference
before changing qualification or recovery code.

Resolve the supplied `codex://threads/...` locator to its thread ID, then use the
Codex task tools to obtain:

- the target's current status and latest completed/in-progress turn;
- recent assistant messages, tool calls, errors, waits, and empty turns;
- formal task identity and host;
- the project/cwd and the managed run ID, spec, ticket, and attempt if present;
- the current applied route when available.

If the target is active, inspect it without sending a second repair prompt until the
current turn's state is understood. If it is `notLoaded`, `completed` with no useful
handoff, capacity-blocked, or waiting indefinitely, record the exact stop symptom.
Do not infer the cause from the title. Separate:

1. a backend/provider problem (capacity, unavailable model, transport failure);
2. a controller/recovery problem (stale action, lost handoff, missing reconciliation,
   retry budget exhaustion, archive not read back); and
3. a Skill/qualification defect (a state is incorrectly classified or a cleanup gate
   is missing).

Completion criterion: a written incident record identifies the target formal thread
ID, the observed stop symptom, the relevant run/spec/action, and a falsifiable
hypothesis. If the target cannot be read back, stop with a blocker; never replace it
with a new task.

## 2. Build the red feedback loop before the fix

Use the smallest correct seam in the real implementation. Prefer a regression test
or the existing validation harness; use a live task readback/replay when the failure
depends on Codex lifecycle behavior. The loop must assert the exact symptom, for
example:

- a `completed` turn whose `items`/`output`/`message`/`result` are empty is not
  accepted as successful handoff;
- a child task that completed external work is archived and archive-read back before
  its parent advances;
- a stale controller action is reconciled from verified external side effects;
- a failed/replaced attempt cannot leave an unarchived run-owned task.

Run the red loop before editing the fix and preserve its output as incident evidence.
If the live provider is nondeterministic, retain the real captured event/readback and
make the local replay deterministic; label live evidence and replay evidence
separately. A fixture or fake backend may exercise a pure state-machine seam, but it
cannot be reported as live GitHub/task evidence or replace a required live readback.

Extend the applicable qualification scenario and verifier so the bug is a required
gate, not an optional note. For `whole-spec-v1` with the `thread` backend, use the
existing files under `implement-needs/validation/` and its scripts, including the
scenario, `qualification.py`, recovery harness, `run_qualification.py`,
`verify_qualification.py`, and `register_qualification.py`. Preserve the real
repository and run-owned resources. Do not create a new qualification repository.

The qualification evidence must identify, as applicable:

- real GitHub umbrella/child SPECs, tickets, parent relationships, blocked-by edges,
  issue state readbacks, branches, commits, merged PRs, and repository sync;
- real managed task IDs with formal identity, non-empty project ID, route readback,
  executed turn, completion/wait readback, archive request, and `archived: true`
  archive readback;
- the exact bug detection, controller action/recovery execution, external side-effect
  reconciliation, and cleanup readback;
- test, release, restore/recovery, and final frontier receipts.

Do not turn a previous incident into evidence that a new run generated the same event.
Cross-run incident evidence may document historical reproduction, but the report must
say so explicitly. If the scenario contract requires same-run live reproduction and
that evidence is absent, leave the result `REJECTED` and continue debugging rather
than claiming qualification.

Completion criterion: the qualification or regression command has been run once and
is red on the precise bug, with a saved report/readback that contains no synthetic
identity or fabricated external evidence.

## 3. Fix the implementation and prove the local transition

Generate 3–5 ranked, falsifiable hypotheses from the red trace, then test the cheapest
distinguishing boundary first. Keep debug instrumentation tagged and remove it before
completion. Make the smallest implementation change that preserves controller
idempotency and recovery semantics. Typical fixes include:

- classifying an empty completed turn as `UNCERTAIN`, requiring history/side-effect/
  handoff reconciliation before success;
- making controller reconciliation consume verified GitHub/task/archive readbacks;
- terminalizing or replacing an interrupted attempt only after archive readback;
- clearing stale recovery state through a formal controller operation that records a
  business event, rather than directly editing SQLite.

Write or update the regression test at the seam that reaches the failure. Run the red
test before the patch, apply the patch, then run it green. Run the original
unminimized reproduction again. Run targeted tests, validation tests, and Python
compile checks proportionally to the files changed.

Completion criterion: the same command that went red in step 2 now goes green, the
original trace no longer produces the bug, and no debug marker or throwaway prototype
remains.

## 4. Requalify and register only after independent verification

Run the full qualification for the actual scenario/backend and repository. For the
standard case, use `whole-spec-v1` and `thread`, and pass the formally read-back
project identity and backend receipt required by `run_qualification.py`. Preserve
failed qualification reports; they are useful evidence and must not be overwritten by
an assertion of success.

Then run `verify_qualification.py` as a separate process against the generated
`qualification.json`. Registration is permitted only when that independent result is
exactly `QUALIFIED` and has matching scenario, skill digest, harness digest, backend
kind, and contract version. Only then run `register_qualification.py` against the
qualification index and read the new index entry back.

If verification is `REJECTED`, fix the missing evidence or implementation and repeat
from the relevant step. If the same external blocker persists, retain the rejected
report and state the blocker; do not register it or mark the task repaired.

Completion criterion: the independent verifier returns `QUALIFIED`, registration
readback matches the report, and the qualification's cleanup receipt confirms every
run-owned Issue/task/thread resource is reconciled.

## 5. Resume the original task safely

Before sending work, read the target again because its state may have changed during
qualification. Reconcile the controller's actual DB state with live GitHub/task
readbacks. Do not recreate a completed SPEC, ticket, branch, PR, or task. If a stale
recovery action blocks progress, use the controller's formal reconciliation API/CLI
and record the event and verified readback. Direct SQLite edits are only an emergency
diagnostic, never the normal repair operation.

Send one concise continuation prompt to the original formal thread, naming the exact
next controller action and requiring it to continue the existing run. If the backend
reports capacity, let the task's existing recovery policy handle fallback/retry; do
not create an untracked replacement. Wait with `wait_threads` until the turn reaches
completed/needs-attention, then read the latest turn and controller status. Repeat
the recovery loop while progress is possible.

The target is repaired only when:

- the target is active or has completed a useful continuation turn;
- the controller's `next-action` is a valid unblocked action or the run has reached a
  verified terminal state;
- the parent/child handoff, external side effects, and archive state reconcile;
- no stale recovery action or orphan run-owned task remains; and
- the final task readback proves the continuation belongs to the original formal
  thread/run.

## Final report

Report the target formal thread ID and host, the observed root cause, the red and green
reproduction commands, changed files, test results, qualification and verifier
decisions, registration readback, and the final target-task continuation readback.
Include paths to `qualification.json` and any preserved rejected evidence. For live
qualification, list actual GitHub Issue numbers, merged PR numbers/merge commits,
all run-owned thread IDs, archive readbacks, and cleanup readbacks. If the target is
still blocked, say exactly which verified external condition prevents progress and
leave all failure evidence intact.
