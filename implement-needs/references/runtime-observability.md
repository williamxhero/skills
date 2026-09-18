# Runtime observability

`runtime_observations` is a typed projection of runtime measurements. The `events`
table remains the audit source, and every accepted observation writes one
`runtime_observation_recorded` event.

Use one stable `observation_key` for a logical observation. A network retry reuses
that key; a changed operation or new attempt uses a new key. Replaying an identical
observation is a no-op, while reusing a key with different data is rejected.

The `phase` vocabulary is open but the metrics projection recognizes
`controller_turn`, `worker_turn`, `external_wait`, and `retry`. Optional boolean
metadata fields `first_acceptance`, `recovery_success`, `retry`, and
`duplicate_external_operation` supply rates and safety counters. `role` identifies
controller and worker turns. Evidence references belong in metadata.

Durations are milliseconds. The aggregate `total_duration_ms` is the sum of
observed durations, not wall-clock elapsed time. Percentiles use the nearest-rank
method. Missing usage or cache data is `unknown`; it is never converted to zero.

The fake runtime backend provides deterministic `normal`, `external-wait`, `retry`,
`repair`, and `verification-failure` scenarios for public controller and SQLite
behavior tests. It has no external side effects.
