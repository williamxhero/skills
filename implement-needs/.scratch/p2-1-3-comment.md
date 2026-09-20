## Delivery

- Commit: `963e404`
- Added action-surface consistency validation and public `action-contract-check` CLI.
- Unregistered scheduler actions and contract drift fail closed before dispatch.
- Contract-derived CLI/help/checklist output is sourced from the canonical registry.

## Verification

- `python -m unittest discover -s tests -p 'test_p2_1*.py'` — 4 passed
- `python -m unittest discover -s tests` — 285 passed
