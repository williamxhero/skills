## Delivery

- Commit: `0521bc8`
- Added canonical read-only action-contract registry for every current dispatch action.
- Contracts declare phase, inputs, preconditions, authorization, evidence, allowed side effects, output, recovery, idempotency, and references.
- Unknown, duplicate-key, and incomplete registrations fail closed.

## Verification

- `python -m unittest discover -s tests -p 'test_p2_1_action_contracts.py'` — 2 passed
