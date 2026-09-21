# Implement Needs qualification

This directory is the pre-use qualification suite for `implement-needs`. The
controller remains the runtime source of truth. The qualification harness only
collects public receipts, runs the fixture checks, and makes an independent
fail-closed decision.

The current certified topology is `whole-spec` with the thread backend. A
qualification run must dynamically resolve the saved project and obtain a
non-empty `project_id` from formal readback before it performs any GitHub
mutation. `request_id`, title tokens, and `codex://threads/...` URLs are
correlation or recovery indexes; they are not task identity.

Qualification also runs the normalized takeover matrix for every supported entry
frontier: requirement, planning, ticketing, implementation, verification,
merge cleanup, final verification, release, synchronization, terminal, and an
existing managed run. The matrix is side-effect free and must report zero created
resources. Live GitHub/task evidence remains separately required for the whole-SPEC
delivery and cleanup contract.

## Commands

Before a normal run, check the immutable subject/harness key:

```text
python implement-needs/validation/scripts/qualification_gate.py \
  --scenario whole-spec-v1 --backend thread
```

```text
python implement-needs/validation/scripts/run_qualification.py check \
  --scenario whole-spec-v1 --backend thread \
  --project-identity <project-readback.json> \
  --backend-receipt <capability-receipt.json>

python implement-needs/validation/scripts/run_qualification.py qualify \
  --scenario whole-spec-v1 --backend thread \
  --project-identity <project-readback.json> \
  --backend-receipt <capability-receipt.json> \
  --evidence-report <public-readbacks.json>

python implement-needs/validation/scripts/verify_qualification.py \
  --scenario implement-needs/validation/scenarios/whole-spec-v1.json \
  --report <qualification.json>

python implement-needs/validation/scripts/register_qualification.py \
  --index implement-needs/validation/reports/index.json \
  --scenario implement-needs/validation/scenarios/whole-spec-v1.json \
  --report <qualification.json>

python implement-needs/validation/scripts/cleanup_qualification.py \
  --manifest <run-manifest.json> --run-id <run-id>
```

`check` is read-only. `qualify` expects the controller run to have produced
public evidence for the real GitHub Issues, Parent relationships, merged PRs,
task backend readbacks, release-train layers, recovery matrix, and repository
sync. The verifier does not query SQLite to prove product behavior.

The permanent scenario contains one umbrella SPEC, three sequential child
SPECs, eight tickets, one Grill task, one planning task, three SPEC tasks, and
three merged PRs. Ticket-level implementation artifacts are forbidden.

Reports are retained only after redaction. Runtime clones, worktrees, task
logs, databases, and HTTP processes are temporary. Cleanup accepts only paths
explicitly recorded in the manifest and containing the exact run marker.
