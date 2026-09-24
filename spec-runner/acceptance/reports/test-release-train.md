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
skipped in 23.41 seconds. This verifies transactional protocol and same-stage
resume path under the acceptance fake adapter, not yet the user's real
business-answer SDK turn required by #254. L0 diff check passed; exact installed
artifact proof is due after source changes are finalized.
