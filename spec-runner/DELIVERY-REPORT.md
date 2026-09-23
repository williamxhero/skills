# Spec Runner delivery and acceptance report

Updated 2026-09-23 after commits `53a044c`, `9653b70`, and `9a7a831`. This is an evidence index, not a claim that every live
acceptance gate is complete. The authoritative runtime behavior baseline is
`fae55797f627e49bd8b453686a464aaf34ad89da`, including managed-worktree cleanup
recovery and release-subject qualification. PR #242 adds native lock coverage
without changing Runner runtime behavior.

## Route and implementation order

The implementation followed the required order:

`SR-01 → SR-02 → SR-03 → SR-04 → SR-05 → SR-06 → SR-09 → SR-07 → SR-08`

| SPEC | Tickets | Implementation evidence | Acceptance state |
| --- | --- | --- | --- |
| SR-01 / #165 | #166–#169 | PR #211, installable CLI and SDK adapter | deterministic and direct SDK slice verified; full parent-free two-stage live run not verified |
| SR-02 / #170 | #171–#174 | PRs #211, #213, #214 | SQLite, lease, detached launch, restart and controls verified; native cleanup probes cover SQLite, log, and artifact-directory locks, while the broader cross-process matrix remains not verified |
| SR-03 / #175 | #176–#179 | PRs #212, #220, current production tracker wiring | local tracker and GitHub read/error contracts verified; live same-repository publication not verified |
| SR-04 / #180 | #181–#184 | PR #211 and locked skill/prompt assets | deterministic prompt/plan contracts verified; upstream Matt invocation not verified |
| SR-05 / #185 | #186–#189 | PRs #211, #212 | local workspace/candidate/review contracts verified; independent live implementation/review not verified |
| SR-06 / #190 | #191–#194 | PRs #212, #222, #223 | local three-SPEC delivery and merge reconciliation verified; GitHub PR/CI/merge queue not verified |
| SR-09 / #205 | #206–#210 | PRs #214–#217 | deterministic mixed/takeover frontier and cleanup-only paths verified; real source-thread handoff and side effects not verified |
| SR-07 / #195 | #196–#199 | PRs #213, #218, #219, #221, #224, #241, #242 | deterministic fault/release/runtime/package gates and direct Windows cleanup locks verified; SDK user-input and GitHub matrix remain not verified |
| SR-08 / #200 | #201–#204 | PR #225 | legacy read-only adapter, thin entry, installation evidence and compatibility boundary delivered; final retirement decision remains dependent on the live gates above |

The 37 ticket inputs are explicitly preserved here rather than inferred from
issue state: #166, #167, #168, #169, #171, #172, #173, #174, #176, #177,
#178, #179, #181, #182, #183, #184, #186, #187, #188, #189, #191, #192,
#193, #194, #196, #197, #198, #199, #201, #202, #203, #204, #206, #207,
#208, #209, and #210. The roadmap issues remain open because issue state is
not being used as a substitute for acceptance evidence.

## Merged PR evidence

The Spec Runner implementation PR sequence currently includes #211–#242:

- #211 independent execution core;
- #212 GitHub delivery reconciliation;
- #213 public restart recovery matrix;
- #214 recovery and migration entrypoints;
- #215–#217 takeover waiting, mixed progress, and cleanup-only boundaries;
- #218–#221 release evidence, runtime diagnostics, GitHub read classification,
  and installed-artifact evidence;
- #222–#223 local multi-SPEC delivery and merge ancestor reconciliation;
- #224 deterministic fault expansion;
- #225 thin `/implement-needs` entry and legacy compatibility boundary;
- #228 live-turn pause, same-thread resume, cancel, and atomic control evidence.

PR #225 is merged at `0ab209a`, the follow-up evidence/CI change is merged at
`263a347`, and PR #228 is merged at `28f57d1`. There were no pre-existing
repository CI checks; `.github/workflows/spec-runner.yml` now adds Windows and
Ubuntu contract jobs. The PR #228 hosted run passed both contract jobs.
PR #231 is merged at `6cb6a69`; its hosted Ubuntu and Windows contract jobs
also passed.
PR #235 is merged at `1372816`; its hosted Ubuntu and Windows contract jobs
also passed.
PR #239 is merged at `8a85fae`; its hosted Ubuntu and Windows contract jobs
also passed.
PR #241 is merged at `fae5579`; it binds the full release subject and makes
unverified required evidence ineligible. PR #242 is merged at `80e5454`; its
hosted Ubuntu and Windows jobs passed the native Windows cleanup lock matrix.

## Verified evidence

* 64 `spec-runner` tests were collected: 63 passed and one
  authentication-dependent test was skipped.
* 477 existing `implement-needs` tests passed, including the thin-entry
  isolation coverage.
- compile and whitespace checks passed.
- local Git candidate/review/merge and a three-SPEC dependency-ordered replay
  passed, including merge-before-receipt crash reconciliation.
- a persisted local cleanup interruption returned `cleanup_pending` after the
  merge, preserved the managed resources, and then retried cleanup from the
  same receipt without re-running implementation or merge.
- a native Windows cleanup probe held a delete-denying handle in a Runner-owned
  candidate worktree on a Chinese/space path. Public local delivery returned
  `cleanup_pending` after its durable merge; after release, the same receipt
  cleaned the now-unregistered orphan worktree and manifest without a second
  implementation or merge.
- a clean isolated wheel previously passed `--version`, `diagnose package`,
  and all 10 deterministic public fault cases.
- release reports now canonically bind build, SDK package/version, current Skill observation,
  prompt/schema/validator digests, configuration, OS, trust mode, and scenario
  version. The report rejects subject-digest tampering, and a required evidence
  kind that is not passed returns `eligible: false`.
- live SDK adapter execution with `openai-codex==0.155.1` created real
  threads/turns, produced real temporary-repository artifacts, and passed
  archive pagination readback.
- a live public CLI run completed both stages after the first stage was
  actively paused and resumed on its persisted thread. It recorded real
  `worker_turn_started`, `worker_turn_interrupted`, `control_applied`, two
  verification receipts, and two archive readbacks before reaching
  `completed`.
- a separate live public CLI run cancelled an active second-stage SDK turn.
  It remained `cancelled`, issued no second-stage verification receipt, and
  archived the interrupted worker with readback.
- the pinned SDK adapter now passes the published `ApprovalMode.deny_all` value
  on both thread creation and persisted-thread resume; the contract tests assert
  both calls and reject SDK doubles that lack the required policy.
- a fresh isolated live CLI run (`8d65fe95-afd7-4d92-a193-b267a63aa584`) used
  that policy on Windows with `openai-codex==0.155.1`: both example stages
  created real thread/turn identities, produced artifacts, passed program-owned
  verification, and completed archive readback. Both persisted worker results
  record `approval_mode=deny_all`.
- a native Windows detached-runner probe used the public `launch` entrypoint in
  a Chinese/space path, verified the registered child command line, terminated
  only that exact Runner PID at the first durable artifact boundary, and used
  public `drive` to recover the same run. The recovered run reached
  `completed` with two verification receipts and archive readbacks.
- a native Windows Win32 share-denied file-lock probe used a Chinese/space path:
  replacement was rejected while the detached holder owned the file and
  succeeded after that exact holder was terminated. This verifies the single
  artifact-file lock boundary only; it is not the full Runner cleanup matrix.
- native Windows cleanup regressions use delete-denying Win32 handles on a
  Chinese/space path for a tracked SQLite artifact, run log, and artifact
  directory. Each returns `pending` while locked and reaches `cleaned` through
  the same retry path after that exact handle is released.
- a separate bounded child process holding a delete-denying SQLite artifact
  handle produces the same `pending` result; after that exact child exits, the
  retry cleans the original Runner-owned workspace and manifest.
- the public GitHub read entrypoint read the real `williamxhero/skills` root
  issue #164, SR-01 issues #165–#169, and takeover issue #205 with complete
  pagination. It classified the observed links as body relations and reported
  native relations as absent; no GitHub write was performed by this probe.
- the explicit `takeover sdk-read` entrypoint read a real, completed but
  unarchived SDK thread (`01a0c9c6-a679-75c0-8903-31cf43972981`) using
  `thread_resume` plus `thread.read`: it returned `idle`, one completed turn,
  no active flags, and `started_turn=false`; the probe then archived that
  thread explicitly. The same SDK rejects reading an unmaterialized idle
  thread or an already archived thread, so those cases remain visible
  capability boundaries rather than inferred history.

## Not verified / external blockers

These are explicit gaps, not simulated passes:

- real three-SPEC GitHub issue/PR/check/merge/cleanup side effects in the
  authorized `williamxhero/skills` acceptance scope;
* Windows control-database and launcher-log locks across a detached Runner
  restart or host/process failure (single-file, managed-worktree cleanup/retry,
  direct SQLite/log/artifact-directory locks, and a detached artifact-lock
  process are verified);
- real production Matt Skill invocation and three-SPEC acceptance against the
  current local Skill installation;
- real source-thread takeover, owner handoff, and thread cleanup across the
  Codex host boundary.
- SDK user-input turn paths; the pinned SDK exposes no public request callback
  for this boundary. Approval handling is verified for the restricted
  `deny_all` policy, while interactive approval is intentionally not enabled.

No credentials, user runs, production issues, or external test repository were
created as part of the deterministic or live local probes. These gaps must stay
`not_verified` until their required environment and authorization are present.
