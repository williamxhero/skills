---
name: unblock-development
description: 'Keep software delivery moving by isolating any persistent development blocker in a dedicated Codex repair task, routing model and reasoning effort by difficulty, verifying the original failure is fixed, archiving the repair task, and resuming the parent task at the exact blocked step. Use when the user or an authorized parent workflow requests automatic repair tasks during implementation, testing, Git/GitHub, CI, merge, build, release, deployment, tooling, network, permissions, credentials, or environment work.'
---

# Unblock Development

Keep the parent development task in control while a fresh repair task removes each blocker. The repair task owns only the blocker; the parent owns the original outcome and resumes immediately after independent verification.

Invocation authorizes creating and archiving dedicated Codex repair tasks within the parent task's existing scope. It does not authorize unrelated product work, new credentials, bypassed access controls, weakened TLS or checks, destructive recovery, or broader infrastructure changes.

Resolve and read the `diagnosing-bugs` skill from the current session's Available Skills catalog before diagnosing a blocker. If it is unavailable, preserve the evidence and treat that as a hard blocker rather than improvising a weaker protocol.

## 1. Detect a blocker

Identify the next concrete completion criterion and permit one immediate retry when the failure is plausibly transient. Enter this workflow when the retry remains red or an unavailable capability objectively prevents the next step.

Treat all phases as eligible: coding, tests, dependencies, local tools, Git and GitHub, network and DNS, CI, merge conflicts, build, packaging, release, deployment, permissions, credentials, services, and shared environments. `gh` failing to reach GitHub is a blocker; a product test failure is also a blocker when fixing it would distract the parent from its assigned scope.

Keep small, local, well-understood corrections in the parent when they are part of its normal implementation loop. Open a repair task when diagnosis is uncertain, the issue crosses systems or permissions, the fix belongs outside the parent's scope, or continuing in the parent would consume substantial context.

## 2. Freeze the parent and build a blocker packet

Pause the blocked operation without closing or finalizing the parent task. Keep the parent state active and record an exact resume point.

Capture a redacted blocker packet containing:

- blocker ID and stable fingerprint;
- parent task ID, project, repository, branch, worktree, host, and blocked phase;
- intended outcome and exact next step;
- failing command or probe and observed output;
- expected green signal;
- attempts already made and their results;
- suspected scope, authority available, and safety constraints;
- integration target and evidence required before resuming.

Fingerprint from the probe, symptom, environment, and suspected scope. Reuse an active or verified repair for the same fingerprint instead of opening a duplicate.

## 3. Route model and effort

Inspect the model and reasoning combinations advertised by the target host at task-creation time. Classify the blocker by diagnostic uncertainty, blast radius, reversibility, systems and permissions involved, and verification difficulty.

| Difficulty | Typical blocker | Model class | Reasoning effort |
| --- | --- | --- | --- |
| Easy | Localized known-pattern tool or configuration repair | Fast, economical coding model | `medium` |
| Standard | Unfamiliar but bounded integration, environment, or repository failure | Balanced agentic coding model | `high` |
| Hard | Cross-system networking, permissions, security, concurrency, data integrity, or difficult debugging | Reliable agentic workhorse | `xhigh` |
| Extreme | Multi-repository coordination, fragile production recovery, or irreversible compatibility risk | Strongest reliable model available | Highest supported of `max` or `ultra` |

Use the closest supported model class. If the target effort is unavailable, choose the nearest supported effort at or above it, or the model's highest supported effort. Record the selected `model`, `thinking`, and rationale before dispatch.

## 4. Dispatch one repair task

Use the Codex task-lifecycle tools exposed by the host, including project lookup, task creation, waiting, messaging, and archival. If dedicated task creation is unavailable, return a hard blocker because this workflow requires an isolated task boundary.

Use a project worktree for repository-owned repairs. Use the saved project or blocked host directly only when a tool, network, credential, permission, or shared-environment repair must affect that environment.

Create one task titled `Unblock <ID>: <blocker>`. Keep exactly one active leaf task: the parent and any ancestor repairs remain paused, not closed, while the newest repair runs.

Give the repair task the complete blocker packet, selected integration target, applicable repository instructions, available authority, and these outcome constraints:

- diagnose with `diagnosing-bugs`;
- apply the smallest durable fix;
- avoid taking over the parent's original feature or SPEC;
- prove the packet's original probe green;
- return `completed` with redacted evidence and every commit, PR, configuration, or environment change;
- return `needs_repair` with a new packet when a distinct blocker prevents completion.

Apply repository conventions and the smallest reversible default without asking preference questions. Missing authority or facts are evidence, not preferences.

## 5. Supervise nested blockers

Follow the repair task with compact wait snapshots. Answer preference requests from repository evidence and safe defaults, then continue the same task.

When a repair task returns a distinct `needs_repair` packet, let the controlling parent create one nested repair task through this same workflow. Preserve the paused task stack and keep one active leaf. Never let a repair task silently expand into its own unrelated repair program.

Treat every repair-task final response as a handoff event inside the still-active parent workflow. It never completes the parent.

## 6. Verify and integrate

Treat `completed` as a claim. Re-run the original probe from the blocked parent's environment and verify every repository merge or configuration change independently.

Return failed verification to the same repair task with the red evidence. Open a new task only for a genuinely different fingerprint.

Integrate repository-owned fixes into the narrowest correct target: the active feature or SPEC branch when specific to it, or the default branch when the fix is an independently required prerequisite. Apply host-level fixes only to the blocked environment. Preserve unrelated user state and required security controls.

## 7. Archive and resume

After the original probe is green:

1. Record root cause, repair evidence, model, effort, changed state, integration result, and reuse scope.
2. Archive the repair task and confirm archival.
3. Send the verified result to the paused parent.
4. Resume the parent at the packet's exact next step in the same controller turn.

Use commentary for repair status. A repaired blocker is phase completion, not the parent task's final answer.

## Hard-blocker gate

Stop opening repair tasks for a fingerprint when either completed repair attempts leave the same fingerprint red twice, no red/green probe can be built from available evidence, or the fix requires unavailable authority or external state. Preserve the task stack, record the completed boundary and exact resume action, and report a hard blocker.

Do not classify an unanswered preference as a hard blocker. Apply repository evidence and the smallest safe reversible default.
