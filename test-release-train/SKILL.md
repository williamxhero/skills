---
name: test-release-train
description: 'Orchestrate impact-selected testing across a multi-SPEC, multi-ticket software release train: run fast L0-L2 gates per SPEC, conditional installed-artifact L3 checks for public contracts, fixed ten-SPEC owner-regression checkpoints, and centralized L4-L5 release validation with reusable environments and evidence. Use when several specs are delivered sequentially, when repeated full-repository tests are too costly, or when Implement Needs must plan per-ticket, per-SPEC, checkpoint, and final release gates.'
---

# Test Release Train

Concentrate expensive proof at release-train boundaries while preserving incremental evidence for every ticket and SPEC. Select tests from changed sources, owners, contracts, and adapters; repeated full-repository testing is not the default proof of safety.

Treat L0-L5 as six executable levels. Derive exact commands and markers from repository configuration, CI, and existing acceptance-scope manifests. Apply `unblock-development` when test infrastructure, tooling, network, environment, or service failures prevent a gate from running.

## 1. Create the train state

Persist `.scratch/<initiative>/test-release-train.md` beside the delivery map. Keep it resumable and update it after every ticket, SPEC, checkpoint, and release gate.

Record:

- ordered SPECs, tickets, dependencies, owners, `checkpoint_size: 10`, and deterministic checkpoint membership;
- baseline and candidate SHA for every repository;
- source fingerprint and installed import smoke for every unchanged repository;
- changed source files and their direct test files;
- directly affected dependencies and owner-level integration suites;
- public contract, wheel, connected, OCI, MarketHub, migration, and release flags;
- selected commands, exclusions, durations, historical p95, timeout, JUnit path, CPU/I/O observations, and result for every gate;
- cached baseline wheel, candidate artifact, isolated environment, and replay evidence;
- deferred release obligations and the exact gate that will discharge each one.

Discover existing acceptance-scope files before selecting tests. Make their `source file -> direct test files` mapping operational and update it when the change introduces or moves coverage. If no manifest exists, derive the smallest evidence-backed mapping from imports, ownership, test names, history, and coverage data, then persist it in the train state; absence of a manifest never justifies an automatic full-repository run.

## 2. Apply the layer contract

| Level | Proof | Target duration | Run policy |
| --- | --- | --- | --- |
| L0 | Format, lint, type checks, architecture and static guards | Under 1 minute | Every SPEC |
| L1 | Unit tests for changed modules and direct dependencies | 1-3 minutes | Every SPEC |
| L2 | Integration tests for the affected owner surface | 3-10 minutes | Every SPEC |
| L3 | Installed wheel/artifact with source unavailable, import smoke, and two deterministic replays | 2-5 minutes | Only when a public distribution contract changes; repeat for the exact final artifact |
| L4 | Full regression for affected owners or repositories | 30-60 minutes | Once per fixed ten-SPEC checkpoint, once for the final tail, and once at final release unless the last checkpoint is exact reusable evidence |
| L5 | Connected, OCI, MarketHub, and other environment-backed acceptance | Environment-dependent | Only for affected owners/adapters and the final release gate |

Every SPEC must pass L0-L2. A public package, CLI, schema, serialization, plugin, or import contract change also owes L3. L4 and L5 are train gates, not ticket gates.

## 3. Select impact instead of breadth

For each ticket, run only the tight red/green tests needed to implement it, keep that impact-selected slice within roughly ten minutes, and save incremental evidence. Accumulate its changed files and evidence into the owning SPEC; run the complete L0-L2 gate once for the SPEC rather than once per ticket.

Before a SPEC merges:

1. Diff the complete SPEC against its integration base.
2. Map every changed source file to direct tests through acceptance scope.
3. Expand through direct dependencies, ownership boundaries, shared schemas, migrations, serialization, plugin discovery, concurrency, and public contracts.
4. Select L0-L2 from that impact set and classify whether L3 is owed.
5. Record why every selected suite is included and why expensive owners or adapters are excluded.

For a repository with no source change, verify its fixed SHA, source fingerprint, and one installed import smoke. Keep its full regression out of the gate.

After Standards or Spec review fixes, recompute the touched impact set and rerun only affected L0-L3 evidence. Preserve the final L4-L5 gate for the integrated release candidate.

## 4. Run the per-SPEC gate

Keep the combined L0-L2 target near ten minutes. Run L0, then L1, then L2 so cheap failures stop later work. A duration target selects and optimizes tests; it never converts a timeout into a pass.

When L3 is owed, build the candidate wheel or artifact once for that integrated SPEC revision, install it once into a clean isolated environment with repository source unavailable, then run all import and contract checks plus two deterministic replays in the same environment. Compare canonical outputs or hashes and record both runs. Cache and reuse the fixed baseline wheel rather than rebuilding it.

Return a per-SPEC evidence packet containing changed owners/files, selected tests and rationale, L0-L3 results, durations, JUnit artifacts, performance-budget exceptions, artifact identifiers, and remaining train obligations. Merge only after the required per-SPEC layers are green.

## 5. Run checkpoint regressions

Form checkpoints deterministically with `checkpoint_size = 10`. Group ordered SPECs into consecutive fixed groups of ten, followed by at most one final tail group. Do not rebalance the tail, and do not move boundaries to natural owner or migration points. For 32 SPECs the required grouping is `10 + 10 + 10 + 2`, with L4 after SPEC 10, 20, 30, and 32.

At each checkpoint, run L4 once for the owners and repositories changed by that checkpoint segment. Reuse session environments and fixtures. Record membership, completed SPEC count, affected owners/repositories, exact integrated candidate revisions, result, and evidence before starting the next train segment. A due or failed checkpoint blocks the next segment and terminal success until it passes.

Do not run every repository merely because it participates in the wider product. Unchanged repositories retain their SHA, fingerprint, and import-smoke evidence.

## 6. Run the final release gate

After all SPECs are integrated, freeze the exact release-candidate revisions and discharge every remaining obligation:

1. Re-run release-configured L0-L2 checks required for the final candidate.
2. Build each release artifact once and reuse that artifact for L3, packaging, deployment, and smoke verification.
3. Install each artifact once with source unavailable and run the full accumulated public-contract selection plus two deterministic replays in the same isolated environment.
4. Reuse the last checkpoint as final L4 evidence only when its tested candidate revision set exactly equals the release candidate. If any product revision changed after that checkpoint, rerun final L4 for the changed owners/repositories and record that evidence. Run the complete multi-repository regression only when the train changed all of them.
5. Run L5 for changed connected adapters and release-required OCI, MarketHub, or external-service paths.
6. Report connected and OCI outcomes independently and truthfully.

An unrelated connected or OCI path cannot block an ordinary SPEC when its adapter and owner are unchanged. A relevant L5 failure blocks release and routes through `unblock-development`.

The release gate is green only when the exact candidate has satisfied every applicable layer and every deferred obligation is discharged.

## 7. Reuse expensive setup

Mark slow suites consistently as `slow`, `oci`, `connected`, or `release` using repository conventions. Default local commands exclude those markers; checkpoint and release commands opt into the required groups explicitly.

Use session-scoped Workspace and SQLite fixtures where supported. Isolate tests with transactions, savepoints, temporary schemas, or unique namespaces instead of repeatedly rebuilding databases and writing equivalent artifacts.

Keep both deterministic replays in one process and isolated environment when the runner supports it. Reuse built wheels, downloaded dependencies, immutable datasets, and unchanged-repository fingerprints only when their cache keys include all relevant source, lockfile, configuration, and platform inputs.

## 8. Enforce performance and observability

Require a marker and written explanation for an individual unit test over two seconds or a test file over sixty seconds. Track these exceptions as performance debt; silent budget expansion is not acceptance.

Stream live progress, current test file or suite, elapsed time, CPU/I/O observations, and JUnit results. Emit a concise heartbeat at least once per minute during long gates.

Set tool timeouts from historical p95 plus startup and teardown margin. When history exists, use at least 1.5 times p95 and never wrap a known 55-minute gate in a fixed 10-minute timeout. When history is absent, use the layer budget plus a conservative margin, record the actual duration, and establish p95 from subsequent runs.

## Failure routing and completion

Return product failures to the active SPEC task when they belong to its accepted behavior. Apply `unblock-development` to harness, dependency, network, permission, environment, runner, or external-service failures that prevent testing. Preserve failed JUnit and telemetry evidence in either path.

A SPEC is test-complete only when L0-L2 and any owed L3 are green. A checkpoint is complete only when its affected-owner L4 is green. The train is release-complete only when the final candidate passes every applicable L0-L5 obligation and the evidence file identifies the tested revisions and artifacts.
