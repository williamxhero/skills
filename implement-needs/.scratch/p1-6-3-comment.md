## Delivery

- Commit: `37acfa5`
- Added read-only context measurement telemetry for payload bytes, estimated/observed tokens, latency, refresh/rejection counts, and explicit coverage.
- Missing provider fees remain `unknown`; estimates are not presented as observed values.
- Added schema-persisted measurement records and CLI commands `measure-context` and `context-measurements`.
- Measurement writes do not advance business version and cannot satisfy completion gates.

## Verification

- `python -m unittest discover -s tests -p 'test_p1_6*.py'` — 8 passed
- `python -m unittest discover -s tests` — 281 passed
