# CPA conflict playbook and local-customization protection

Reference for the [optional CPA profile](cpa-profile.md). Apply in the isolated candidate.

## Identity: never handed to the parent

- Module paths, binary names, plugin IDs and product names stay the fork's (`cpa-credential-opencode`, `cpa-credential-qwen`, `cpa-manager-plus`, `cpa-usage-keeper`).
- Preserve CPA's `VERSION` and generated Go constant. For authorized regeneration, follow the [CPA version procedure](cpa-profile.md#version-only-when-authorized-and-applicable).
- Fork-only gates and machinery stay when the parent has no equivalent: Core's provider scheduling, request budget/admission, quota termination, resource catalog, stream replay; Manager's items in `docs\FORK-MAINTENANCE.md`; OpenCode's `api` routing hints, extra family prefixes, display-name fallback, plugin identity; Keeper's explicit pricing-catalog contract.

## Prefer the parent when it now covers a fork feature

This is the user's standing rule: a capability the fork invented that the parent now ships is replaced by the parent's implementation.

1. Confirm the parent's implementation is equivalent for the fork's use (same inputs, same user-visible result).
2. Delete the fork's parallel code; keep the fork's tests and adapt fixtures/imports so they exercise the parent's path.
3. If the parent's version is missing one thing the fork relied on, keep that delta as a small explicit patch next to the parent's code — do not resurrect the whole implementation.
4. Record what was replaced in the merge commit message.
5. `CPA-Manager-Plus-ex\docs\FORK-MAINTENANCE.md` is authoritative for Manager: each row carries the keep rule and the replacement condition. Read it before resolving Manager conflicts.

## Compose when the changes are adjacent and independent

- Two additions in one function: keep both in a defensible order (validation → stop checks → admission → retry waits).
- Both sides rewriting the same block with the same intent: take the parent's unless the fork's is strictly richer; if the fork's is richer, keep it and fold in the parent's distinct checks (for example `isCredentialTerminatedError`, request-scoped stop, Retry-After annotation).
- Both sides adding a helper with the same purpose under different names: take the parent's, delete the fork's, keep the fork's call sites working.

## Parent APIs that were renamed or re-signed

Update the fork's call sites to the parent's new name and signature. Keep a thin wrapper only when the parent's new tests call the shorter arity while the fork's callers pass one more argument (for example `wrapStreamResult(..., cancels ...context.CancelFunc)`).

## Mechanical cleanup

Composing two `if` blocks usually leaves one orphan `}` where the two closing braces meet. After editing a conflicted file:

```powershell
Select-String -Path <file> -Pattern '^(<<<<<<< |=======|>>>>>>> )'
go build ./...
```

Also scan `.json`, `.md`, `.ts`, `.tsx` for markers, then `gofmt` the Go files you touched. A file that keeps a marker compiles into nonsense — search the whole tree, not just the files you remember.

`go vet ./...` may flag code that already exists in the parent (for example `internal/pluginhost/host_test.go` copying a `sync.Once`). Check the parent before treating it as a merge failure.

## Local customization protection

The forks maintain their own documentation and branding. The parent's README copy lives in `README-source.md`; `README.md` belongs to the fork.

Protected paths to audit after every merge (globs are relative to each repository):

- `README.md`, `README.zh.md`, `README_CN.md`, `README_JA.md`, `RELEASE_NOTES.md` (OpenCode keeps both sides' entries).
- Policy files that describe local customizations: `docs/FORK-MAINTENANCE.md` (Manager), `AGENTS.md` (OpenCode, Keeper).
- Branding: `apps/web/src/assets/brand/**`, `apps/web/public/favicon.ico`, `apple-touch-icon.png`, `apps/web/index.html` inline fallback, `apps/manager-server/internal/httpapi/web/**` (embedded panel copy), `resources/logo.svg`, `logo.svg`, `logo-white.svg`, provider icons, i18n product names, the Keeper web title and locale strings.
- Local config defaults worth a look: `config.example.yaml`, `.env.example`.

Audit procedure:

```powershell
$pre = '<pre-merge target sha>'
git -C <candidate> diff --name-status --cached $pre -- README.md README.zh.md README_CN.md README_JA.md RELEASE_NOTES.md apps/web/src/assets apps/web/public resources logo.svg logo-white.svg
```

Extend the example to all protected paths in that repo. Review each hit and retain or restore fork content in the candidate unless intentionally changing it for a recorded reason.

When refreshing `README-source.md`, use the pinned parent SHA, verify `git cat-file -t "${parentSha}:README.md"` is a blob, and copy its bytes without PowerShell text transcoding. For example, from the isolated worktree with the verified SHA in `$parentSha`:

```powershell
cmd /d /c "git show ${parentSha}:README.md > README-source.md"
```

Require success and compare the raw file hash (`git hash-object --no-filters README-source.md`) to the parent's blob ID. Older PowerShell redirection or `Set-Content` / `Out-File` can corrupt UTF-8 punctuation.

## Known hard case (as of 2026-11-22)

`CPA-Core-ex\sdk\cliproxy\auth\conductor_stream.go` holds two independent implementations: the fork's stream replay engine (hold-until-complete, recovery modes, buffered retry) against the pump the parent rewrote for issues 7720–7745. Keep one whole implementation and port only the other side's independent additions (`streamHeaderError`, `streamBootstrapError.attemptID`, the cancel-func signature). Whichever side loses its engine, expect its suite to fail until the port is done; that is a decision for the user, not a conflict to bulldoze.
