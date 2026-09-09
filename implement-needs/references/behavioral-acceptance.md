# Behavioral acceptance

Run `python -m unittest discover -s implement-needs/tests -p 'test_*.py'` after changing controller behavior. The black-box scenarios in `test_behavioral_acceptance.py` drive observable messages, waits, task operations, recovery, handoff verification, archival, release ownership, and terminal output through an isolated fixture; they do not assert prose wording.

The `audit failure trace` reproduces the defining failure from thread `01a08424-a284-72c0-a48d-3b964e177fb8`: release work begins under a SPEC child while that child remains active and the controller proceeds toward completion. Acceptance requires deterministic rejection with both ownership and active-child reasons.

For a substantial protocol change, also run an independent forward evaluation in a temporary workspace. Give the evaluator the skill and a realistic broad implementation request, but not the expected trace or suspected failure. Retain its emitted lifecycle trace and validator receipt as review evidence; generated evaluation artifacts stay outside the repository.
