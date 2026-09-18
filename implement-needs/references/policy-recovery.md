# Policy pinning and recovery validation

Pin each run to workflow, skill bundle, backend protocol, schema, rules, profile, and
profile digest before external work. `pin-policy` rejects incomplete or unvalidated
profiles. A shadow decision reads the pinned rules and next action but calls no
mutation adapter.

`backup` uses SQLite online backup and writes a SHA-256 manifest with the evidence URI.
Keep the manifest with the backup; restore acceptance requires both files and matching
integrity evidence. `FakeBackend` injects named failures for recovery tests, while
`ShadowBackend` raises on every attempted mutation.
