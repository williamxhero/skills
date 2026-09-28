---
name: implement-needs
description: 'Start and report a governed requirement delivery run through the public Spec Runner contract.'
disable-model-invocation: true
---

# Implement Needs

`/implement-needs` is the single user-facing entry for governed continuous
development. It hands one authorized requirement to the installed Spec Runner,
which owns durable planning, SPEC and ticket ordering, implementation, tests,
review, delivery, cleanup, and the final summary.

## Intake

Accept exactly one of these authorized sources:

- the current conversation;
- explicit text supplied by the user; or
- a historical-chat reference supplied by the user.

Normalize the source into the Runner brief without inventing requirements. Ask
for any missing repository/configuration scope, authorized artifact roots, or
stable launch key. Preserve the source identity and UTF-8 content. A historical
reference identifies the source; do not persist an inline copy when a reference
is sufficient.

Pass only the inputs required by the public launch contract:

- the UTF-8 requirement brief;
- repository and configuration scope;
- authorized artifact roots; and
- a stable launch key.

Do not broaden the repository or artifact scope, create a local SPEC mirror, or
construct private Workflow payloads.

## Public handoff

Use the installed wrapper and public Runner CLI:

```powershell
python scripts/spec_runner_handoff.py launch --brief <brief> --config <runner.json> `
  --control-root <runner-control-root> --launch-key <stable-key>
```

The wrapper checks the versioned `spec-runner-cli/v1` response contract and
preserves the Runner's durable result. It must report the run ID, status, phase,
next action, evidence references, and blocker or `not_verified` reason.

Use the same wrapper for later controls:

```powershell
python scripts/spec_runner_handoff.py status --control-root <runner-control-root> --run-id <run-id>
python scripts/spec_runner_handoff.py pause --control-root <runner-control-root> --run-id <run-id>
python scripts/spec_runner_handoff.py resume --brief <brief> --config <runner.json> `
  --control-root <runner-control-root> --launch-key <stable-key>
python scripts/spec_runner_handoff.py answer --control-root <runner-control-root> `
  --run-id <run-id> --question-id <question-id> --value <json-value>
python scripts/spec_runner_handoff.py cancel --control-root <runner-control-root> --run-id <run-id>
python scripts/spec_runner_handoff.py diagnose --config <runner.json> --control-root <runner-control-root>
```

The run ID and stable launch key are durable identities. Retrying identical
input adopts the existing run; changed input with the same key is a blocker.
Keep subprocess output as evidence, but do not treat process exit, a queued
write, a model message, or a terminal-looking status as completion without the
Runner's durable readback.

## Legacy boundary

The public entry has one supported execution path: the Spec Runner. New
requests must not invoke the former controller, dispatch, recovery,
supervisor, continuation, or legacy database path. A legacy run is historical
evidence only. Resume, migration, takeover, or execution of a legacy run must
be rejected with an explicit blocker and must not create a replacement run.

If the Runner reports missing input, authorization, or external capability,
preserve that durable blocker and show the concrete next action. A deterministic
result is deterministic evidence; unavailable live capability is
`not_verified`, never live success.

## Installation contract

The installed Skill and Runner must agree on `implement-needs-handoff/v2` and
`spec-runner-cli/v1`. Validate both at the installed artifact boundary. A
version mismatch is a setup blocker with the observed versions recorded.

Development of the Spec Runner itself happens in its package and tests. Do not
use an unfinished handoff to schedule its own development.
