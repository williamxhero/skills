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
