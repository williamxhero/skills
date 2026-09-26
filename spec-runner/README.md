# Spec Runner

`spec-runner` is the independent, local execution core that will gradually take
over cross-stage delivery from the existing `implement-needs` Skill.  It is not
an import or wrapper around that Skill.

The first vertical slice supports an explicit `deterministic_test` backend and
the validated `openai-codex==0.155.1` SDK backend. The deterministic backend is
useful for isolated contract tests; the SDK backend creates a real local Codex
thread when the operator has authenticated Codex. No deterministic result is
represented as a Codex thread or turn.

## Install

From this directory, in a new virtual environment:

```powershell
python -m pip install .
spec-runner --help
```

## Example

Create `brief.md` and `runner.json` (replace `repository_path` with an existing
Git repository):

```json
{
  "schema_version": "spec-runner-config/v1",
  "repository_path": "C:/work/example",
  "target_ref": "HEAD",
  "artifact_root": "artifacts",
  "execution_backend": "deterministic_test",
  "allowed_stages": ["example"],
  "model": {"name": "deterministic-test", "effort": "none"},
  "authorization": {"artifact_roots": ["artifacts"]}
}
```

For an explicit local whole-SPEC run, add a trusted delivery plan below the
control root. Each SPEC declares `blocked_by`, argument-array implementation
commands, acceptance/check mappings, and a review receipt path:

```json
{"delivery": {"plan": "delivery-plan.json"}}
```

The plan schema is `spec-runner-delivery-plan/v1`. It runs dependency-ordered
SPECs in isolated worktrees, binds checks and review to the candidate SHA,
merges each candidate into the configured local ref, and persists a durable
receipt. A failure stops the queue before the next SPEC; re-running reads the
receipt/worktree and never blindly replays an unknown implementation command.

After a durable local merge, the Runner removes only its managed candidate
worktree and manifest. If Windows or another process still holds a managed
file, the receipt remains `cleanup_pending`; the same run can be driven again
to retry cleanup without re-running implementation or merge commands.

```powershell
spec-runner start --brief .\brief.md --config .\runner.json `
  --control-root .\.spec-runner --launch-key example-001
spec-runner status --control-root .\.spec-runner --run-id <run_id>
spec-runner doctor --config .\runner.json --control-root .\.spec-runner
spec-runner pause --control-root .\.spec-runner --run-id <run_id>
spec-runner resume --brief .\brief.md --config .\runner.json --control-root .\.spec-runner --launch-key example-001
spec-runner answer --control-root .\.spec-runner --run-id <run_id> --question-id Q1 --value '"approved"'
spec-runner delivery run --plan .\.spec-runner\delivery-plan.json --repository C:\work\repo --workspace-root C:\work\runner-workspaces --control-root .\.spec-runner --run-id <run_id> --target-ref refs/heads/main
```

All commands write versioned JSON to stdout. `status` and `doctor` are read-only:
they never create a control directory, SQLite database, Git resource, credential,
or SDK installation. Reusing a launch key with identical normalized inputs returns
the original run. Reusing it with changed input is rejected; use a new launch key
for a deliberately new run.

The live SDK case is intentionally not run by ordinary tests because it requires
the operator's existing Codex authentication. Set `execution_backend` to
`codex_sdk` for that explicit run.

A production semantic run must define `workflow.acceptance.ids`, trusted
`workflow.acceptance.checks`, and exactly one repository-relative
`workflow.acceptance.write_scope` root. The implementation and repair workers
start with that directory as their SDK working directory. Before checks, review,
or publication, the Runner compares committed, staged, untracked, ignored,
and symlinked candidate paths against the same scope and rejects any escape.

For an authenticated SDK run, `pause` and `cancel` are applied to the active
`TurnHandle` through the SDK's `interrupt()` operation. A paused run persists
its formal thread/turn identity and `resume` continues that same thread; a
cancelled run retains its artifacts and performs thread archive readback without
advancing to another stage.

The validated SDK path starts and resumes threads with the explicit `deny_all`
approval policy. The Runner never silently grants a tool approval. SDK
0.155.1 exposes no public user-input request callback in this adapter boundary;
such a path remains `not_verified` and is not treated as a completed run.

The complete implementation/evidence index is [DELIVERY-REPORT.md](DELIVERY-REPORT.md).
It separates deterministic, local Git, live SDK, Windows, and GitHub evidence;
an open roadmap issue or a successful model response is not treated as delivery
proof. The repository CI workflow runs the deterministic contract and installed
wheel checks on both Windows and Ubuntu. It does not silently run live SDK or
GitHub side effects.

## Delivery and takeover boundaries

The package also contains the later vertical slices used by SR-02 through
SR-09. They are explicit commands, so reading, planning, verification, review,
merge, and takeover cannot be delegated to a model:

```powershell
spec-runner tracker read --root .\issues
spec-runner intake local --root .\issues --entry SR-01
spec-runner plan validate-spec --file .\spec-plan.json
spec-runner plan validate-tickets --file .\ticket-plan.json --spec-key SR-01 --base-sha <sha>
spec-runner workspace prepare --repository C:\work\repo --workspace-root C:\work\runner-workspaces --run-id <run> --spec-key SR-01
spec-runner candidate verify --workspace C:\work\runner-workspaces\SR-01-1234 --candidate-sha <sha> --acceptance-version v1 --checks .\checks.json --acceptance .\acceptance.json
spec-runner review validate --file .\review.json --candidate-sha <sha> --acceptance-version v1
spec-runner merge local --repository C:\work\repo --workspace-root C:\work\runner-workspaces --candidate-branch spec-runner/SR-01-1234 --target-ref refs/heads/main --expected-target-sha <sha> --run-id <run>
spec-runner takeover inspect --file .\takeover.json
spec-runner takeover sdk-read --thread-id <thread_id> --repository C:\work\repo
spec-runner takeover apply --file .\takeover.json --control-root .\.spec-runner --takeover-key <stable-key>
spec-runner takeover inspect --thread-id <thread_id> --repository C:\work\repo --scope fixture-app\run-1
spec-runner takeover apply --thread-id <thread_id> --repository C:\work\repo --scope fixture-app\run-1 --handover-policy interrupt_then_takeover --control-root .\.spec-runner --takeover-key <stable-key>
spec-runner legacy inspect --db .\old-control.sqlite3 --repository C:\work\repo
spec-runner diagnose package --wheel .\dist\spec_runner-0.1.0-py3-none-any.whl
spec-runner diagnose release-build --subject .\release-subject.json --evidence .\deterministic-evidence.json --evidence .\local-git-evidence.json --output .\release-report.json
spec-runner diagnose release-report --file .\release-report.json
spec-runner fault run --seed sr-07-seed-1
spec-runner diagnose runtime --control-root .\.spec-runner
```

`candidate verify` accepts only trusted argument-array commands and checks the
Git SHA before and after execution. A worker's `passed` text is not a receipt.
`merge local` uses a disposable managed worktree and expected-ref compare; it
does not reset or overwrite a dirty user checkout. GitHub publication uses an
operation receipt and is explicit about body-link versus native relation mode.

The SDK adapter manifest is `dependencies.lock.json`; it records the SDK/runtime
combination, not a Matt Skill version gate. The packaged Skill lock is a
historical, explicitly selected input for `skill render --lock`; production
`start`, `drive`, and SDK resume resolve the current authorized local Skill on
each turn and do not require a Skill hash, commit, new sandbox, or re-
authentication. Live GitHub, live SDK, Windows-native and provider-specific
capabilities are reported as `not_verified` unless they have their own real
evidence; deterministic tests never fill those gaps.

`legacy inspect` opens the old database read-only and emits a common takeover
inventory. It does not migrate, repair, or delete the old database. The
inventory keeps old rows as historical evidence; missing thread identity,
verification, and requirements remain unknown and are handled by the normal
takeover planner.

`takeover sdk-read` is an explicit read-only source-thread probe. It records
thread metadata, visible business items, safe Git/index/untracked digests, and
history completeness without starting, steering, interrupting, or archiving a
turn. Reasoning, encrypted, opaque, and credential-bearing payloads are
omitted. `takeover inspect/apply --thread-id` can build this input directly
from a source ID and an authorized `--scope`; it does not require a hand-written
inventory or a pre-existing Runner database. A read or an SDK turn interrupt
does not prove that an external scheduler has stopped writing. The pinned SDK
has no public operation for interrupting an arbitrary historical turn, so
`interrupt_then_takeover` fails closed at that capability boundary. The apply
path remains blocked until a supported handover readback proves both source
writer stop and dispatcher quiescence.

`diagnose package` checks the actual wheel contents for the CLI, dependency
lock, metadata, and forbidden runtime data. `diagnose release-build` constructs
a version-bound report from complete evidence bodies. Its subject must bind the
build, SDK package/version, current Skill observation summary, prompt/schema/validator digests,
configuration contract, OS, trust mode, and scenario version; `release-report`
re-reads both that canonical subject and its digest. A release index or a
manually supplied digest is not sufficient evidence.

The `--subject` file is UTF-8 JSON and must contain `runner_version`,
`build_digest`, `config_contract`, `sdk_runtime` (`package` and `version`),
`contract_digests` (`prompt_templates`, `schemas`, and `validators`), `os`, `trust_mode`,
and `scenario_version`. Current Skill path, read time, and digest are recorded
in worker evidence and are not release admission pins. Pass each live
qualification kind through `--required-kind`: an unavailable required probe is
recorded as `not_verified` and makes the returned `eligible` field false.

`fault run` invokes the public CLI against a temporary Git repository and
reports replayable deterministic cases for normal completion, process restart
after each durable artifact boundary, takeover continuation, completed-run
idempotency, input drift, and late cancellation. It also lists live SDK,
Windows-native, and GitHub merge-queue cases separately as `not_verified`.
