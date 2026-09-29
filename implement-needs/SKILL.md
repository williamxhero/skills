---
name: implement-needs
description: 'Start or take over a governed requirement delivery run through the public Spec Runner contract.'
disable-model-invocation: true
---

# Implement Needs

`/implement-needs` is the user-facing entry for continuous development. It
hands one requirement or one explicit existing workflow to the installed Spec
Runner. The Runner owns durable planning, SPEC and ticket ordering,
implementation, tests, review, delivery, cleanup, and the final summary.

## New request

Accept the current conversation, explicit text, or a historical chat reference.
Normalize the source into one UTF-8 brief without inventing requirements. Keep
the repository, configuration, authorized artifact roots, and stable launch key
explicit. Invoke the wrapper:

```powershell
python scripts/spec_runner_handoff.py --brief <brief> --config <runner.json> `
  --control-root <runner-control-root> --launch-key <stable-key>
```

The wrapper invokes only the public Runner CLI and preserves its versioned
response contract. Use the same Runner controls for status, pause, resume,
answers, cancellation, and diagnostics.

The handoff contract is `implement-needs-handoff/v2`, and the installed Runner
CLI contract is `spec-runner-cli/v1`. Validate both at the installed artifact
boundary before starting a run.

## Existing workflow takeover

When the user identifies a GitHub umbrella Issue and an existing workflow,
collect the repository, umbrella Issue, workspace, target ref, authorized
artifact roots, required checks, optional source thread, brief, config, and
stable takeover key. Run discovery first, then apply its immutable snapshot:

```powershell
python scripts/spec_runner_handoff.py `
  --takeover-repository <owner/name> --umbrella-issue <number> `
  --workspace <repository-workspace> --target-ref <target-ref> `
  --control-root <runner-control-root> --takeover-key <stable-key> `
  --brief <brief> --config <runner.json> `
  --artifact-root <authorized-artifact-root> --required-check <check-name>
```

Discovery reads the complete Issue graph, relations, branches, PRs, checks,
receipts, local refs, remote target, and cleanup evidence. It writes a
digest-bound snapshot. The Runner applies that snapshot once and continues the
same durable queue.

The discovery operation is exposed by the public `takeover discover` command.

Takeover skip rules are evidence based. A complete requirement and SPEC graph
skips Grill and to-spec. A complete Issue-backed ticket graph
skips to-tickets.
An existing candidate is reverified. Checks and review are reused only when
bound to that candidate. A merged PR is read back before closure. A SPEC is
completed only after candidate, review, checks, merge, target, Issue closure,
and cleanup evidence all validate. The queue selects the first unfinished
dependency-ready SPEC.

Use the lower-level Runner form only when a previously saved inventory is the
explicit source:

```powershell
python scripts/spec_runner_handoff.py --takeover-file <inventory.json> `
  --control-root <runner-control-root> --takeover-key <stable-key> `
  --brief <brief> --config <runner.json>
```

Preserve blockers for ambiguous identity, incomplete relations, dirty or
out-of-scope workspaces, active writers, source changes, branch drift, remote
divergence, unknown merge outcomes, and missing evidence. Do not create
replacement Issues or PRs when an existing identity cannot be proven.

## Existing legacy run

Pre-SR-08 legacy runs are compatibility evidence only. Continue one only when
the user identifies that exact run and the read-only legacy adapter proves its
identity. Never route new work through the old controller, dispatcher,
qualification gate, or parent-model continuation loop. Legacy and Runner runs
keep separate databases, leases, workers, and ownership.

## Completion

Treat only durable Runner receipts and external readbacks as completion
evidence. Report the run ID, durable state, executed or skipped frontier,
verification evidence, and any explicit `not_verified` live-environment gate.

Development of Spec Runner itself happens in its package and tests. Do not use
an unfinished Runner to schedule or clean up its own development.
