# SF integration release train

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

The focused PR delivery and GitHub contract suites passed 22 tests. The source
and acceptance suite, excluding the two run fixture directories whose tests
share a module basename and excluding the environment-specific missing-SDK
assertion while the SDK is installed, passed 167 tests with one skip. The
default recursive collection remains invalid for those duplicate fixture module
names; the missing-SDK test was not treated as a product failure.

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
