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
