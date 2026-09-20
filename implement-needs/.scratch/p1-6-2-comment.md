## Delivery

- Commit: `48305a6`
- Added explicit context freshness validation and context-driven SPEC/Ticket state write APIs.
- Stale projections fail with `stale_state` before any business state or event write; callers must refresh the projection.
- Added CLI context write support while preserving existing evidence gates and authorization semantics.

## Verification

- `python -m unittest discover -s tests -p 'test_p1_6*.py'` — 6 passed
