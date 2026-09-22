# Spec Runner delivery and acceptance report

Updated 2026-09-22. This is an evidence index, not a claim that every live
acceptance gate is complete. The authoritative delivered code is on
`origin/master` at `0ab209aca7677d3e43c70fc1bd9b9dea942331cf`.

## Route and implementation order

The implementation followed the required order:

`SR-01 → SR-02 → SR-03 → SR-04 → SR-05 → SR-06 → SR-09 → SR-07 → SR-08`

| SPEC | Tickets | Implementation evidence | Acceptance state |
| --- | --- | --- | --- |
| SR-01 / #165 | #166–#169 | PR #211, installable CLI and SDK adapter | deterministic and direct SDK slice verified; full parent-free two-stage live run not verified |
| SR-02 / #170 | #171–#174 | PRs #211, #213, #214 | SQLite, lease, detached launch, restart and controls verified; native kill/restart remains not verified |
| SR-03 / #175 | #176–#179 | PRs #212, #220 | local tracker and GitHub read/error contracts verified; live sandbox publication not verified |
| SR-04 / #180 | #181–#184 | PR #211 and locked skill/prompt assets | deterministic prompt/plan contracts verified; upstream Matt invocation not verified |
| SR-05 / #185 | #186–#189 | PRs #211, #212 | local workspace/candidate/review contracts verified; independent live implementation/review not verified |
| SR-06 / #190 | #191–#194 | PRs #212, #222, #223 | local three-SPEC delivery and merge reconciliation verified; GitHub PR/CI/merge queue not verified |
| SR-09 / #205 | #206–#210 | PRs #214–#217 | deterministic mixed/takeover frontier and cleanup-only paths verified; real source-thread handoff and side effects not verified |
| SR-07 / #195 | #196–#199 | PRs #213, #218, #219, #221, #224 | deterministic fault/release/runtime/package gates verified; real Windows/SDK/GitHub matrix remains not verified |
| SR-08 / #200 | #201–#204 | PR #225 | legacy read-only adapter, thin entry, installation evidence and compatibility boundary delivered; final retirement decision remains dependent on the live gates above |

The 37 ticket inputs are explicitly preserved here rather than inferred from
issue state: #166, #167, #168, #169, #171, #172, #173, #174, #176, #177,
#178, #179, #181, #182, #183, #184, #186, #187, #188, #189, #191, #192,
#193, #194, #196, #197, #198, #199, #201, #202, #203, #204, #206, #207,
#208, #209, and #210. The roadmap issues remain open because issue state is
not being used as a substitute for acceptance evidence.

## Merged PR evidence

The Spec Runner implementation PR sequence currently includes #211–#225:

- #211 independent execution core;
- #212 GitHub delivery reconciliation;
- #213 public restart recovery matrix;
- #214 recovery and migration entrypoints;
- #215–#217 takeover waiting, mixed progress, and cleanup-only boundaries;
- #218–#221 release evidence, runtime diagnostics, GitHub read classification,
  and installed-artifact evidence;
- #222–#223 local multi-SPEC delivery and merge ancestor reconciliation;
- #224 deterministic fault expansion;
- #225 thin `/implement-needs` entry and legacy compatibility boundary.

PR #225 is merged at `0ab209a`. There were no pre-existing repository CI
checks; `.github/workflows/spec-runner.yml` now adds Windows and Ubuntu
contract jobs for future pushes and pull requests. Its first hosted run is
still pending after this change is pushed.

## Verified evidence

- 51 `spec-runner` tests passed with one authentication-dependent test skipped.
- 427 existing `implement-needs` tests passed; 3 thin-entry isolation tests
  passed.
- compile and whitespace checks passed.
- local Git candidate/review/merge and a three-SPEC dependency-ordered replay
  passed, including merge-before-receipt crash reconciliation.
- a clean isolated wheel previously passed `--version`, `diagnose package`,
  and all 10 deterministic public fault cases.
- live SDK adapter execution with `openai-codex==0.155.1` created a real
  thread/turn, produced a real temporary-repository artifact, and passed
  archive pagination readback. The new `Thread.turn()` callback also captured
  formal IDs before the result.
- a live public CLI run reached `running` with its formal thread/turn visible
  in `status`; the two-stage run was not allowed to be called complete because
  the bounded external probe timed out while the model performed repository
  exploration.

## Not verified / external blockers

These are explicit gaps, not simulated passes:

- real three-SPEC GitHub issue/PR/check/merge/cleanup side effects in a
  dedicated authorized sandbox repository;
- live public two-stage SDK execution to final archive and next-stage receipt
  under a sufficiently long external timeout;
- SDK active-turn interrupt/resume and approval/user-input paths;
- native Windows process kill/restart and file-lock cleanup matrix;
- upstream Matt Skill invocation against the locked external sources;
- real source-thread takeover, owner handoff, and thread cleanup across the
  Codex host boundary.

No credentials, user runs, production issues, or external test repository were
created as part of the deterministic or live local probes. These gaps must stay
`not_verified` until their required environment and authorization are present.
