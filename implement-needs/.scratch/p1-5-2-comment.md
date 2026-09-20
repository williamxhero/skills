## Delivery

- Commit: `66cc75a`
- Added canonical backup manifest with schema version, database/file digests, policy identity, evidence reachability, and tamper detection.
- Added fail-closed manifest validation and restore records that start in `pending_reconciliation`.
- Added reconciliation gate: unresolved `outcome_unknown` Intents prevent restore readiness; local restore is explicitly not external rollback.
- Added CLI commands: `backup-manifest`, `validate-backup-manifest`, `begin-restore`, and `restore-reconciliation`.

## Verification

- `python -m unittest discover -s tests -p 'test_p1_5*.py'` — 5 passed
- `python -m unittest discover -s tests` — 272 passed
