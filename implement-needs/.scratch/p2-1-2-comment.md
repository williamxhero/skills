## Delivery

- Commit: `05cf3f0`
- Dispatch now consumes the canonical action contract and includes the contract in its output.
- Added derived CLI contract output and generated phase checklist; generated output identifies its source.
- Unknown dispatch/CLI actions fail closed with structured `action_contract_unknown`.
- Registered all action kinds currently emitted by the scheduler, including recovery and checkpoint actions.

## Verification

- `python -m unittest discover -s tests -p 'test_p2_1*.py'` — 3 passed
- `python -m unittest discover -s tests -p 'test_dispatch.py'` — 1 passed
