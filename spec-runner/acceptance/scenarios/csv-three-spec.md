# Run-marked three SPEC acceptance brief

Build a small Python command in the assigned `spec-runner/acceptance/fixture-app/<run-marker>/` directory.

1. Read a task CSV, validate required columns (`id`, `title`, `status`) and
   write deterministic JSON rows. Reject malformed input with a useful error.
2. Add stable counts by status, sorted by status name, to the JSON result.
3. Add a CLI with input/output paths, a nonzero failure exit and a concise
   error report. Preserve the behavior of the first two steps.

The Runner must plan, implement, test, independently review and deliver each
dependent SPEC. The brief contains no implementation command or approval.
