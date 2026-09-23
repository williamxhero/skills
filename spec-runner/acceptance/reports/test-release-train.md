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
Native relation writer remains unimplemented; body links are not native proof.

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
