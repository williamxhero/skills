# Protocol resolution and defaults

Read this reference at controller startup and whenever a suspended run resumes.

## Source protocols

Resolve every protocol before its phase begins. The current session's Available Skills catalog is the first lookup surface, not an availability boundary: Ask Matt intentionally marks user-invoked skills such as `to-tickets` and `implement-spec` with `disable-model-invocation: true`, so their absence from that catalog is expected.

Use this resolution order:

1. Use the canonical-name entry in the current session's Available Skills catalog when present.
2. Locate the package lock by checking the parent of each advertised `.agents/skills` root, then the configured agent home, then the user's `.agents/.skill-lock.json`. When `skills.<canonical-name>` exists, resolve `<lockfile-directory>/skills/<canonical-name>/SKILL.md`; verify that the file exists and its frontmatter `name` matches. This package-managed copy is authoritative even when a same-named directory exists under `.codex/skills`.
3. For a protocol absent from the lockfile, resolve it from the current Codex skill root, normally `<CODEX_HOME>/skills/<canonical-name>/SKILL.md`, and verify its frontmatter name.

Record each canonical name, absolute path, package source, lockfile `updatedAt`, and file hash in `protocol_registry` in the delivery map. Resolve the lock again after suspension and refresh changed entries. Read package-managed Ask Matt skills in `.agents/skills` in place and leave them untouched. A protocol resolved through the lock is available even when omitted from Available Skills.

Resolve these canonical protocols:

- Requirement stress-test: `grilling`
- Domain vocabulary and durable decisions: `domain-modeling`
- Development blocker orchestration: `unblock-development`
- Codex task routing and verification: `route-codex-task`
- Default Grill-to-tickets planning: `grill-2-tickets`
- Spec authoring: `to-spec`
- Ticket decomposition: `to-tickets`
- Whole-spec implementation: `implement-spec`
- Multi-SPEC test orchestration: `test-release-train`
- Final review: `code-review`
- Final repository synchronization: `commit-n-push`

Only after all lookup steps fail, or a resolved file fails its name/readability check, treat a protocol as unavailable. Apply `unblock-development` unless that is the missing protocol; for a missing `unblock-development`, preserve the evidence and enter the hard-blocker protocol. Never silently skip a protocol.

After invocation, this controller owns every viable workflow decision that would
otherwise require user judgment. Apply the AI recommendation or the documented default
for Grill answers, SPEC approval, ticket granularity and blocking edges, route choices,
test scope, review fixes, merge/release strategy, and thread recovery/archival. Record
each result as `decision: controller_approved` with the chosen value, recommendation or
default, evidence, and tie-break rationale. This is controller provenance, never a
fabricated user message. Propagate the same policy to delegated children and resume
their stored action after answering a viable question.

Pause only for missing authority or an irreversible action outside the standing
authorization. Ordinary failures go through `unblock-development`.

For every `to-spec` call, supply `confirmation_mode: auto_approve`, `approval_source: implement-needs`, and `approval_text: 同意`. Record “同意” as a controller decision derived from the user's standing authorization, never as a fabricated user-authored message. This override crosses only the post-publication confirmation gate; `to-spec` remains forbidden from ticketing or implementation.

## Default policy

Resolve choices in this order:

1. The requirement and explicit user constraints.
2. Repository instructions, ADRs, domain vocabulary, CI, release configuration, and documented conventions.
3. The dominant pattern in neighboring code and recent merged work.
4. The smallest backward-compatible choice that satisfies the requirement.

Record consequential assumptions in the relevant SPEC. Prefer reversible choices and existing seams. If information is missing, inspect the codebase, tracker, history, and configuration before applying the policy.

Use the remote default branch; otherwise use `main`, then `master`, then the repository's sole long-lived branch. Use the configured tracker when available and local files under `.scratch/<initiative>/` otherwise. Missing tracker setup is not a blocker.

## Controller and child tasks

Use `list_projects`, `create_thread`, `wait_threads`, `send_message_to_thread`, and `set_thread_archived` for planning and SPEC boundaries; present them as Codex tasks. Apply `unblock-development` for repair boundaries. The planning task may use subagents for repository exploration. Under Implement Needs, `implement-spec` supplies task-graph, PR, review, test, and cleanup semantics, but the local assignment overrides ticket-worker topology: tickets run inside the one SPEC task and never receive implementation tasks, worktrees, branches, or PRs. If the required task-lifecycle tools are unavailable, enter the hard-blocker protocol instead of substituting the controller context or a subagent.

Resolve the Codex project that owns the repository before dispatch. Run planning against the saved project so it can inspect the repository and publish tracker artifacts while remaining code-read-only. Use a project worktree for repository-scoped code repairs and SPEC implementation; use the saved project directly for host-level, network, credential, or shared-environment repairs. Start each SPEC task from the default-branch state containing every prior merge. Verify and archive planning before the first SPEC. Keep exactly one SPEC child active.

At every planning, SPEC, and repair task boundary, invoke `route-codex-task`. Supply only this workflow's context: planning ownership or SPEC difficulty, reliability and failure cost, reasoning complexity, ambiguity, coupling, search space, verification burden, and the existing owner task ID on recovery. That skill is the single source for allowed pairs, ranks, difficulty floors, exceptional `xhigh` evidence, host capability readback, fallback ordering, applied-settings readback, drift rejection, and receipts.

The complete planning sequence defaults to `gpt-5.6-sol` + `high`; keep `planning_xhigh_evidence` empty. Upgrade the effort to `xhigh` only when the planner records concrete evidence that `high` is inadequate. For each SPEC, preserve the planner's difficulty and `xhigh_evidence` inputs and let `route-codex-task` validate the predicted route. The controller does not reclassify routes. A material SPEC or ticket-graph change returns to the same planner; every recovery reuses the existing owner task and exact persisted receipt.
