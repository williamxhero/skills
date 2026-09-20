## Delivery

- Commit: `8562ae3`
- Added structured fail-closed policy errors for missing identity, migration evidence, and policy/implementation drift.
- Added CLI end-to-end coverage for manifest validation rejection and restart reconstruction of policy and pending restore state.
- Restore remains `pending_reconciliation` until all unknown external Intents are reconciled with verified evidence.

## Verification

- `python -m unittest discover -s tests -p 'test_p1_5*.py'` — 6 passed
- `python -m unittest discover -s tests` — 273 passed
