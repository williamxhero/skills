# Startup and scope contract

Read this reference before creating or resuming a run. The startup contract is
the executable record of the environment in which later controller decisions may
be made; a missing or unverified contract is a startup blocker, not permission
to guess a replacement environment.

## Required startup sequence

1. Initialize or open the run database.
2. Resolve the canonical Skill dependencies and their explicitly registered aliases.
3. Record the runtime, Skill root, target repository and branch, tracker mode, host
   capabilities, permissions, and resolved dependency path/digest/version/adapter.
4. Run `startup-check` before proceeding with phase work.

The contract is canonicalized and stored with a SHA-256 digest. Repeating the
same contract is idempotent. A different contract cannot overwrite a verified
contract; restart and recovery must read the same digest.

## Public commands

From the project root, use the controller as follows. JSON arguments are shown
inline only for short examples; callers may generate them from their validated
configuration.

```text
python scripts/controller.py --db <db> init --run-id <run-id> --initiative <initiative> --requirement <requirement>
python scripts/controller.py --db <db> startup-contract --run-id <run-id> --contract '<json>' --available-dependencies '<json>' --expected-version <version>
python scripts/controller.py --db <db> startup-check --run-id <run-id>
python scripts/controller.py --db <db> decide --run-id <run-id> --subject <subject> --selected '<json>' --recommendation '<json>' --evidence '<json>' --rationale <text> --actor <actor> --scope '<json>' --source <source> --authorization '<json>' --expected-version <version>
```

`startup-check` is read-only and returns `startup_contract_missing` when the
run has not recorded a verified contract. Dependency failures are structured as
`blocked_missing_dependency`, `blocked_ambiguous_dependency`, or
`dependency_mismatch`; the controller does not select a lookalike dependency.

## Evidence boundaries

`decide` writes the auditable decision stream. `record-observation` writes only
telemetry and cannot accept receipt, delivery proof, dependency waiver, or other
business evidence. External receipts use the business evidence APIs and remain
separate from decisions and observations.
