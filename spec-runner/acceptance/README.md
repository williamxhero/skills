# Spec Runner acceptance workspace

This is the single test root for SF-01 through SF-06. The repository under
test is `williamxhero/skills`; no sandbox remote or second Git repository is
created. The user has authorized run-marked test issues, branches, pull
requests and source threads in this repository. All created resources must be
recorded in the run manifest before cleanup or closure.

`scenarios/` contains requirements, `fixture-app/<run-marker>/` is the only
source tree an acceptance worker may change, `harness/` and its oracle are
read-only to that worker, `reports/` records evidence, and ignored
`.runtime/<run-marker>/` holds control data, logs, worktrees and installed
builds. The installed wheel is fixed for a test run; the Runner may not upgrade
itself mid-run. Production code fixes belong in the product source tree, outside
the acceptance worker's permission scope.

Prepare one isolated namespace:

```powershell
python spec-runner/acceptance/harness/prepare.py prepare --repository .
```

The JSON result includes the marker and manifest path. Repeating with
`--run-id <marker>` returns the same manifest without creating a second run.
Before delivery, inspect the candidate's changed paths:

```powershell
python spec-runner/acceptance/harness/prepare.py scope --manifest <path> --workspace <worktree> --base <base-sha>
```

`scope` rejects every path outside the run's fixture directory. It is a
read-only check; it does not approve code or merge a PR. Keep source and
acceptance tests unavailable for writes inside the tested worker's sandbox.
