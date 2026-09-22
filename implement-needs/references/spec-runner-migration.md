# Spec Runner migration

The existing `/implement-needs` name is intentionally preserved as a thin
handoff entry. New runs use `spec-runner` and its separate SQLite control root.
Old `implement-needs` databases remain read-only audit sources until SR-08.1
explicitly imports a compatible observation. They are never opened as the new
writer database and are never deleted during migration.

## New run

```powershell
python -m pip install .\spec-runner
spec-runner launch --brief .\brief.md --config .\runner.json --control-root .\.spec-runner --launch-key <stable-key>
```

## Takeover

Prepare a `spec-runner-takeover-input/v1` JSON inventory containing the target
repository, source threads, artifacts, and historical facts. Run
`spec-runner takeover inspect`. A report marked `blocked` requires actual
ownership/stop evidence; it is not permission to create a competing writer.

The legacy controller, old control database, and old recovery scripts remain
available for their existing runs. This document does not claim those runs
were migrated or that old qualification receipts prove Runner qualification.
