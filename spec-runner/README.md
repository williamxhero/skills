# Spec Runner

`spec-runner` is the independent, local execution core that will gradually take
over cross-stage delivery from the existing `implement-needs` Skill.  It is not
an import or wrapper around that Skill.

The first vertical slice supports an explicit `deterministic_test` backend and
the pinned `openai-codex==0.155.1` SDK backend. The deterministic backend is
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

```powershell
spec-runner start --brief .\brief.md --config .\runner.json `
  --control-root .\.spec-runner --launch-key example-001
spec-runner status --control-root .\.spec-runner --run-id <run_id>
spec-runner doctor --config .\runner.json --control-root .\.spec-runner
```

All commands write versioned JSON to stdout. `status` and `doctor` are read-only:
they never create a control directory, SQLite database, Git resource, credential,
or SDK installation. Reusing a launch key with identical normalized inputs returns
the original run. Reusing it with changed input is rejected; use a new launch key
for a deliberately new run.

The live SDK case is intentionally not run by ordinary tests because it requires
the operator's existing Codex authentication. Set `execution_backend` to
`codex_sdk` for that explicit run.

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
spec-runner takeover apply --file .\takeover.json --control-root .\.spec-runner --takeover-key <stable-key>
spec-runner fault run --seed sr-07-seed-1
spec-runner diagnose runtime --control-root .\.spec-runner
```

`candidate verify` accepts only trusted argument-array commands and checks the
Git SHA before and after execution. A worker's `passed` text is not a receipt.
`merge local` uses a disposable managed worktree and expected-ref compare; it
does not reset or overwrite a dirty user checkout. GitHub publication uses an
operation receipt and is explicit about body-link versus native relation mode.

The pinned adapter manifest is `dependencies.lock.json`. Its source digests are
checked before prompt rendering. Live GitHub, live SDK, Windows-native and
provider-specific capabilities are reported as `not_verified` unless they have
their own real evidence; deterministic tests never fill those gaps.

`fault run` invokes the public CLI against a temporary Git repository and
reports replayable deterministic cases for normal completion, completed-run
idempotency, input drift, and late cancellation. It also lists live SDK,
Windows-native, and GitHub merge-queue cases separately as `not_verified`.
