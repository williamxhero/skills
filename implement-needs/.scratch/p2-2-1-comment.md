## Delivery

- Commit: `bc8eab6`
- Added fixed `implement-needs-safety-v1` benchmark task set.
- Added separate correctness, behavior, efficiency, and measurement-coverage fields.
- Missing tokens, fees, provider readback, and duplicate evidence remain `unknown`/`null`; they are never coerced to zero or success.
- Added read-only `benchmark-manifest` and `safety-metrics` CLI commands.

## Verification

- `python -m unittest discover -s tests -p 'test_p2_2_metrics.py'` — 2 passed
