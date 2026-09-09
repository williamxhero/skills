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

Use `list_projects`, `create_thread`, `wait_threads`, `send_message_to_thread`, and `set_thread_archived` for planning and SPEC boundaries; present them as Codex tasks. Apply `unblock-development` for repair boundaries. The planning task may use subagents for repository exploration, and `implement-spec` may use them for parallel tickets. If the required task-lifecycle tools are unavailable, enter the hard-blocker protocol instead of substituting the controller context or a subagent.

Resolve the Codex project that owns the repository before dispatch. Run planning against the saved project so it can inspect the repository and publish tracker artifacts while remaining code-read-only. Use a project worktree for repository-scoped code repairs and SPEC implementation; use the saved project directly for host-level, network, credential, or shared-environment repairs. Start each SPEC task from the default-branch state containing every prior merge. Verify and archive planning before the first SPEC. Keep exactly one SPEC child active.

Planning uses at least a reliable high-intelligence agentic model with `xhigh` effort. For broad cross-repository, migration-heavy, or unusually ambiguous requirements, use the strongest reliable model with the highest supported effort of `max` or `ultra`. If unavailable, choose the closest advertised model class and the highest supported effort at or above `high`. Record the selection and rationale before creation; never route planning to the fast/economical class.

The planning task locks one advertised `model` and `thinking` pair for each SPEC after producing its ticket graph:

| Difficulty | Typical shape | Model class | Reasoning effort |
| --- | --- | --- | --- |
| Easy | Localized, known-pattern change | Fast, economical coding model | `medium` |
| Standard | Several modules or an unfamiliar bounded integration | Balanced agentic coding model | `high` |
| Hard | Cross-cutting behavior, migrations, concurrency, security, or performance | Reliable agentic workhorse | `xhigh` |
| Extreme | Multi-repository or unusually fragile compatibility/release constraints | Strongest reliable model | Highest supported of `max` or `ultra` |

Record the exact recommendation, difficulty, rationale, and fallback class in the delivery map. The controller does not reclassify it. If the exact pair is unavailable, choose the nearest advertised pair in the same or stronger class and record the substitution. A material SPEC or ticket-graph change invalidates the recommendation and requires a verified, archived high-reasoning planning revision. Let `unblock-development` route actual repair tasks independently.
