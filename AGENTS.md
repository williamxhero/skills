# Repository instructions

## Scope and source management

This repository versions three To-Done skills. CC Switch remains the only installation and distribution manager. Read `docs/source-management.md` before editing or importing skill sources. Do not rewrite CC Switch configuration, replace client symlinks, or deploy a draft merely because a repository file exists.

The two imported skills are exact historical exports. The architecture-to-done skill is an uninstalled draft until the SPEC is implemented and verified. Treat planned behavior as planned, not as a passing implementation.

## Agent skills

### Issue tracker

Publish specs and implementation tickets only to `williamxhero/skills` on GitHub. See `docs/agents/issue-tracker.md`. Always pass `--repo williamxhero/skills` to avoid writing to another repository.

### Triage labels

Use the role names in `docs/agents/triage-labels.md`; specs produced by to-spec use `ready-for-agent`.

### Domain docs

Read any existing relevant glossary or ADR before changing terminology or decisions. Create a glossary or ADR lazily when there is a real term or consequential decision to record; do not create a duplicate vocabulary store.

## Change and verification boundaries

Read the originating SPEC and its completion method before implementation. Do not use issue publication as authorization to execute the SPEC. Keep draft, static validation, actual Pi execution, independent review, CC Switch import and remote synchronization distinct in reports. Never claim runtime tests passed from Markdown or link checks alone.
