# Repository instructions

## Scope and source management

This repository versions three To-Done skills plus two personally maintained skills, `what-now` and `commit-n-push`, which are not To-Done entries. CC Switch remains the only installation and distribution manager. Read `docs/source-management.md` before editing or importing skill sources. Do not rewrite CC Switch configuration, replace client symlinks, or deploy a draft merely because a repository file exists.

The initial two exports and third-entry draft are historical facts preserved in `docs/source-snapshot.json` and the originating SPEC snapshot; do not rewrite either. Current three-entry sources implement a reviewed candidate; user-managed CC Switch updates and bounded Pi discovery/load observations are indexed in `docs/acceptance.md`, not proof of full acceptance. Source changes are not installation authorization. Keep compatible sibling common resources and pinned specialist assets/licenses together as documented in source management; do not create copied templates, another scheduler or vocabulary authority. The GitHub source repository is Public; root licensing still requires the user's decision.

## Agent skills

### Issue tracker

Publish specs and implementation tickets only to `williamxhero/skills` on GitHub. See `docs/agents/issue-tracker.md`. Always pass `--repo williamxhero/skills` to avoid writing to another repository.

### Triage labels

Use the role names in `docs/agents/triage-labels.md`; specs produced by to-spec use `ready-for-agent`.

### Domain docs

Read any existing relevant glossary or ADR before changing terminology or decisions. Create a glossary or ADR lazily when there is a real term or consequential decision to record; do not create a duplicate vocabulary store.

## Change and verification boundaries

Read the originating SPEC and its completion method before implementation. Do not use issue publication as authorization to execute the SPEC. Keep draft, static validation, actual Pi execution, independent review, CC Switch import and remote synchronization distinct in reports. Never claim runtime tests passed from Markdown or link checks alone. For acceptance or handoff, read and update `docs/acceptance.md` with version-bound evidence and unresolved requirements; it is an evidence index, not a second task tracker. When recording recovery, co-locate the original failure and later version-bound outcome in that index; keep the original blocked architecture fixture distinct from the separately authorized prospective fixture's same-fixture recovery. Keep raw model logs outside the public repository and redact before publication.
