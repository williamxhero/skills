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
- Spec authoring: `to-spec`
- Ticket decomposition: `to-tickets`
- Whole-spec implementation: `implement-spec`
- Multi-SPEC test orchestration: `test-release-train`
- Final review: `code-review`

Only after all lookup steps fail, or a resolved file fails its name/readability check, treat a protocol as unavailable. Apply `unblock-development` unless that is the missing protocol; for a missing `unblock-development`, preserve the evidence and enter the hard-blocker protocol. Never silently skip a protocol.

This controller overrides the protocols' human checkpoints: auto-accept every Grill recommendation, infer testing seams, auto-approve each published SPEC, self-approve ticket granularity and blocking edges, fix review findings, and continue without asking the user to choose among viable options.

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

At SPEC entry, use only the Implement Needs policy values: models `gpt-5.6-luna`, `gpt-5.6-terra`, and `gpt-5.6-sol`; reasoning efforts `medium`, `high`, and `xhigh`. Planning uses at least `gpt-5.6-terra` with `xhigh`; for broad cross-repository, migration-heavy, or unusually ambiguous requirements, use `gpt-5.6-sol` with `xhigh`. Lock only advertised, allow-listed pairs and same-or-stronger fallbacks; if the floor has no supported pair, enter blocker handling before creation.

The rank is deterministic and deliberately independent of task wording: fast/economical `gpt-5.6-luna` < balanced `gpt-5.6-terra` < reliable-workhorse `gpt-5.6-sol` by policy capability tier, and `medium` < `high` < `xhigh` by reasoning budget. A fallback must be different and at least the recommendation's rank on both axes, and it always resumes the existing SPEC owner task. Record the actual selected pair from readback—not merely the requested pair—before dispatch or recovery.

The planning task locks one advertised `model` and `thinking` pair for each SPEC after producing its ticket graph:

| Difficulty | Typical shape | Model | Reasoning effort |
| --- | --- | --- | --- |
| Easy | Localized, known-pattern change | `gpt-5.6-luna` | `medium` |
| Standard | Several modules or an unfamiliar bounded integration | `gpt-5.6-terra` | `high` |
| Hard | Cross-cutting behavior, migrations, concurrency, security, or performance | `gpt-5.6-terra` | `xhigh` |
| Extreme | Multi-repository or unusually fragile compatibility/release constraints | `gpt-5.6-sol` | `xhigh` |

Record the exact recommendation, difficulty, rationale, and concrete same-or-stronger fallback pairs in the planning record. The controller does not reclassify it. A material SPEC or ticket-graph change invalidates the recommendation and must be returned to the same planning task; never create a second planner. Let `unblock-development` route actual repair tasks independently.
