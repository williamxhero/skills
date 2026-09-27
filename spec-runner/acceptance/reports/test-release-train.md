## Production workflow typed context (2026-09-27)

`ProductionWorkflow` now receives the existing typed `RunContext` as one
cohesive input instead of independently accepting the control directory,
configuration, brief digest, run, and Store. Queue continuation replaces only
the context's durable run after Store readback. The compatibility workflow
factory translates its legacy arguments into that context, and delivery
receipt writes now pass the run and Store already held by their caller.

The focused production, GitHub recovery, cleanup, and Runner-interface
selection passed 85 tests. The complete source and acceptance selection passed
358 tests with 1 authentication-dependent test skipped. `compileall` and
`git diff --check` passed. CLI JSON, SQLite, and receipt formats were not
changed. This is structural source evidence only; live SDK, GitHub, takeover,
and project-level L3-L5 gates remain `not_verified`.

## SF-06.1 current candidate installed-wheel replay (2026-09-27)

The current pushed candidate `e4357eac15a1d5eafc4d6f7916b121e97d9a316b`
was built as `spec_runner-0.1.0-py3-none-any.whl` and installed into a fresh
virtual environment from a non-source working directory. `PYTHONPATH` and
`PYTHONHOME` were cleared, the imported package resolved from the isolated
environment, and `diagnose package` returned `verified`. The wheel SHA-256 is
`981061f107b2dd726036f6a2bf6e4e3bc9427a08c0918d062287fb2437046db9`.

The public CLI fault matrix was replayed twice with the same run seed. Each
replay passed all 10 cases and produced report digest
`97627c900f832e6fac7fbdd270307c75fca4696c59b33868dd3389841350a636`; case
outcomes were identical. Evidence is
`acceptance/reports/SRAC-20260927-459206570e59-installed-wheel.json`.
This discharges the current installed-artifact L3 contract only. Live SDK
production, GitHub publication/merge queue, source-thread takeover, six-SPEC
L4, and connected L5 remain `not_verified`.

## RCV-01.2 shared route circuit coordination (2026-09-26)

The control Store now persists an explicit, bounded `route_scope` circuit with
`closed`, `open`, and `half_open` states. A route failure records the scope and
cooldown in SQLite. After cooldown, `acquire_route_probe` claims the half-open
owner in one `BEGIN IMMEDIATE` transaction, so two independent Store
connections cannot both reserve the same probe. The same owner may replay its
reservation, an expired owner lease may be reclaimed, and different scopes are
isolated. Closing the circuit requires explicit `business_progress:` or
`route_success:` evidence; an ordinary HTTP success cannot close it. Unknown
route scopes are rejected and are never merged into the shared circuit.

The Runner recovery boundary records route-not-found observations into this
shared state, adds the circuit state and half-open ownership precondition to the
durable recovery decision, and exposes the route circuits in public status.
The candidate source revision is `0b1b206`.

The focused Store and recovery selection passed 35 tests. The complete source
and acceptance selection passed 289 tests with 1 authentication-dependent test
skipped on 2026-09-26 using
`PYTHONPATH=spec-runner/src python -m pytest spec-runner/tests spec-runner/acceptance --ignore=spec-runner/acceptance/fixture-app -q`.
`compileall` and `git diff --check` also passed.

This is an incremental RCV-01.2 correction. It proves durable cross-process
reservation and scope isolation at the SQLite boundary. It does not prove a
real provider route probe, live capacity/404 recovery, SDK timer scheduling,
source-thread migration, or project-level L3-L5 gates. Those remain
`not_verified`; #294 and its parent remain open.

## RCV-01.2 atomic recovery budget reservation (2026-09-26)

Recovery episode counters now use an idempotent SQLite reservation row keyed by the provider request or turn identity. The reservation and counter increment commit in one transaction, so replaying the same attempt cannot consume budget twice and a crash cannot lose a reservation. Episode upserts preserve the maximum persisted counter instead of allowing a stale snapshot to reduce it. Workflow recovery now persists route probe, clean-context probe, and clean migration consumption after their corresponding action has been attempted, bounding those paths across retries and restarts.

Candidate source revision: 3b8d00b.

The focused Store and Runner recovery-runtime selection had 19 passing tests; the earlier recovery policy selection had 40 passing tests; the explicit source and acceptance selection passed 282 passed, 1 skipped in 68.85 seconds using PYTHONPATH=spec-runner/src pytest spec-runner/tests spec-runner/acceptance --ignore=spec-runner/acceptance/fixture-app -q. compileall and git diff --check passed.

This is an incremental RCV-01.2 correction. It does not prove cross-process route circuit coordination, live provider capacity behavior, SDK timer scheduling beyond the existing deterministic driver, source-thread migration under #292, or project-level L3-L5 gates. Those remain not_verified; #294 and its parent remain open.

# SF integration release train

## PR receipt identity hardening (2026-09-24)

Review of the PR delivery adapter found that a provider readback could omit the
PR number or base repository identity and still be accepted: the number fell
back to the request path, and the repository check was optional. The adapter
now requires the returned number, structured head/base refs, and an exact
`base.repo.full_name` match before adopting or refreshing a receipt. Missing or
conflicting identity fails closed.

The focused PR delivery and GitHub contract selection passed 24 tests; the
tracker and production boundary selection passed 29 tests. The complete source
and acceptance selection passed 192 tests with one skip in 61.94 seconds, and
`git diff --check` passed. This is source-level L0-L2 evidence; it does not
replace exact-wheel L3 for the final source or discharge the outstanding live
SDK restart, Windows detached-parent/control DB recovery, source takeover,
six-SPEC L4, or GitHub merge queue gates. Issue #256 remains open.

## Latest production three-SPEC GitHub run (2026-09-24)

Run marker `SRAC-20260924-a1b2c3d4e5f6`; run ID
`07eef590-ebe1-47b4-94cd-fd10895eb66f`. The production entrypoint completed
SPEC-1, SPEC-2, and SPEC-3 with durable SQLite completion records, candidate
verification, independent approval, required Ubuntu and Windows checks, GitHub
PR merge readback, and cleanup. The durable summary is
`SRAC-20260924-a1b2c3d4e5f6-production.json`.

The merged PRs are #307 (SPEC-1, merge `d7a2bea8eaa9c1aca791c448b6a44527c9a762f5`),
#311 (recovered SPEC-2, merge `50865edd45cbde9fc9f807f8e600e83df861a53c`), and
#316 (SPEC-3, merge `ec63e6c8c6e262449ef3206369117aa9a8491e35`). PR #310 remains
open as historical evidence of the failed SPEC-2 candidate whose Ubuntu check
failed; it was not treated as merged or successful.

This run verifies the complete three-SPEC GitHub production delivery path for
this marker, including failed-candidate recovery. It does not close issue #256
or discharge the project-level exact-wheel L3, six-SPEC L4, live SDK restart,
Windows detached-parent exit, source takeover, Windows control DB/log recovery,
or GitHub merge queue gates. Those remain `not_verified`.

The nine issues created by this run (#304-#306, #308-#309, and #312-#315) were
subsequently reconciled with the durable publication receipts and closed through
the current per-issue close operation. Each issue's repository, run marker,
title, and body matched before PATCH, and its closed state was read back. Nine
`github_issue_close` receipts are complete in the run's SQLite control database.
PR #310 remains open as failed-candidate history and was not included. This
close reconciliation adds SF-03.1 issue-closure evidence; it does not change the
SPEC completion receipts or discharge any project-level gate listed above.

The user-required acceptance root overrides the skill's default scratch path.
Baseline for this increment: 400775b8571ff43d85d999933d8d31283b61a0de.
checkpoint_size: 10. Ordered SPECs: SF-01 #247, SF-02 #251, SF-03 #255,
SF-04 #259, SF-05 #263, SF-06 #267. One final tail checkpoint (six SPECs).
Tickets and dependency order remain the existing #246 index; no new tickets.

## Current increment: publication recovery (#256)

Source mapping:
- github_tracker.py -> tests/test_github_tracker.py and
  acceptance/test_tracker_publication_recovery.py (operation identity, partial
  publication, process loss, external edit, body-link identity).
- workflow.py -> acceptance/test_production_planning.py (production dispatch).

L0: git diff --check. L1/L2: the three mapped suites above, PYTHONPATH=src.
Initial budget: 180 seconds; no historical p95 recorded for this selection.
JUnit: acceptance/.runtime/tracker-recovery.xml. L0 passed; selected L1/L2
completed with 30 passed in 1.19 seconds on 2026-09-23. This result binds the
uncommitted source increment over the baseline, not a released wheel.
No live SDK or GitHub side effects in these tests; transport is simulated.

Deferred: final exact-wheel L3 with two replays; owner regression L4 after the
six-SPEC tail; live single-SPEC, three-SPEC GitHub, source takeover, and Windows
control DB/log recovery L5. None are discharged by a unit-test result.
Native parent and blocked-by relation writing and readback are verified by the
live report `SRAC-20260923-6dfe49ef27ba-native-relations.json`. That evidence
covers the SF-03.1 relation capability only; it does not discharge the
deferred project-level live SDK restart, complete three-SPEC GitHub delivery,
or Windows control DB/log recovery gates.

The transport tests additionally verify BOM-free UTF-8 file upload, temporary
file cleanup, a 120-second gh timeout, and structured timeout classification.
No repository initialization, test issue creation, or issue closure occurred.

## PR receipt boundary correction (2026-09-24)

Review of the merged SF-03.1 implementation found two fail-closed gaps in the
PR delivery adapter. A paginated response containing a non-object entry could
be silently filtered before the create decision, and definitive authentication,
permission, not-found, rate-limit, rejection, or local `gh` availability
errors from PR creation could be reclassified as an unknown outcome. The
adapter now rejects malformed pages before POST and preserves those definitive
classes; timeouts, server errors, and lost responses still require marker
reconciliation before retry.

The focused PR delivery and GitHub contract suites passed 23 tests. The source
and acceptance suite, excluding the two run fixture directories whose tests
share a module basename and excluding the environment-specific missing-SDK
assertion while the SDK is installed, passed 167 tests with one skip. The
default recursive collection remains invalid for those duplicate fixture module
names; the missing-SDK test was not treated as a product failure.

A follow-up regression also verifies that the real `_gh` transport maps an HTTP
422 validation response to the definitive `github_rejected` class. PR creation
therefore does not treat a provider rejection as an unknown external outcome or
perform an unnecessary marker reconciliation query. The broader tracker/delivery
recovery selection passes 41 tests.

This correction improves SF-03.1 failure classification and readback safety. It
does not discharge the issue's live three-SPEC production publication,
source-thread takeover, Windows control DB/log recovery, or project-level L4/L5
gates. GitHub issue #256 remains open.

## Live production single-SPEC delivery (2026-09-24)

Run marker `SRAC-20260924-f531e1d00a03`; run ID
`20e28e7a-17fe-47fa-a2ae-99a1e9d4bb88`. This run exercised the production
entrypoint with nine persisted business answers, then completed SpecPlan,
TicketPlan, implementation, candidate verification, independent review, local
merge, and workspace cleanup.

The candidate was `2af7662b147bad06e4ebab518daf8dbfd86d2e48`, based on
`22b46c2933eba0936e83f533212383d92b0cf659`. The candidate check ran the six
fixture tests successfully. Independent review approved the candidate with no
blocking findings. Local merge produced
`b88ea9e64bf3a0bbd364b00f08d6063bd0c431b`; cleanup completed with no errors.
The durable receipt is
`SRAC-20260924-f531e1d00a03-production.json`.

The review left two open medium findings: the fixture test matrix does not
cover missing, extra, or reordered headers or preserve an existing output for
each invalid input; and it does not inject serialization or temporary-file
preparation/write/flush/fsync failures. These findings remain open and this
run is not evidence that SF-03.1 or the project-level release gates are
complete. Exact-wheel L3 for this post-merge candidate, complete three-SPEC
GitHub delivery, live SDK restart, Windows detached-parent exit, source
takeover, Windows control DB/log recovery, and the six-SPEC L4 checkpoint
remain unverified.

## Production cleanup recovery hardening (2026-09-23)

Impact: `src/spec_runner/workflow.py` production dispatch and recovery; direct
acceptance coverage is `acceptance/test_production_planning.py` and
`acceptance/test_github_production_boundary.py`.

L0: `git diff --check` passed. L1/L2: complete `pytest -q` selection under
`spec-runner/` passed (118 passed, 1 skipped, 23.40 seconds); the run includes
the affected production queue and GitHub boundary contracts. The initial run
exposed a missing plan/ticket fixture in the waiting-CI acceptance; the fixture
was updated to represent durable production evidence and the complete suite
was rerun successfully. This is source-level evidence only, not L3 installed
artifact proof or L5 live GitHub merge evidence.

The live current-skill probe marker `SRAC-20260923-7e4a1b9c2d3f` observed the
registered idle SDK thread, then archived it through the installed SDK and
verified archived-list readback (one page). Receipt:
`SRAC-20260923-7e4a1b9c2d3f-skill-probe.json`.

Remaining for this train: exact-wheel L3 and two replays; six-SPEC tail L4;
live GitHub source/takeover and Windows control DB/log recovery L5. None is
discharged by these unit/acceptance tests.

## Recovery and candidate-scope audit (2026-09-24)

The audit resumed the existing production run `40415c4d-634b-4625-9d3f-99e6cd0a3e59`
for marker `SRAC-20260923-e818da834f41`; it did not create a second run. The
run's fixture implementation candidate is `32be96c1c08298874a687b39df19cdc75ed529a5`,
based on `29045fed3f41e0a5a83fe12315dde59d9f866fce`.

The audit found and fixed five recovery defects: candidate-scope Git reads did
not enable Windows long paths; failed-run closure hid a completed implementation
worker from safe recovery; workspace adoption rejected the manifest's original
base after `master` moved; trusted pytest checks created an ignored cache outside
the fixture allowlist; and completed implementation recovery validated its
TicketPlan against the moved target instead of the manifest base. The fixes
were pushed to `origin/master` as `a8e67e2`, `07db777`, `ef5fc04`, `65291e6`,
and `6d22645`.

L0 `git diff --check` passed. Focused delivery/production checks passed (64
passed), workflow recovery checks passed (9 passed, 5 deselected), and the
final source suite passed (159 passed, 1 skipped, 1 deselected; 49.69 seconds).
The deselection is the environment-specific missing-SDK test, since the SDK is
installed locally. These are source-level checks, not installed-wheel L3.

The same run recovered the completed SDK turn without replaying it. Trusted
candidate verification passed all six fixture tests and recorded
`candidate-SRAC-20260923-e818da834f41.json`; independent review approved the
same candidate SHA with no findings. Merge then stopped with
`target_ref_changed`: `master` had advanced beyond the candidate's base while
the recovery fixes were being committed. The run remains `blocked` at
`codex_review`; there is no merge receipt, cleanup readback, or transactional
SPEC completion. No candidate was force-merged and no completion was claimed.

## Approved-review recovery follow-up (2026-09-24)

The blocked run exposed a recovery branch that treated every persisted
`reviewed` worker as a repair frontier, including an approved review whose
candidate was waiting at the merge boundary. Recovery now revalidates the
review worker identity and receipt, candidate receipt, TicketPlan, and
implementation result before reusing that candidate. The finish path also
checks that the approved review still names the verified candidate and reuses
the persisted implementation archive readback instead of repeating the SDK
archive operation.

Regression coverage proves recovery reaches the guarded finish path without
calling review or repair again, and that a mismatched review candidate is
rejected. The complete source/acceptance suite passed 166 tests with one skip;
the installed-SDK environment-specific missing-SDK test was deselected.
Replaying the same public `start` command and launch key against the existing
run returned `target_ref_changed` at local merge. The run remains blocked;
its implementation and review worker identities are unchanged, and it has no
merge receipt or transactional SPEC completion. This verifies recovery through
the merge guard only; the candidate still needs a deliberate rebase/review
cycle against the current target before delivery can complete.

The current recovery implementation also verifies that the embedded review
worker snapshot matches the separately persisted worker receipt. Rehydration
preserves `approval_mode` and `skill_observation`, which older persisted reviews
include. Regression tests reject a changed embedded worker. Replaying the same
public `start` and launch key on 2026-09-24 passed receipt reconciliation and
again stopped at `target_ref_changed`; no worker turn or SPEC completion was
added. The run remains blocked pending a deliberate candidate rebase and fresh
review.

GitHub issue #256 remains OPEN and its acceptance checklist remains unchecked.
The existing live report `SRAC-20260923-6dfe49ef27ba-native-relations.json`
proves native parent and blocked-by write/readback for its three run-marked
test issues only. It does not prove the full SF-03.1 production publication
recovery acceptance or the project-level three-SPEC GitHub delivery. Exact-wheel
L3, six-SPEC tail L4, live SDK process restart, Windows detached-parent exit,
source takeover, Windows control DB/log recovery, and complete three-SPEC
GitHub delivery remain `not_verified`.

## Transactional production completion frontier (2026-09-23)

The SQLite Store is now authoritative for per-SPEC completion: plan and
delivery digests, queue state, and the event are committed in one transaction.
`completed-specs.json` is only a projection and cannot independently advance
the queue. Conflicting receipts fail closed. Cleanup recovery records a SPEC
only after validating its persisted delivery evidence and completing cleanup.

Impact selection: `store.py` schema/transaction and `workflow.py` queue/recovery;
direct evidence is `acceptance/test_production_planning.py` and
`acceptance/test_github_production_boundary.py`. L0 `git diff --check` passed;
complete L1/L2 pytest passed (119 passed, 1 skipped, 23.17 seconds). No live
production or GitHub side effect was performed in this source validation.
Exact-wheel L3, release-train L4, and live L5 obligations remain open.

L3 was subsequently run on the exact `0554dcd72d7a8807bade083ec0f500180eaf8dc1`
artifact. A clean Windows venv installed the built wheel with its declared SDK
dependency; the import resolved from `site-packages` with `PYTHONPATH` cleared
and cwd outside the source tree. Package inspection passed, and two public-CLI
fault-matrix replays each passed all 10 deterministic cases with identical
report digest. Receipt and wheel SHA-256 are in
`SRAC-20260923-9d21fca8837b-installed-wheel.json`. Live SDK process restart,
Windows detached-parent exit, and GitHub merge queue remain explicitly
unverified by this deterministic L3 gate.

## Atomic business answer wake (2026-09-23)

`answer` now commits the answer row, deduplicated audit event, and
`resume_requested` generation in one SQLite transaction when the run is
actually `needs_input`. A repeated identical answer is idempotent and does not
create another wake generation; conflicting answers remain rejected, and a
cancelled or non-waiting run cannot gain a wake intent. Non-waiting legacy
answer-only calls retain their prior behavior.

Impact: `store.py` answer/control transaction and `cli.py` answer route.
Selection `acceptance/test_production_planning.py`, `tests/test_cli.py`, and
`tests/test_store.py`: 40 passed in 9.36 seconds; full suite 120 passed, 1
skipped in 23.41 seconds. This verifies the transactional protocol and
same-stage resume path under the acceptance fake adapter. A later live
single-SPEC run (`SRAC-20260924-96136e690162`, run
`ceecb69c-6ccb-46c2-a633-93db1a6f8dfc`) also exercised real business answers:
the Grill worker entered `needs_input` twice, accepted seven persisted answers,
and resumed three turns on the same thread before planning continued. The run
completed implementation, independent review, six fixture tests, local merge
(`df94c892845e33a9f32467603d4a1789d89c9705`), workspace cleanup, and archive
readbacks. The review was approved with two open medium findings about test
isolation and replacement-failure coverage; those findings were non-blocking
under the configured severity gate. This is live single-SPEC evidence, not
evidence for kill/restart at every answer/wake boundary, installed-wheel L3, or
the remaining project-level L4/L5 gates. L0 diff check passed; exact installed
artifact proof remains due after source changes are finalized.

## Exact-wheel replay at `135bf93` (2026-09-24)

The current pushed source was built as a wheel and installed into a clean
Windows Python 3.13.5 virtual environment with `PYTHONPATH` cleared and the
declared `openai-codex==0.155.1` dependency installed. Import resolved from
`site-packages`; package inspection passed with 32 members and no forbidden
members. Wheel SHA-256:
`93f14755cb7a03db8dd1a7c272fb353a259a09fed1b43f44cc30fa7a24da2d26`.

Two installed-wheel public-CLI fault-matrix replays each passed all 10 cases
with identical digest
`66a26b57ae37f4974a38d67f3e37512c41a241b1b6f7dae862464ec2eecabfb7`.
The durable receipt is
`SRAC-20260924-facd52576608-installed-wheel.json`. These deterministic L3
replays do not establish live Codex process restart, Windows native parent
exit, GitHub merge queue, full three-SPEC GitHub delivery, source-thread
takeover, or Windows control DB/log recovery; each remains `not_verified`.

## Exact-wheel replay at `4892797` (2026-09-24)

The exact `48927971cb62498bc03fb363b7e2c48fbcb2df84` source was built into
`spec_runner-0.1.0-py3-none-any.whl` under marker
`SRAC-20260924-b986df18bf51`. The wheel was installed into a fresh Windows
Python 3.13.5 virtual environment with `PYTHONPATH` cleared and the working
directory outside the repository. Import resolved from that environment's
`site-packages`; `openai-codex==0.155.1` was installed. Package inspection
passed with 32 members and no forbidden members. Wheel SHA-256:
`02414068cd52d4e68f59cd7872953b68f4a8e4b64669286f244862f34cb2521d`.

Two installed public-CLI fault-matrix replays passed all 10 deterministic
cases with identical report digest
`04c608146b93dc114bebb8b306120f9a57709b2124b10d57b90c61ebb0545b5a`.
The durable receipt is
`SRAC-20260924-b986df18bf51-installed-wheel.json`. The source suite passed
`166 passed, 1 skipped, 1 deselected` in 52.05 seconds; the deselected test
requires the Codex SDK to be absent, while the current development environment
has it installed. An initial unfiltered run therefore failed only that
environment-specific assertion; no product failure was observed.

This L3 evidence is bound to `4892797` and does not establish live Codex
process restart, Windows native parent exit, GitHub merge queue, complete
three-SPEC GitHub delivery, source-thread takeover, or Windows control DB/log
recovery; each remains `not_verified`. GitHub issue #256 remains OPEN with its
acceptance checklist unchecked.

## Exact-wheel replay at `2377386` (2026-09-25)

The current pushed source was built into `spec_runner-0.1.0-py3-none-any.whl`
under marker `SRAC-20260925-l3-2377386`. The wheel was installed into a clean
Windows Python 3.13.5 virtual environment with `PYTHONPATH` cleared and the
declared `openai-codex==0.155.1` dependency installed. Import resolved from
that environment's `site-packages`; package inspection passed with 32 members
and no forbidden members. Wheel SHA-256:
`4b592f9756f04b83de5c897976cbaabf8b77ca5deaa8a5eb7956f021e10ab691`.

Two installed-wheel public-CLI fault-matrix replays each passed all 10
deterministic cases with identical report digest
`38c1df021ba884a99c5769bc2e11c24be05dbecffd689970a854c0c350643a7a`.
The durable receipt is
`SRAC-20260925-l3-2377386-installed-wheel.json`. These deterministic L3
replays do not establish live Codex process restart, Windows native parent
exit, GitHub merge queue, complete three-SPEC GitHub delivery, source-thread
takeover, or Windows control DB/log recovery; each remains `not_verified`.

## CI recovery check corrections (2026-09-24)

GitHub Actions run `35961463445` failed on Ubuntu and Windows. Both jobs lacked
`pytest`, although a Runner boundary test launches `python -m pytest`; the
workflow now installs `pytest` alongside `build`. Windows also exposed a test
that compared an unresolved temporary path with the resolved managed
worktree path; the assertion now resolves both paths. The missing-SDK adapter
test now explicitly hides `openai_codex` from imports, so its expected
`sdk_unavailable` result is independent of the developer environment.

Local verification after these corrections: Runner unittest `92 tests, 1
skipped`; source and acceptance pytest selection `172 passed, 1 skipped`;
thin-entry unittest `3 passed`; targeted SF-03.1 publication and delivery
selection `74 passed`; `git diff --check` passed. The two fixture-app test
directories with a duplicate module basename remain excluded from recursive
pytest collection, and the unit-test skip is environment-independent. The
GitHub Actions run `35963939388` passed both `contract (ubuntu-latest)` and
`contract (windows-latest)` on source commit `c99e1d2284d4c0487e3e43cf40b7341aead6a1e8`,
including Runner tests, thin-entry tests, wheel build/install, and the
installed public CLI. These are CI/test-harness corrections only: issue #256
remains OPEN and its checklist unchecked; complete three-SPEC delivery and
the outstanding project-level L3/L4/L5 gates remain `not_verified`.

## RCV-01.3 Runner recovery runtime wiring (2026-09-25)

This increment wires the existing structured fault observations and pure recovery
policy into the Runner boundary. Failed SDK receipts retain their structured
`fault_observation`; Runner failures now persist one stable episode per
run/operation/stage/generation, observations, deterministic decisions, counters,
and retry or service-wait deadlines. Accepted turns whose outcome is unknown are
recorded as `observe` and are never replayed automatically. Capacity failures are
bounded and escalate to `service_wait`; relaunches read the durable recovery
state instead of bypassing it. Public status includes the recovery evidence via
the existing Store projection.

Focused recovery and adapter/store checks passed: 55 passed, 1 skipped.
The explicit repository suite passed: 232 passed, 1 skipped. The new acceptance
coverage is `acceptance/test_runner_recovery_runtime.py`; it verifies capacity
budget persistence and service-wait escalation, accepted unknown execution
outcomes, and status readback of observations and decisions.

This is source and offline acceptance evidence for RCV-01.3. It does not prove
real provider capacity/Fast/404 incidents, live SDK same-thread continuation,
Windows detached-parent recovery, or the project-level L3-L5 release gates.
Those remain unverified until the corresponding live or installed-artifact
receipts exist.

## RCV-01.4 Recovery decision diagnostics (2026-09-25)

The recovery status projection now exposes a read-only
`spec-runner-recovery-diagnostic/v1` for each persisted episode. It explains
the incident family and evidence source, the operation/stage/generation, the
current action and next recovery condition, request admission and execution
outcome, route/model/effort/service-tier comparison, SDK retry observation
coverage, remaining budgets and deadlines, last verified progress, execution
owner, and cleanup debt. Unknown SDK retry counts remain `unknown`; an accepted
turn with an unknown outcome remains explicitly unconfirmed and cannot be
treated as business progress.

The Runner also records structured `fault_observed`, `reconcile_started`,
`retry_scheduled`, `retry_started`, `service_wait`, `route_changed`,
`probe_result`, `migration_requested`, `progress_verified`, and
`recovery_blocked` events where the corresponding deterministic decision is
made. The status path is read-only and does not advance the run or reset any
budget.

The table-driven policy and runtime acceptance selection passed `29 passed`.
The complete source and acceptance selection passed `234 passed, 1 skipped`
in 69.18 seconds after excluding the repository's duplicate fixture module
basenames. `compileall` and `git diff --check` were also run for the candidate.

This is source and deterministic acceptance evidence for RCV-01.4. It does not
prove a live SDK injected capacity/Fast/404 incident, a real provider incident,
Windows detached-parent recovery, clean migration under #292, or project-level
L3-L5 gates. Those remain `not_verified`; #296 and its parent remain open.

## RCV-02.1 durable continuation receipt and crash-window reconciliation (2026-09-25)

Continuation bundles now use a bounded business-context budget and retain only
schema-validated fields. The bundle digest is checked during construction and
again at Store registration. Workspace identity records the repository,
branch/base/head and bounded Git status, index, worktree, and binary-diff
digests; it does not copy thread history or hidden model material.

Each atomically written bundle is registered in the run's SQLite Store with
run/SPEC/stage/generation identity, input revision, canonical bundle path,
digest, workspace identity, and last verified progress. Registration is
idempotent for the same receipt and fails closed on identity or digest changes.
On relaunch with the same launch key, the Runner scans only that run's
continuation artifacts, validates the bundle and filename identity, and adopts
an unregistered file into the existing run. It never creates a second run for
this reconciliation path.

Acceptance coverage includes atomic file round-trip, forbidden encrypted or
opaque history, digest mismatch, total context budget, receipt readback,
process exit between file write and Store registration, and same-generation
receipt conflict. The continuation/store selection passed `13 passed`; the
explicit source and acceptance selection passed `240 passed, 1 skipped` in
70.21 seconds. `compileall` and `git diff --check` also passed.

This is source and deterministic crash-window evidence for RCV-02.1. It does
not prove a real native process crash, live SDK source-thread handoff, Windows
control DB or log recovery, clean migration under #292, or project-level L3-L5
gates. Those remain `not_verified`; #297 remains OPEN until its live and
project acceptance gates are separately evidenced.

## RCV-02.2 clean-thread migration and durable owner handoff (2026-09-25)

The takeover continuation path now persists a `thread_migrations` record before
creating a successor. The record binds migration key, run, stage, source thread,
handover digest, input revision, and owner generation. A successor is accepted
only after confirmed source handover; its formal SDK thread ID is registered
before the first business turn. Replays reuse the recorded successor, while
uncertain creation is fail-closed and cannot create a second writer. Owner
transfer uses a compare-and-set generation, and late old-generation events are
recorded as audit evidence without advancing the current owner.

The adapter exposes a clean-thread boundary that calls the published
`thread_start` interface without source thread, fork, prompt, or old response
history. Unit and acceptance coverage verifies clean identity, handover
blocking, migration identity conflicts, successor idempotency, uncertain-state
blocking, owner CAS, and stale-generation auditing. The focused source suite
passed `42 passed, 1 skipped`; acceptance migration coverage passed `1 passed`.

This is deterministic source and acceptance evidence for RCV-02.2. It does not
prove a live native SDK migration, OS-level old-writer termination, process
crash injection at every provider boundary, business recovery under #299, or
project-level L3-L5 gates. Those remain `not_verified`; #298 remains OPEN until
its live and downstream acceptance gates are separately evidenced.


## RCV-02.4 Windows control database and launcher recovery (2026-09-25)

The native Windows acceptance probe `SRAC-20260925-windows-control-db-restart`
held the real `spec-runner.sqlite3` in an independent SQLite `BEGIN EXCLUSIVE`
transaction, confirmed the detached Runner had reached its durable pause, and
terminated the recorded Runner while that lock was held. After the lock holder
released the database, the public `status` command read the same control DB and
`drive` recovered the same `run_id` to `completed`. The control DB remained on
disk and passed `PRAGMA integrity_check`; both launcher stdout/stderr logs
remained readable. The raw evidence is
`SRAC-20260925-windows-control-db-restart.json`.

The probe initially exposed two harness defects: its own integrity-check
connection was left open on Windows, and recovery removed the deterministic
stale-owner setting before invoking `drive`. Both are fixed and covered by
`acceptance/test_windows_control_db_restart_probe.py`. The focused acceptance
selection passed `2 passed`, and the full source plus acceptance selection
passed `254 passed, 1 skipped` in 78.94 seconds after excluding the repository's
known duplicate fixture test-module basenames. A literal recursive `pytest -q`
still fails collection on those five pre-existing fixture collisions, so that
project-level collection gate remains open.

This is real native Windows control DB and launcher-log recovery evidence for
#266 and one RCV-02.4 recovery gate. It does not prove source-thread takeover,
GitHub merge queue, complete A-J recovery, or the remaining project-level
L3-L5 gates; those remain `not_verified`, and #266/#300 remain open.


## Windows control DB lock fail-closed behavior (2026-09-25)

A native-Windows probe exposed that SQLite `database is locked` errors from
Store transactions escaped as raw `sqlite3.OperationalError`. The public CLI
therefore could not return a structured recoverable result while the control
database was exclusively locked. Store initialization and transaction handling
now map SQLite busy/locked errors to `control_database_busy`, while preserving
other SQLite error classes.

The extended Windows probe held the actual control DB with an independent
`BEGIN EXCLUSIVE` transaction and invoked public `drive`. It returned the
structured `control_database_busy` error in 7.808 seconds, terminated the
recorded detached Runner while the lock remained held, then released the lock
and recovered the same run to `completed`. The database remained present and
passed `PRAGMA integrity_check`; both launcher logs remained readable. Durable
receipt: `SRAC-20260925-windows-control-db-restart-locked-write.json`.

Focused Store and probe-contract tests passed (12 passed). The full explicit
source and acceptance selection passed `255 passed, 1 skipped` in 87.56 seconds
with `pytest tests acceptance --ignore=acceptance/fixture-app -q`. This verifies
one Windows control-DB lock boundary. It does not cover independently locked
launcher log rotation, all cleanup-pending combinations, source-thread
migration, or complete RCV-02.4 A-J / project L3-L5 gates; #266 and #300 remain
open.

## RCV-01.1 runtime-version provenance correction (2026-09-25)

Fault observations previously copied `client_version` into `runtime_version` when the explicit runtime field was absent. That made configured client metadata look like a runtime observation. The parser now populates `runtime_version` only from an explicit `runtime_version` field; a regression case verifies configured `client_version` remains unobserved and an explicit runtime value is retained.

The targeted recovery-policy, Runner recovery-runtime, and Codex-adapter selection passed `36 passed, 1 skipped`. The explicit source and acceptance selection passed `256 passed, 1 skipped` in 73.88 seconds using `pytest tests acceptance --ignore=acceptance/fixture-app -q` with this checkout on `PYTHONPATH`. `git diff --check` passed.

This corrects evidence provenance in RCV-01.1; it does not complete the ticket or prove live SDK injected-error recovery, provider incidents, or project-level L3-L5 gates. Those remain `not_verified`.

## RCV-01.1 worker and attempt provenance correction (2026-09-25)

The Runner recovery failure boundary now carries the durable worker ID and a persisted episode attempt number into `FaultObservation`. The acceptance case creates a real Store worker, records two capacity failures, and verifies the first observation is attributed to that worker with attempt 1. This closes an evidence attribution gap without changing the recovery policy or creating another worker.

The focused recovery policy and Runner runtime selection passed `22 passed`. The explicit source and acceptance selection passed `256 passed, 1 skipped` in 77.01 seconds using `pytest tests acceptance --ignore=acceptance/fixture-app -q`. `git diff --check` passed.

This is an incremental RCV-01.1 correction. Live SDK fault injection, provider incidents, and project-level L3-L5 gates remain `not_verified`; the related issues stay open.

## RCV-01.2 Retry-After precedence correction (2026-09-25)

Recovery decisions now honor a valid structured provider `Retry-After` value for capacity waits and retries. Invalid, negative, NaN, or infinite hints fall back to the configured local delay. A deterministic policy case proves a 17.5 second hint produces the corresponding next check instead of the five second default.

The focused recovery-policy selection passed `19 passed`. The explicit source and acceptance selection passed `257 passed, 1 skipped` in 73.00 seconds using `pytest tests acceptance --ignore=acceptance/fixture-app -q`. `git diff --check` passed.

This is an incremental RCV-01.2 correction. It does not prove a live provider capacity incident, automatic service wake-up, or project-level L3-L5 gates; the recovery issues remain open.

## RCV-01.4 cleanup-debt diagnostic correction (2026-09-25)

Recovery diagnostics now report cleanup debt only for an explicit `cleanup_pending` run state or an explicitly persisted cleanup-debt marker. `blocked`, `service_wait`, and `wait_retry` are control or recovery states and no longer appear as cleanup debt. Acceptance covers all three negative states and the positive cleanup state.

The focused recovery-policy selection passed `20 passed`. The explicit source and acceptance selection passed `258 passed, 1 skipped` in 81.48 seconds using `pytest tests acceptance --ignore=acceptance/fixture-app -q`. `git diff --check` passed.

This is an incremental RCV-01.4 accuracy correction; real SDK recovery and project-level L3-L5 gates remain `not_verified`.

## SF-05.1 Git reconciliation timeout boundary (2026-09-25)

The Runner's workflow Git reconciliation helpers now apply a bounded 120-second
command timeout to status, fetch, merge, push, and continuation workspace Git
reads. A timeout is converted to the structured `implementation_git_timeout`
error with the attempted arguments and timeout value, so a hung Git process
cannot leave the implementation phase indefinitely active. The acceptance
regression patches a real `subprocess.run` timeout and verifies both the error
code and the 120-second bound.

The focused timeout selection passed `3 passed`. The explicit source and
acceptance selection passed `259 passed, 1 skipped` in 79.48 seconds using
`pytest tests acceptance --ignore=acceptance/fixture-app -q`; `git diff --check`
and compile checks remain required before release packaging.

This is a bounded default increment for #264. The timeout is not yet a
RunnerConfig setting, and `canonical_repository` plus some delivery/takeover
Git helpers still lack the same structured configurable boundary. Live SDK
long-turn behavior, heartbeat/control-DB failure, duplicate detached-writer
prevention, and the complete SF-05.1/project L3-L5 gates remain
`not_verified`; #264 stays open.

## RCV-01.1 SDK failed-turn observation boundary (2026-09-25)

The Codex adapter now preserves typed `TurnError.codexErrorInfo` from a failed SDK result, including the public error code and nested upstream HTTP status when present. It maps the SDK enum form as well, keeps text-only SDK errors on an explicit fallback path, classifies the typed stream variants, and stores only the redacted public message in the worker receipt. The regression uses the installed `openai-codex==0.155.1` models and does not claim a live provider incident.

Candidate source revision: `b918f435f0077ecad9eb79d68555b9b57f161562`.

The focused adapter/recovery/SDK contract selection passed `38 passed, 1 skipped`. The explicit source and acceptance selection passed `263 passed, 1 skipped` in 83.20 seconds using `PYTHONPATH=spec-runner/src pytest tests acceptance --ignore=acceptance/fixture-app -q`; `git diff --check` passed before commit. This increment covers only the failed SDK result boundary. The four live provider entry paths, injected recovery against a real provider, and project-level L3-L5 gates remain `not_verified`; #293 and its parent remain open.


## RCV-01.2 recovery policy validation (2026-09-25)

`RecoveryPolicy` now rejects negative or boolean retry budgets and non-finite or negative retry/service-wait delays instead of silently clamping malformed configuration into a live policy. Persisted recovery decisions include the fixed `spec-runner-recovery-policy/v1` marker so a readback identifies which policy contract produced the budget. The change remains a pure policy boundary and does not claim a provider incident or automatic service wake-up.

Candidate source revision: `6b088572089016c01a657369d0be0307466084f5`.

The focused policy/store/adapter selection passed `39 passed, 1 skipped`. The explicit source and acceptance selection passed `264 passed, 1 skipped` in 87.46 seconds using `PYTHONPATH=spec-runner/src pytest tests acceptance --ignore=acceptance/fixture-app -q`; `git diff --check` passed before commit. This increment covers policy input validation and evidence versioning only. Atomic budget reservation, route circuit coordination, real timer wake-up, provider capacity behavior, and project-level L3-L5 gates remain `not_verified`; #294 and its parent remain open.

## RCV-01.3 durable recovery wait driver (2026-09-25)

The public `drive` path now owns the persisted `wait_retry` and `service_wait`
timer loop. It re-reads the durable recovery deadline and control row through
short-lived Store connections, polls at a bounded interval, applies pause or
cancel without creating another worker, and records one idempotent
`recovery_timer_woke` event before re-entering the existing launch-key recovery
path. Detached launch and `resume` now use this driver, while direct `start`
continues to perform one bounded workflow attempt. Missing or malformed
deadlines fail closed with a structured Runner error.

Candidate source revision: `79999222bc92a0b732e7f420bb0959852ed513e1`.

The focused Runner recovery-runtime selection passed `7 passed`; the CLI
selection passed `21 passed`. The explicit source and acceptance selection
passed `267 passed, 1 skipped` in 76.22 seconds using
`PYTHONPATH=spec-runner/src pytest tests acceptance --ignore=acceptance/fixture-app -q`;
`compileall` and `git diff --check` passed before commit.

This is deterministic timer and durable-control evidence for RCV-01.3. It does
not prove real SDK provider fault injection, live same-thread business
continuation, Fast/404 provider configuration changes, Windows detached-parent
recovery beyond the existing control-DB probe, or project-level L3-L5 gates.
Those remain `not_verified`; #295 and its parent remain open.

## SF-05.1 configurable Git timeout propagation (2026-09-25)

The Git timeout is now a validated `RunnerConfig` setting at
`git.timeout_seconds`, defaulting to 120 seconds and contributing to new
configuration digests. Repository canonicalization, workflow reconciliation,
local delivery, multi-SPEC delivery, and takeover Git reads/writes all receive
the configured bound. Public workspace, candidate, merge, delivery, and
takeover commands expose the same override. Timeout failures remain structured
as `repository_git_timeout`, `implementation_git_timeout`, or the existing
cleanup-specific timeout, with the attempted command and bound retained where
the operation is part of the implementation boundary.

The focused timeout and production boundary selection passed `15 passed`. The
explicit source and acceptance selection passed `271 passed, 1 skipped` in
71.98 seconds using `PYTHONPATH=spec-runner/src pytest tests acceptance
--ignore=acceptance/fixture-app -q`; `compileall` and `git diff --check` also
passed.

This is a bounded configurable increment for #264 and does not close the
issue. Live SDK long-turn or disconnect recovery, duplicate detached-writer
prevention across launch/control roots, complete cancellation and handshake
coverage, and the remaining project-level L3-L5 gates are still
`not_verified`. Existing targeted Windows control-DB and durable-wait probes do
not by themselves satisfy the complete #264 acceptance. #264 and its parent
remain open; #256 remains open with its previously recorded SF-03.1 evidence.

## SF-05.1 configurable GitHub command timeout (2026-09-25)

GitHub CLI operations now share a validated `github.timeout_seconds` Runner
setting, defaulting to 120 seconds. Production issue publication/closure and
PR/check/merge delivery receive the configured bound; the public tracker and
GitHub delivery commands also accept `--timeout-seconds`. Explicit values bind
to the config digest. Timeout errors retain the relevant command and bound and
continue to require reconciliation before a write is retried.

The focused CLI/tracker/PR/production-boundary/timeout selection passed
`85 passed`. The explicit source and acceptance selection passed
`275 passed, 1 skipped` in 82.04 seconds using
`PYTHONPATH=spec-runner/src pytest spec-runner/tests spec-runner/acceptance
--ignore=spec-runner/acceptance/fixture-app -q`. `compileall` and
`git diff --check` passed.

This closes the fixed-timeout gap for GitHub CLI calls only; it does not close
#264. Live SDK long-turn/disconnect recovery, duplicate detached-writer
prevention across launch/control roots, complete cancellation and handshake
coverage, and remaining project-level L3-L5 gates remain `not_verified`.
Existing Windows control-DB and timer probes cover their named cases, not the
whole SF-05.1 acceptance. #264 and its parent remain open.
## SF-05.1 control-plane fail-closed during an active SDK turn (2026-09-26)

The public Codex adapter turn watcher now treats a control-plane read failure as
unsafe to continue. It calls the SDK turn's interrupt boundary before returning
an explicit `sdk_control_unavailable` error, records the control exception type
and whether interruption succeeded, and returns
`sdk_control_interrupt_failed` when the SDK cannot be interrupted. A regression
uses the published turn seam with a blocking turn and a failing control read;
it proves the active turn is interrupted and the failure remains structured.

The affected control/recovery/timeout selection passed `63 passed, 1 skipped`.
The explicit source and acceptance selection passed `276 passed, 1 skipped` in
`76.88 seconds` using `PYTHONPATH=spec-runner/src pytest spec-runner/tests
spec-runner/acceptance --ignore=spec-runner/acceptance/fixture-app -q`.
`compileall` and `git diff --check` passed.

This is a bounded SF-05.1 control-plane increment. It does not prove live SDK
provider recovery, cross-launch/control-root duplicate-writer prevention,
complete cancellation/handshake coverage, or project-level L3-L5 gates; those
remain `not_verified`, and #264 plus its parent remain open.
## SF-05.3 rejected control-DB write does not advance state (2026-09-26)

The native Windows control-DB restart probe now records a SHA-256 digest before
and after the public drive attempt made while an independent process holds the
real SQLite database with BEGIN EXCLUSIVE. The attempt returned structured
control_database_busy in 7.798 seconds, the digest stayed identical, the known
Runner was terminated while the lock was held, and the same run later recovered
to completed. The database remained present and passed PRAGMA integrity_check;
two launcher logs remained readable.

The acceptance contract selection passed 2 passed. The related CLI/process
selection passed 24 passed in 16.99 seconds. Durable evidence is
SRAC-20260926-windows-control-db-rejected-write.json.

This proves the rejected control write did not advance the control DB in this
scenario. Independent launcher-log handle/rotation behavior, cleanup_pending to
cleaned replay, external side-effect reconciliation, and the remaining #266 and
project-level L3-L5 gates remain not_verified.

## SF-05.3 public cleanup replay on native Windows (2026-09-26)

The new Windows cleanup replay probe creates and merges a real candidate
worktree, persists matching production plan, ticket, merge, and run receipts,
then holds a Win32 delete-denying handle from a separate process. The public
`start` command returns `cleanup_pending` while that handle is held. After the
holder exits, the same public entry retries the persisted run and reaches
`completed`; the managed workspace and manifest are removed. The main branch
HEAD and merge receipt remain unchanged, and worker and operation counts do
not increase.

The native observation is recorded in
`SRAC-20260926-windows-cleanup-replay-01.json` (run
`2db6b503-4fb5-490b-8354-cdae84028a40`). The native probe completed through
the public CLI, the focused cleanup/production selection passed `38 passed`,
and the explicit source + acceptance selection passed `278 passed, 1 skipped`.
`compileall` and `git diff --check` passed. This covers the merged-worktree
cleanup replay only. Independent launcher-log handle/rotation/final cleanup,
combination with a control-DB lock, external GitHub side-effect reconciliation,
the remaining #266 criteria, and project L3-L5 gates remain `not_verified`;
#266 stays open.

## SF-05.3 independent launcher log handles (2026-09-26)

The native Windows probe launched a detached Runner through the public
`launch` command, held both recorded stdout and stderr launcher logs from a
separate process with delete sharing denied, and confirmed rotation was
blocked while those handles were held. The exact detached child was then
terminated by its recorded PID, the handles were released, both same log
objects rotated and remained readable, and public `drive` recovered the same
run to `completed`.

The observation is recorded in
`SRAC-20260926-windows-launcher-log-handles-01.json`. This is an OS-level
launcher log handle and restart observation. A production log-rotation command
and retention policy do not currently exist, so that capability remains
`not_verified`, as do the combined control-DB/log lifecycle, external GitHub
side-effect reconciliation, the remaining #266 criteria, and project L3-L5.

## SF-05.3 public launcher-log rotation boundary (2026-09-26)

Added the production `logs rotate` CLI and `log_runtime` module. It persists a
rotation intent before moving a stdout/stderr pair, creates fresh active files,
rolls back a partial move when possible, and replays the same rotation key for
retention cleanup. The public command rejects active runs, live recorded
processes, and durable writer leases before touching logs. Focused CLI, rotation,
and Windows-report contract coverage passed `29 passed`; the full source and
acceptance selection remains to be rerun before commit.

This is deterministic implementation evidence. A native Windows rerun of the
existing launcher-handle probe against the new public rotation command is still
required; the prior handle report remains evidence for OS-level locking only.
The control-DB/log combined lifecycle, external GitHub side-effect
reconciliation, remaining #266 criteria, and project L3-L5 gates remain
`not_verified`.

## SF-06.1 installed wheel cold replay (2026-09-26)

The current `c51a5be` source built `spec_runner-0.1.0-py3-none-any.whl` and
installed it into a fresh Windows virtual environment. The public CLI ran from
a Chinese/space-containing directory with `PYTHONPATH` cleared and the source
tree unavailable: `--version` returned `0.1.0`, `diagnose package` verified all
required package members with no forbidden runtime data, and the public
deterministic fault matrix passed all 10 cases.

The durable report is
`SRAC-20260926-cold-install-01.json`. This verifies the installed CLI/package
boundary and deterministic replay only; live SDK production delivery, live
GitHub side effects, Windows control-DB/log restart recovery, and source-thread
takeover remain `not_verified`.

## RCV-01.1 SDK exception and process-exit observation boundary (2026-09-26)

The Codex adapter now projects structured error data from SDK JSON-RPC and
transport exceptions into the same `FaultObservation` shape used by failed
turn results. It preserves the discriminated `codexErrorInfo` code and
`httpStatusCode` when the SDK exposes them, records the SDK exception type and
an explicit structured-versus-text evidence marker, classifies transport or
client-process exits as stream disconnections, and keeps admission/outcome
`unknown` when no formal turn identity exists. A failed turn with no SDK error
object also receives a structured fallback observation and receipt error.

The focused adapter/recovery/SDK contract selection passed `43 passed, 1
skipped`. The explicit source and acceptance selection passed `285 passed, 1
skipped` in `68.26 seconds` using `PYTHONPATH=spec-runner/src pytest
spec-runner/tests spec-runner/acceptance
--ignore=spec-runner/acceptance/fixture-app -q`; `compileall` passed.

This increment covers the SDK exception and result conversion boundary only.
The four real provider entry paths, live injected recovery, full attribution
matrix, and project-level L3-L5 gates remain `not_verified`; #293 and its
parent remain open.

## SF-05.1 detached launch identity and writer exclusion (2026-09-26)

The public detached `launch` path now reads and validates an existing
`launch_key` before creating a child UUID or launcher log. A terminal replay
returns the recorded run without spawning another process. An active replay
returns the same durable run and PID only when the runtime owner token still
matches its writer lease. Launchers for different control roots share a
repository/ref process lock, so an active writer cannot be raced by a second
detached child. The handshake also requires the child to hold the matching
durable writer lease; a runtime row by itself is not accepted as ownership.

The focused public CLI selection passed `12 passed`, covering terminal replay,
active replay, same repository/ref contention across control roots, input
conflict, relative paths, and the existing handshake cases. The explicit source
and acceptance selection passed `320 passed, 1 skipped`; `compileall` and
`git diff --check` passed.

This is deterministic process and SQLite evidence. Native Windows duplicate
launch under a detached restart, live SDK long-turn/provider recovery, complete
cancellation and handshake coverage, and project-level L3-L5 gates remain
`not_verified`; #264 and its parent remain open.

## SF-05.2 production control boundary before GitHub resume (2026-09-26)

The production workflow now consumes a persisted pause or cancel request at
the queue boundary and at the GitHub waiting-resume boundary. It records the
control application, moves the run to `paused` or `cancelled`, and returns the
durable status before selecting another SPEC or invoking GitHub delivery. This
preserves the existing candidate, review, merge and cleanup evidence; it does
not pretend that an already accepted external merge was undone.

Focused GitHub production boundary coverage passed `12 passed`, including
pause and cancel during CI or merge-queue waiting, with no GitHub adapter call;
production queue coverage also proves cancellation before selecting the next
SPEC. The explicit source and acceptance selection passed `323 passed, 1
skipped`; `compileall` and `git diff --check` passed.

This is deterministic production orchestration evidence. Public CLI recovery
against a real GitHub sandbox, live SDK interruption/provider recovery,
external side-effect reconciliation after an in-flight merge, complete
takeover-chain coverage, and project-level L3-L5 gates remain `not_verified`;
#265 and its parent remain open.

## SF-05.3 combined control DB and launcher-log restart (2026-09-26)

The native Windows combined probe held the real control SQLite database with an
independent `BEGIN EXCLUSIVE` process and held both recorded detached stdout and
stderr launcher logs with delete sharing denied. A public `drive` attempt
returned `control_database_busy` in 7.843 seconds, and the SQLite digest was
unchanged. The recorded detached child was terminated while both locks were
held. After releasing only the database lock, public `drive` recovered the same
run to `completed` while the log handles remained held; rotation returned the
structured `launcher_log_rotation_failed` error. Releasing the log handles and
replaying the same rotation intent succeeded, the rotated logs were readable,
and the control database remained present with `PRAGMA integrity_check=ok`.

Durable evidence is
`acceptance/reports/SRAC-20260926-windows-control-db-launcher-logs-01.json`,
run `698d9970-4e51-4568-a4ab-7bec4201f1d3`. The native report contract
selection passed `2 passed`; the explicit source and acceptance selection after
this increment passed `325 passed, 1 skipped`; `compileall` and
`git diff --check` passed.

This closes the combined Windows control DB and launcher-log lifecycle scenario
covered by the probe. Live GitHub side-effect reconciliation, source-thread
takeover, the remaining #266 criteria, and project-level L3-L5 gates remain
`not_verified`; #266 remains open.

## SF-05.3 combined probe replay (2026-09-26)

The combined native Windows probe was replayed after the public answer-contract
change. It again held the control SQLite database and both detached launcher
logs, rejected the locked `drive` with `control_database_busy` in 7.799 seconds
without changing the database digest, terminated the exact recorded Runner,
and recovered the same run to `completed`. Log rotation remained blocked while
the handles were held, then succeeded and was replayable after release. The
durable receipt is
`acceptance/reports/SRAC-20260926-windows-control-db-launcher-logs-02.json`;
the database passed `PRAGMA integrity_check` and both rotated logs were
readable.

This confirms the covered Windows lock and restart scenario on the current
candidate. It does not discharge live GitHub side effects, source-thread
takeover, the remaining #266 acceptance items, or project L3-L5 gates.

## SF-02.3 answer contract boundary (2026-09-26)

The public `answer` command now reads the durable worker question before
writing. It requires the question ID and receipt input digest to match the
waiting run, and validates offered choices. Unknown questions, stale receipts,
and invalid choices fail before `run_answers` or the resume intent changes;
valid answers keep the existing atomic answer-plus-wake behavior and SQLite
schema. Added CLI and acceptance negative cases, plus the source and
acceptance selection, passed `330 passed, 1 skipped`.

This is deterministic public-entry evidence for the answer contract. A live
single SPEC using the current Skill, real implementation/review workers, and
the full production delivery path remains `not_verified`; #254 and its parent
remain open.

## RCV-01 recovery attempt identity and observation retention (2026-09-26)

Recovery observations now retain distinct provider requests even when they
share a thread and turn. Their durable observation identity uses the request
identity when available, so a later request cannot be silently deduplicated as
the earlier attempt. When a provider explicitly rejects a request but supplies
neither request nor turn identity, the Runner records an `observe` decision
with `attempt_identity_missing` and does not issue a retry or consume a
recovery budget reservation. Existing metadata-only route failures with
unknown admission retain their bounded route-probe behavior.

Acceptance coverage added two cases for these boundaries. Focused recovery
runtime plus route-circuit coverage passed `11 passed`; the explicit source
and acceptance selection passed `327 passed, 1 skipped`; `compileall` and
`git diff --check` passed.

This is deterministic recovery safety evidence. Live provider incidents,
same-thread business continuation after an injected live fault, clean source
thread takeover, and project-level L3-L5 gates remain `not_verified`; RCV-01,
RCV-02, and their dependent SF tickets remain open.
## Architecture recovery episode seam and exact wheel replay (2026-09-27)

Commit `1ce0ecfc208cfc24cd2a1dad393afb451ba069f9` binds the recovery
coordination interface to a `RecoveryEpisode(run, store)` value. The public
CLI, SQLite schema, event shapes, and receipt formats remain unchanged. The
source and acceptance selection passed `355 passed, 1 skipped`; compileall and
`git diff --check` passed.

The exact candidate wheel was installed in a fresh Windows environment with
the source tree unavailable and `PYTHONPATH` cleared. Package inspection passed,
and two installed public fault-matrix replays passed all 10 cases with the same
report digest. Evidence is
`acceptance/reports/SRAC-20260927-1ce0ecf-installed-wheel.json`.

A new live three-SPEC run was attempted from the same SHA. The first SDK Grill
turn was rate limited after accepted request admission, with execution outcome
unknown; no GitHub resources were created. The run is recorded as
`not_verified` in `acceptance/reports/SRAC-20260927-abcdef123456-production.json`.
The live SDK, GitHub delivery, source takeover, six-SPEC L4, and final L5 gates
remain open.

The final follow-up refactor moved the recovery persistence implementation into
`RecoveryEpisode`; `RecoveryRuntime` remains only as a compatibility facade.
Commit `46b22d9452238a1fc7664753ad1b5a47f942e5e6` passed the complete source
and acceptance selection with `355 passed, 1 skipped`. Its exact installed
wheel passed package inspection and two deterministic 10-case fault replays
with identical digest `e97931787be888eb6ccea1b4c3cc477dd21dd023aeb87201965d62b0fe069efe`.
Evidence is
`acceptance/reports/SRAC-20260927-46b22d9-installed-wheel.json`.

## Production planning seam and current live retry (2026-09-27)

Commit `929dff4` moved the initial production planning transition behind
`ProductionWorkflow.plan()` and kept `workflow.py` as the compatibility
adapter. Its focused planning and Runner interface selection passed `63
passed`; the complete source and acceptance selection remained green at
`355 passed, 1 skipped` before the merge-only fixture addition.

The run marker `SRAC-20260927-c0de1234abcd` was started through the public
single-SPEC production entrypoint from commit `de26e63`. The first SDK Grill
turn was accepted and then exhausted on HTTP 429, with execution outcome
`unknown`; the exact thread and turn identities were recorded. No issue, PR,
branch, or other GitHub resource was created. Evidence is
`acceptance/reports/SRAC-20260927-c0de1234abcd-production.json` and remains
`not_verified`. The durable SQLite run is
`3712b6d2-d74d-4016-96d5-982adedb9901` and its final state is `blocked` after
the recovery readback; the SDK thread ID is retained separately as the
external worker identity.

The 429 is an external capacity blocker for live production qualification. It
does not change deterministic implementation status and does not close SF-02,
SF-03, SF-04, SF-05, SF-06, RCV-01, RCV-02, or SF-00.

## Same-thread recovery readback (2026-09-27)

The same public `resume` entrypoint was used with the existing launch key after
the first terminal failed turn. The Runner read back the original SDK thread,
continued on that thread, and received another HTTP 429. No second run, thread,
issue, PR, branch, or other GitHub resource was created. The durable run remains
`blocked`; the recovery episode has two capacity observations and zero remaining
capacity retry budget, with `request_admission=accepted` and
`execution_outcome=unknown`. The external thread identity remains
`01a0dfe8-197b-7742-9748-d7aa68bf3072`, while the durable run identity remains
`3712b6d2-d74d-4016-96d5-982adedb9901`.

This proves same-thread recovery accounting and the no-duplicate-writer guard
for this live incident. It does not prove successful production delivery or
discharge the live SDK, GitHub, takeover, Windows, L4, or L5 gates.

## Recovery episode stage identity correction (2026-09-27)

The live run exposed a real recovery defect: a failure raised while the
production entry was moving from Grill into planning could be recorded against
the stale `codex_planning` identity, and the next same-run failure could open a
second episode for `codex_grill`. The compatibility workflow now reloads the
latest durable Run record before recording recovery, so the episode stage,
incident identity, and capacity budget follow the latest persisted worker
intent across the transition.

The focused recovery selection passed `11 passed`; the complete source and
acceptance selection passed `357 passed, 1 skipped`. `compileall` and
`git diff --check` passed. This corrects deterministic recovery accounting and
does not promote the blocked live SDK run or any project-level live gate.

## Runner architecture deepening and same-run ticket recovery (2026-09-27)

The compatibility façade and typed Runner seam were deepened in three small
commits: `4015a00` isolates the legacy workflow calls behind one injected
adapter, `7aee657` centralizes durable failure state transitions in
`RecoveryRuntime`, and `7c64c3c` routes stage execution through the same port.
The public workflow entrypoints, CLI JSON, SQLite schema, and receipt formats
remain unchanged. The targeted seam and recovery selections passed, and the
complete source plus acceptance selection passed `365 passed, 1 skipped`.
`compileall` and `git diff --check` passed.

The existing live run
`c6da8825-7a9c-4811-8b04-59fbffc8d316` was driven again with its original
launch key after the refactor. Runner reused ticket-planning thread
`01a0e07c-526a-7e41-b4aa-83e252d00972`, recorded the new turn
`01a0e0af-c889-79f0-bca8-5bc39d70b57d`, and received another HTTP 429 with
accepted request admission and unknown execution outcome. No second run,
thread, ticket, PR, branch, or merge was created. The durable run remains
`blocked` at `codex_ticket_planning` and the live production, GitHub,
takeover, Windows, L4, and L5 gates remain `not_verified`.

The current candidate wheel was replayed from commit `ec1752c` in a fresh
isolated environment with source imports unavailable. Package inspection
returned `verified`; both public deterministic fault replays passed all 10
cases with identical report digest
`ee2030144ba2de23203579f55925d3db6dbf56e83ee187ffbb0af9c23cf5bca0`.
Evidence: `acceptance/reports/SRAC-20260927-bf9ee0008d4f-installed-wheel.json`.
This remains installed-artifact and deterministic evidence only.

## SF-05.3 Windows lock replay on the current candidate (2026-09-27)

The native Windows combined control-database and launcher-log probe passed on
the current candidate. It held both independent locks before terminating the
known detached child, observed the bounded public `control_database_busy`
failure without database advancement, recovered the same run to `completed`
while log handles remained held, and replayed the rotation intent after log
release. Control DB integrity remained `ok` and rotated logs were readable.
The probe run is `c247b04e-d0b7-4dcb-8dd6-e9ccb4809403`; evidence is
`acceptance/reports/SRAC-20260928-windows-control-db-launcher-logs-architecture.json`.
This covers the exercised Windows combination and does not complete the
remaining #266 or project-level L3-L5 acceptance gates.

## Prepared production intake and live implementation limit (2026-09-27)

Commits `d3e307b` and `f6bc7ca` add explicit prepared SpecPlan/TicketPlan
intake to the public production `start` path and adopt matching local tracker
records across runs. The prepared stages persist their source digests and
receipts; a resumed run can replay from its artifacts after source removal.
`af03f24` preserves completed Stage and worker states when a later Stage fails.
The final focused recovery/planning selection passed 85 tests; the complete
source plus acceptance selection passed `381 passed, 1 skipped`. `compileall`,
`git diff --check`, and Ubuntu/Windows contract CI `36293368897` passed.

A live prepared-plan run `56335d20-5e36-4be8-bc15-875eedb42743` reached a
real `codex_implementation` SDK thread after adopting both plans and publishing
the local SPEC/ticket. Its first turn and a subsequent same-thread drive each
received SDK HTTP 429 after accepted request admission; the run is `blocked`
without a verified candidate or delivery. The earlier live run
`9d60c002-16ae-455d-b6df-93a47d4b9bbc` cannot resume because its recorded
implementation worktree is missing; the last continuation snapshot described
that worktree as dirty. Neither run proves a completed production SPEC. GitHub
three-SPEC delivery, source takeover, full Windows recovery, and project-level
L3-L5 remain `not_verified` where their separate reports require live evidence.

## Failed-turn capacity reconciliation (2026-09-27)

Commit `4dd3577` makes process-exit recovery apply the persisted fault policy
after readback proves an accepted SDK turn failed. The same attempt retains its
budget reservation and wait deadline across replays. Source and acceptance
tests passed `387 passed, 1 skipped`; `compileall`, `git diff --check`, and
Ubuntu/Windows contract CI `36295322422` passed.

The existing run `56335d20-5e36-4be8-bc15-875eedb42743` was resumed once
from its original launch key. Readback settled the second 429 turn as failed,
kept the capacity count at two, and entered durable `service_wait` without an
immediate third SDK turn. After that wait expired, one bounded same-thread
attempt returned another HTTP 429. Its turn
`01a0e134-141f-79a2-8b34-fa3b4432ebe2` was reconciled, capacity count became
three, and the run returned to `service_wait`. No verified candidate, review,
PR, or merge was produced. The live business continuation, bounded long-term
service probing, remaining Windows and takeover cases, and project L3-L5 gates
remain `not_verified`; #295 remains open.

## Bounded capacity service probes (2026-09-27)

The recovery policy is now `spec-runner-recovery-policy/v2`. After the two
short same-thread capacity retries, it permits three persisted service-wait
probes in total. A fifth capacity failure records
`capacity_probe_budget_exhausted` and stays blocked; it cannot start an
unbounded sixth business turn. A provider `Retry-After` beyond the one-hour
policy limit becomes `wait_for_config` rather than scheduling an unbounded
timer. Historical v1 decisions remain readable as evidence from the earlier
policy contract.

The focused recovery selection passed `69 passed`; the complete source and
acceptance selection passed `396 passed, 1 skipped`; `compileall` and
`git diff --check` passed. This is deterministic policy and replay evidence.
The live provider capacity incident, successful business continuation, and
project-level L3-L5 gates remain `not_verified`.

## Interrupted implementation control readback (2026-09-27)

The production implementation now handles an SDK `interrupted` result before
input gating or candidate delivery. A matching durable pause/cancel request
atomically updates the run, implementation step, and worker; an interruption
without such a request remains an error. A failed SDK result retains its
structured fault observation instead of being replaced by an artifact error.
Process-exit recovery applies the same control only after readback verifies
the original thread and turn are idle and interrupted. The SDK read adapter
also projects root-wrapped file-change kinds into JSON-safe values.

Focused planning/recovery/adapter tests passed `86 passed, 1 skipped`; full
source plus acceptance passed `404 passed, 1 skipped`. `compileall` and
`git diff --check` passed. The existing live run
`56335d20-5e36-4be8-bc15-875eedb42743` was reconciled through public
`start` with its original launch key. Its run, step, and worker are now
`paused` on turn `01a0e188-ba5c-7b02-bb85-6481c357f2bc`; worker count
remains three, capacity attempts remain three, and no writer lease remains.
This proves pause readback, not successful business continuation. No verified
candidate, review, PR, merge, three-SPEC delivery, or project-level L3-L5
acceptance is claimed; #264 and #295 remain open.

## Completed implementation adoption after live resume (2026-09-27)

The original live run resumed on the same implementation thread and completed
turn `01a0e1a2-ca04-77c0-8822-ca01e99c369e`, producing two Python source
files inside its trusted fixture write scope. Initial candidate validation
rejected 274 out-of-scope `.pyc` paths. Of those, 272 ignored cache files
were moved into the run's control-root quarantine; two tracked cache files
were restored to the managed worktree HEAD. The first quarantine command had
a PowerShell path-expression error, so the original modified bytes of those
two tracked cache files were not retained. Product source files were not
removed. After cleanup, the scope scan saw only four allowed paths.

Process-exit recovery also exposed a duplicate-turn risk: it treated a
completed worker's local-test blocker as a reason to retry and looked for
write-scope-relative artifacts at the workspace root. The retry identity
check now accepts a completed outcome with existing scoped artifacts, while
still retrying when the artifact is missing. A focused regression passed.
Public `start` then adopted the original completed implementation turn,
verified the candidate, and started an independent reviewer without another
implementation turn. The reviewer did not finish before a public pause
request; its interrupted result was recorded as rejected and the run is
`blocked`. Review pause reconciliation, review approval, delivery, and all
project-level completion gates remain open.

## Paused reviewer recovery on the original thread (2026-09-27)

Commit `74ddaf8` lets a controlled interrupted review persist `paused` or
`cancelled` before review validation. A blocked run with a paused reviewer and
an `external_result_unreconciled` episode may enter read-only reconciliation
again. The coordinator requires the original idle thread, the exact
interrupted turn, and the matching candidate receipt before continuing on the
same thread. An outstanding pause/cancel request is applied before a new turn.
Public `Runner.start` regressions cover the blocked episode and all three
control states. Full source and acceptance tests passed `410 passed, 1
skipped`; source/test `compileall` and `git diff --check` passed. CI run
`36303976651` passed Ubuntu and Windows contract jobs, including installed
wheel CLI checks.

The live run `56335d20-5e36-4be8-bc15-875eedb42743` kept four workers and
the original reviewer thread `01a0e1ab-2d05-7e50-87c2-b153e057209b`.
Public `resume` started turn `01a0e1ca-70f5-7973-8c0a-8f114a067e4f` on
that thread; the reviewer was still running at the time of this report. No
review approval, PR, merge, or next SPEC is claimed. The run's earlier
capacity budget was not reset.

An initial attempt was blocked by two tracked `.pyc` files changed by this
operator's broad `compileall acceptance` command, which traversed the live
candidate worktree under `.runtime`. Both modified byte streams were copied
and hash-checked in the run's `quarantine-compileall-pyc` artifact directory
before restoring those two tracked cache paths. The candidate worktree was
clean before the successful resume. Later compile checks exclude the runtime
tree. This was local test contamination, not a confirmed SDK write.

SF-05.1, RCV-01.3, the three-SPEC delivery, source takeover, and project-level
L3-L5 gates remain open where their separate live evidence is missing.

## Repair-turn reconciliation after real review findings (2026-09-27)

The original reviewer completed turn `01a0e1ca-70f5-7973-8c0a-8f114a067e4f`
and rejected candidate `82b31ee`: Python's strict CSV reader still accepted a
bare quote in an unquoted field. The existing implementation thread produced a
repair turn and a contract-correct artifact receipt. Recovery initially could
not adopt those completed turns because it compared their persisted TicketPlan
base to a newer `master`, and interpreted write-scope-relative artifact paths
differently from the formal candidate gate. Commit `526789f` validates the
repair against its persisted base and accepts either relative path spelling
only when the resolved file is within the trusted write root. Source and
acceptance tests passed `412 passed, 1 skipped`; source/test `compileall` and
`git diff --check` passed.

CI run `36305020232` passed both Ubuntu and Windows contract jobs, including
the installed-wheel CLI checks.

The live repair validation rejected 274 ignored `.pyc` files outside the
trusted write scope. All 274 were moved, preserving their bytes, to the run's
`quarantine-repair-pyc` artifacts. The gate then admitted the existing repair
as candidate `7317869` and started a fresh independent review. At this report
the reviewer turn `01a0e1e0-5897-7111-9c16-9d87dbf7a061` is running and
the candidate worktree is clean. Review approval, delivery, subsequent SPECs,
and project-level L3-L5 are still `not_verified`.

## Approved repair and advanced target integration gate (2026-09-27)

The fresh reviewer approved candidate `7317869` with no blocking findings.
Its review receipt binds turn `01a0e1e0-5897-7111-9c16-9d87dbf7a061` to
that exact SHA. Delivery then stopped at `target_ref_changed` because `master`
had advanced since the run's original base. Commits `3291b5a` and `df5fb85`
permit an approved reviewer to be revalidated on the same run, and require a
trusted integration check in a disposable merge worktree before atomically
updating the current target ref. The full source and acceptance selection
passed `414 passed, 1 skipped`; source/test `compileall` and `git diff --check`
passed. Both commits passed Ubuntu and Windows contract CI.

Live integration remained blocked: the trusted `FIXTURE_SCOPE` command embeds
the run's original base SHA. On the proposed merge commit it sees the
intervening mainline changes as out of scope, so the required check fails and
`master` is not updated. Commit `61bf8e7` makes a clean failed integration
worktree removable for a later retry; its focused regression and Ubuntu/Windows
contract CI `36306200295` passed. The one
pre-fix disposable merge worktree was checked clean and removed with Git.
Candidate branch `spec-runner/SRAC-20260927-f1a2b3c4d5e6-56335d20` remains
unmerged and attached to this run. An acceptance contract revision or an
equivalent durable integration gate is still needed before delivery can be
claimed. No SPEC completion or project-level L3-L5 result is inferred.

## Live candidate integration and local delivery (2026-09-27)

Commit `58b947f` marks the original-base `FIXTURE_SCOPE` check as candidate-only.
The revised acceptance configuration retains the run's original config digest
under the explicitly checked compatibility rule. Integration still runs the
other required check and the built-in changed-path scope gate. The original
candidate-only check receipt is verified and carried in the merge receipt;
it is not represented as a check rerun on the merge commit. CI `36307034844`
passed Ubuntu and Windows contract jobs for this change.

Public `start` on the original launch key and run
`56335d20-5e36-4be8-bc15-875eedb42743` recorded `spec_completed`. The
delivery receipt binds independently approved candidate `7317869`, verified
integration SHA `554007bbe864a9cea95bb03945c1e3c3f2ccdecd`, the
candidate-only check evidence, and a clean disposable merge workspace. The
integration check ran the fixture oracle (`9 passed`) and confirmed the
candidate's two changed paths are within its trusted write scope. The local
`master` ref advanced from `58b947f` to `554007b` and was pushed to
`origin/master`; the merged candidate branch was deleted.

Because local merge updates the target ref without resetting the checked-out
user workspace, the two newly merged fixture files initially appeared as
staged deletions in that checkout. The checkout was clean before this run;
both paths were restored exactly from the verified merge commit. No user
changes in the separate original checkout were touched. The resulting worktree
is clean. Full source and acceptance tests passed `415 passed, 1 skipped`;
the merged fixture's own oracle passed `9 passed`; `git diff --check` passed.
CI run `36307449876` passed Ubuntu and Windows contract jobs, including
installed-wheel public CLI verification.

This is one real, locally merged fixture SPEC. It does not meet #258's
three-SPEC GitHub PR, CI, issue closure, archive, cleanup and next-base
acceptance. The full #264 and #295 fault matrices, remaining open SPECs, and
project-level L3-L5 gates remain `not_verified` where their separate live
receipts are missing.

## SF-03.2 duplicate CI check-run selection (2026-09-27)

The GitHub delivery check reader could ignore malformed entries in a paginated
check-run response and then accept a successful status context. It also chose
same-name runs by completion time, allowing an older run that finished later
to mask a newer running attempt. Both false-ready paths were reproduced by
contract regressions before the fix.

The reader now rejects incomplete check-run pages and selects the latest
same-name attempt by creation/start time and run ID. A queued attempt without
a start time uses its newer run ID and remains pending. Focused GitHub delivery
tests passed `34 passed`; full source and acceptance tests passed `418 passed,
1 skipped`; source/test compileall and `git diff --check` passed. Public
`github-delivery checks` read back the exact `af8ce41` GitHub Actions SHA and
confirmed both required Ubuntu and Windows contract checks ready with no
missing, pending, failed, or wrong-SHA result.

This is a production CI gate correction, not live evidence of duplicate
check-run recovery or a protected PR/merge queue. #257 and project-level
gates remain open where those separate receipts are missing.

## Historical control database status readback (2026-09-27)

The public `status` command raised a raw SQLite `no such table:
recovery_episodes` error for an older completed production run. That database
predates the additive recovery, continuation, migration and route-circuit
status tables, while retaining the same metadata schema version. The Store
already handled the absent route-circuit table; its remaining optional status
projections now use the same read-only table-existence boundary. An entirely
absent feature table group yields empty evidence. The three core recovery
tables arrived together and must be complete; budget reservations and migration
milestones were added later and may legitimately be absent. A partial core
recovery group or a milestone table without its migration parent raises
`control_not_ready` instead of hiding corruption.

The current public CLI read the original 2026-09-24 SQLite run
`ceecb69c-6ccb-46c2-a633-93db1a6f8dfc` as `completed`, with six workers
and 31 events. Recovery, continuation and migration projections were empty.
The database SHA-256 was identical before and after the read:
`8043ebd7c3f4244627c0af38dc55ee4a9e92dd5cde995bd0d8dc650eaaa3c457`.
No migration or SDK turn was performed, and the original checkout's user edits
were untouched. The focused Store/CLI selection passed `46 passed`; full
source and acceptance passed `419 passed, 1 skipped`; source/test compileall
and `git diff --check` passed.

This corrects one #264 read-only compatibility defect. It does not verify the
remaining live SDK, multi-process writer, full cancellation or project-level
L3-L5 requirements.

## SF-03.2 GitHub CI pagination count (2026-09-27)

The check-run and combined-status readers previously accepted a syntactically
valid page even when GitHub's `total_count` exceeded the number of returned
items. A missing newer check attempt or status context could make a required
check appear ready. Both readers now reject a present count that differs from
the flattened pages, including inconsistent or invalid counts across pages.
Public `GitHubDelivery.checks` regressions cover both incomplete responses.

The focused delivery selection passed `17 passed`; full source plus acceptance
passed `421 passed, 1 skipped`. The public `github-delivery checks` command
read GitHub's actual `750d2d4` master SHA and found both Ubuntu and Windows
contract checks ready. GitHub returned two check-runs with `total_count=2`,
and zero status contexts with `total_count=0`. This confirms the new count
check accepts the current real repository response; it does not prove a live
truncated-page incident. Protected PR delivery, merge queue behavior, and
project-level L3-L5 remain `not_verified`; #257 stays open.

## SF-02.2 / SF-05.1 recovered local delivery queue (2026-09-27)

The existing live run `56335d20-5e36-4be8-bc15-875eedb42743` had a durable
approved candidate, local merge and worker archive receipts, but remained at
`spec_completed` without a transactional queue completion. Process-exit
recovery returned before resuming the queue. A subsequent start could select
the already delivered SPEC again and attempt new ticket planning.

Recovery now continues through `ProductionWorkflow` before returning. Local
delivery persists its candidate-workspace cleanup and SPEC completion before
returning to the queue. For historical unrecorded local frontiers, the
production adapter checks matching run/plan/ticket/candidate/review identities,
the durable independent reviewer and exact archive receipts, actual Git
ancestry for candidate/merge/current target, and owned workspace cleanup or
absence confirmed by the Git worktree registry. It then records completion
and selects the next dependency-ready SPEC. Pause/cancel is checked before
reconciliation and terminal queue completion.

Sixteen public Runner cases cover start and process-exit recovery, terminal
replay, next-SPEC dispatch, wrong review SHA, missing archive and an unowned
workspace. They use simulated workers and a real temporary Git repository.
The complete source plus acceptance selection passed `437 passed, 1 skipped`
in 101.11 seconds; source/test compileall and `git diff --check` passed. Two
older private seam tests were updated to supply the newly required completion
boundary, without relaxing the production evidence checks.

Public `start` reused the original live launch key and recorded `completed`
with one `production_spec_completed` event. Readback retained six workers,
capacity attempts `3`, and the prior same-thread counters; no new turn,
merge or run was created. The completed-spec receipt binds delivery digest
`a01ffa9227b10076943966412b4353260f877a08722154f4e72691a8045ace99`.
Final public status and terminal replay show no writer lease. The original
three `dy-video-download` user edits were untouched.

Independent `codex review --uncommitted` session
`01a0e248-57d2-7523-baa6-c0d9f264d8fa` was interrupted by the provider's
usage limit, with an explicit retry time of 18:45 local. It produced no
completed review result and is not an approval. No quota or recovery budget
was reset. This increment does not prove local ticket closure, the complete
SF-02.2 rejection/cleanup matrix, three-SPEC GitHub delivery, protected merge,
source takeover, or project L3-L5. #253, #264 and their parents remain OPEN;
independent review remains pending.

## SF-02.2 local tracker closure and replay (2026-09-27)

The local production path now closes the published SPEC and ticket only after
verified candidate, independent approved review, merge, archive and workspace
cleanup evidence have been reconciled. The immutable published Markdown stays
unchanged; an atomic tracker state file and a Store external-operation receipt
record the close intent, exact publication identity and readback. A failed close
leaves `cleanup_pending`, and replay performs closure without starting another
implementation or review worker. An already completed historical run keeps its
recorded delivery receipt and completion digest unchanged.
The normal local path records `cleanup_pending` before deleting its workspace,
so a process exit during cleanup returns to this same replay frontier. The
workflow-control regression asserts that ordering before invoking cleanup.

Public Runner acceptance cases cover ordinary closure, terminal replay,
historical completion, legacy ticket-source layout, close failure, exit after
tracker state write, a locked cleanup retry, state tampering, and multi-SPEC ticket isolation. The
legacy publication replay preserves the original cumulative snapshot and
receipt even when earlier SPEC records share the source directory.
The tracker test covers two SPECs closed independently against one published
snapshot, idempotent replay and unchanged published bytes. The affected
planning, tracker and workflow-control selection passed `122 passed`. The
complete source plus acceptance selection passed `445 passed, 1 skipped` in 109.81 seconds;
source/test `compileall` and `git diff --check` passed.

The original live run `56335d20-5e36-4be8-bc15-875eedb42743` was resumed
with launch key `SRAC-20260927-f1a2b3c4d5e6-prepared`. Public `start` returned
`created=false`, `completed` on the same run. Tracker state readback shows
`SRAC-20260927-f1a2b3c4d5e6` and its `.1` ticket closed; the Store close
operation is `completed`. The run still has six workers and no writer lease.
Its original Store completion digest remains
`a01ffa9227b10076943966412b4353260f877a08722154f4e72691a8045ace99`.
No new run, SDK turn or recovery-budget reset was used.

Independent fixed-commit review session
`01a0e2ef-22bd-73d2-a216-231079511f9b` found one P2: a locked workspace's
`production_cleanup_pending` escaped cleanup replay and could turn the run
`blocked`. Replay now persists and returns `cleanup_pending` for that exact
condition, while other evidence errors propagate. Its new public Runner
regression passed. The reviewer inspected the two-file correction and confirmed
the finding resolved with no remaining actionable defects in that diff; it did
not rerun tests. The earlier long-running review session
`01a0e2d1-d4d0-7683-822b-c7b228bba73d` was stopped after being superseded;
it has no completed approval result.

This is one local fixture delivery, not the three-SPEC GitHub PR/CI/closure
acceptance. The full SF-02.2 rejection and cleanup matrix,
source takeover, and project-level L3-L5 remain `not_verified` where
durable evidence is absent. #253, #264 and their parents remain OPEN.

## SF-05.1 lease heartbeat fail-closed increment (2026-09-27)

The active Runner now keeps a process-local health signal for the durable
repository writer lease. If the heartbeat cannot update the lease, the signal
is checked by the existing SDK control watcher. The watcher interrupts the
active turn before it can produce further external side effects. The resulting
`sdk_control_unavailable` receipt retains the source `writer_lease_lost` code,
thread and turn identity, and an `execution_outcome=unknown` fault observation
so recovery must reconcile the external result before creating another worker.

The increment adds no SQLite tables and does not alter CLI projections. Focused
lease and SDK control tests passed `18 passed, 1 skipped`; the affected public
Runner, CLI and recovery selection passed `60 passed, 1 skipped`; the complete
source plus acceptance selection passed `449 passed, 1 skipped`. `compileall`
and `git diff --check` passed.

This is deterministic fail-closed evidence. Live SDK provider recovery,
Windows detached multi-process ownership, complete cancellation and project
L3-L5 remain `not_verified`; #264, #263 and the remaining SPECs stay OPEN.

## SF-05.2 / SF-05.3 control database read fail-closed increment (2026-09-27)

The Store control-plane read now converts a SQLite `database is locked` result
from `run_controls` into the existing structured `control_database_busy`
Runner error. This covers production queue control-boundary reads as well as
the active SDK control watcher, so an exclusive control-database lock cannot
escape as a raw SQLite exception or be mistaken for an absent control request.

A real temporary SQLite database with an independent `BEGIN EXCLUSIVE` holder
proves the read path returns the structured code and bounded diagnostic. The
focused Store, GitHub production-boundary and Windows control-db selections
passed `35 passed`; the complete source plus acceptance selection passed
`450 passed, 1 skipped` in 140.13 seconds. `compileall` and
`git diff --check` passed. No schema or CLI projection changed.

This is local/deterministic lock evidence. Native Windows combined lock replay,
live GitHub side-effect reconciliation, full SF-05.2/SF-05.3 production
acceptance and project-level L3-L5 remain `not_verified`; #265 and #266 stay
OPEN.

## RCV-01.1 / RCV-01.2 recovery progress accounting (2026-09-27)

The durable recovery runtime now consumes the existing `no_progress_attempts`
budget for failed unknown observations with a stable request or turn identity.
The reservation is idempotent across replay of the same attempt. Explicitly
verified progress is persisted, and a later empty progress value cannot erase
that receipt. The pure policy reports `no_progress_budget_exhausted` when the
persisted budget is exhausted; control requests and accepted unknown execution
still take precedence. Existing capacity service-wait and route/clean-migration
budgets retain their prior behavior.

Focused recovery policy/runtime tests passed `48 passed`. The affected Store and
workflow-control selection passed `46 passed`. The complete source plus
acceptance selection passed `455 passed, 1 skipped`; `compileall` and
`git diff --check` passed.

This is deterministic durable accounting evidence only. Real provider fault
injection, successful SDK continuation, clean source-thread migration, GitHub
three-SPEC delivery, Windows full-path recovery and project-level L3-L5 remain
`not_verified`; #293, #294 and their parent EPICs remain OPEN.

## RCV-02.2 handover evidence enforcement (2026-09-28)

The clean migration Store now shares the takeover handover contract. A migration
cannot be advanced from `intent` to `handover_confirmed` unless the persisted
evidence identifies the source thread and proves source writer stopped,
dispatcher quiescence, ownership transfer, and a completed/idle/archived source
readback. This prevents a lower-level recovery path from accepting the former
`accepted=true` flag alone and then registering a successor or transferring the
owner. Existing SQLite tables, CLI projections, and migration receipt shapes are
unchanged.

Focused Store, clean migration, takeover, CLI and delivery selections passed
`26 passed` and `67 passed`; the complete source plus acceptance selection passed
`456 passed, 1 skipped`. `compileall` and `git diff --check` passed.

This is deterministic handover safety evidence only. Real native SDK migration,
OS/Windows old-writer termination, provider crash windows, business continuation
and project-level L3-L5 remain `not_verified`; #297, #298 and #292 remain open.
