---
name: implement-needs
description: 'Hand a new requirement or an explicit takeover to the independently installable Spec Runner, then report its durable run status; retain read-only compatibility for pre-SR-08 legacy runs.'
---

# Implement Needs

`/implement-needs` is the user-facing handoff. `spec-runner` is the execution
engine. The Skill performs intake, authorization, and status presentation; the
Runner owns durable state, ordering, worker lifecycle, waiting, recovery,
verification, delivery, and cleanup.

## New request or explicit takeover

Use the installed Runner through `scripts/spec_runner_handoff.py`:

```powershell
python scripts/spec_runner_handoff.py --brief <brief> --config <runner.json> `
  --control-root <runner-control-root> --launch-key <stable-key>

python scripts/spec_runner_handoff.py --takeover-file <inventory.json> `
  --control-root <runner-control-root> --takeover-key <stable-key>
```

The handoff must pass a separate control root and a stable launch/takeover key.
The wrapper may resolve either the installed `spec-runner` executable or the
package module, but it must invoke only the Runner public CLI. Report the
returned run identifier and use the Runner's `status`, `pause`, `resume`,
`answer`, `cancel`, `takeover`, and diagnostic commands for subsequent
interaction.

New requests enter the Runner directly. The entry path does not run the legacy
qualification gate, `dispatch.py`, `advance_runtime`, `managed_recovery`,
`supervisor`, or a parent-model continuation loop. Do not create a legacy
control database, lease, task/thread binding, or recovery record for a Runner
run. The parent chat must not repeatedly call a “next action” function to keep
the run moving.

Before handoff, collect only the inputs needed by the Runner contract: the
requirement brief, repository/configuration scope, authorized artifact roots,
and a stable key. Do not silently manufacture missing requirements or expand
the authorized repository scope. If the Runner reports an input, authorization,
or external-capability blocker, preserve that durable status and report the
specific blocker.

## Existing legacy run

An invocation may inspect or continue a run that was already created by the
pre-SR-08 controller only when the user or the discovered inventory identifies
that exact legacy run. Read `references/legacy-controller.md` and use the old
path solely for that compatibility case. It remains an audit/migration path;
it is not a default for new work and it is not a development mechanism for
`spec-runner`.

For that identified compatibility path, retain the existing startup contract
and command reference in `references/startup-and-scope.md`; its
`startup-contract`, `startup-check`, `decide`, `record-observation`, and
`blocked_missing_dependency` checks remain legacy-only.

Legacy and Runner executions are isolated. They never share a control database,
writer lease, worker, or execution ownership. The Runner may consume a legacy
database through its read-only `legacy inspect` adapter to produce takeover
evidence; the adapter must not migrate, repair, delete, or write the legacy
database. Do not delete old databases, old artifacts, or old controller code
while compatibility evidence is still required.

When taking over, preserve verified artifacts and distinguish historical facts,
current observations, and newly verified evidence. Missing identity, thread,
issue, PR, test, merge, or cleanup evidence remains unknown until the Runner
reconciles it. A takeover inventory is not completion: after `takeover apply`,
the same Runner execution loop must continue through the remaining work, or
finish the cleanup-only path without starting an implementation worker.

## Completion and reporting

Treat only durable Runner receipts and readbacks as execution evidence. A model
message, process exit, accepted request, queued merge, or terminal-looking
thread status is not by itself proof of completion. Report the Runner run ID,
durable status, receipts, verification commands/results, and any explicit
`not_verified` live-environment gates.

The migration is complete only when the installed package and public handoff
have been validated, new requests route exclusively through `spec-runner`, and
the final SR-08 retirement decision has been made with legacy data preserved.
Until then, leave the legacy path available for identified existing runs.

## Development boundary

Development of `spec-runner` is performed directly in its isolated package and
tests. Do not use this Skill, its legacy controller, or an unfinished Runner to
schedule or clean up the Runner's own development.
