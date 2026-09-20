## Delivery

- Commit: `432abd7`
- Added phase/entity context projections with mandatory `run_id`, entity identity, and read business version.
- Active SPEC/Ticket projections include only direct dependencies, applicable decision summaries, and evidence pointers.
- Closed entities expose delivery summaries and stable `history://` pointers for explicit on-demand reads.
- Added read-only `context` and `context-history` CLI commands with structured fail-closed errors.

## Verification

- `python -m unittest discover -s tests -p 'test_p1_6_context.py'` — 4 passed
