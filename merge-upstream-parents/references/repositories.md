# Repositories, parents, builds, deployment targets

Reference for the [optional CPA profile](cpa-profile.md), not a standalone workflow. The CPA workspace is the directory holding the selected checkouts and `ops/`; take its path from the user and re-check paths and mappings there.

## Parents and remotes

| Repository | Parent remote (tracked ref) | Parent project | Own remote (push ref) | VERSION source part |
| --- | --- | --- | --- | --- |
| CPA-Core-ex | `origin` (`origin/main`) | `router-for-me/CLIProxyAPI` | `fork` (`fork/main`) | newest tag on `origin/main` |
| CPA-Manager-Plus-ex | `origin` (`origin/main`) | `seakee/CPA-Manager-Plus` | `fork` (`fork/main`) | newest tag on `origin/main` |
| CPA-OpenCode-ex | `origin` (`origin/main`) | `massiveits/opencode-go-cliproxyapi` | `fork` (`fork/main`) | newest tag on `origin/main` |
| CPA-Usage-Keeper-ex | `upstream` (`upstream/main`) | `Willxup/cpa-usage-keeper` | `origin` (`origin/main`) | newest tag on `upstream/main` |
| CPA-Qwen | — | self-owned | `origin` (`origin/main`) | own version only |

- This table records expected legacy remote names and version inputs. Verify parent and fork identity, branches and URLs via the canonical skill. `ops\update-versions.ps1` (`$definitions`: `Source`, `Generated`, `Kind`) defines the version script's inputs only.
- Core's `origin` is a partial clone (`blob:none`, fetch refspec covers only `main`). Missing blobs may be fetched on demand; verify the named fetch and subsequent reads succeed rather than assuming warnings or missing objects are harmless.
- CPA-Manager-Plus-ex also has `opencode-upstream` = `massiveits/opencode-go-cliproxyapi`. It is the source of the vendored `providers/opencode-go` subtree, not a parent of the panel.
- Out of scope unless the user names them: Manager `origin/v2`, `origin/dev`, `origin/update-channel`; Keeper `upstream/next`, `upstream/release/v1`.

## Vendored provider subtree (Manager only)

`CPA-Manager-Plus-ex\providers\opencode-go` is a `git subtree` copy of the OpenCode plugin. For an explicitly requested subtree sync, verify `opencode-upstream` against the recorded OpenCode project and confirm its source head (normally `refs/heads/main`). Fetch that exact head to a separate run ref as in the canonical skill. In the isolated Manager worktree, after finishing the parent merge and before final verification:

```powershell
git subtree merge --prefix=providers/opencode-go <pinned-opencode-sha> --squash
node bin/release/check-provider-import-boundary.mjs
```

`docs\FORK-MAINTENANCE.md` → 「provider 源码整合」 holds the preserved customizations and exclusions (`plugins/**`, binaries, logs). Re-apply those customizations and review all subtree changes against the pre-sync candidate, including cleanly merged paths. If you defer the sync, say why in the report.

## Builds and artifacts

| Repository | Build | Artifact the deploy consumes |
| --- | --- | --- |
| CPA-Core-ex | `go build -trimpath -o <ws>\CPA-Core-ex\local-deploy-output-<slug>-<date>\cli-proxy-api.exe .\cmd\server` | newest `CPA-Core-ex\local-deploy-output-*\cli-proxy-api.exe` |
| CPA-Manager-Plus-ex | `npm run test:web`, `npm run build`; then in `apps\manager-server`: `go build -o <ws>\local-deploy-artifacts\manager\cpa-manager-plus.exe .\cmd\cpa-manager-plus` | `local-deploy-artifacts\manager\cpa-manager-plus.exe` + `management.html` |
| CPA-Manager-Plus-ex (panel) | copy `apps\web\dist\index.html` to **both** `apps\manager-server\internal\httpapi\web\management.html` and `<ws>\local-deploy-artifacts\manager\management.html` | same |
| CPA-OpenCode-ex | `CGO_ENABLED=1 go build -trimpath -buildmode=c-shared -o <ws>\local-deploy-artifacts\opencode\cpa-credential-opencode.dll .` | `local-deploy-artifacts\opencode\cpa-credential-opencode.dll` |
| CPA-Usage-Keeper-ex | `npm --prefix web run build`; `CGO_ENABLED=1 go build -trimpath -ldflags '-s -w' -o <ws>\local-deploy-artifacts\keeper\cpa-usage-keeper.exe .\cmd\server\main.go`; restore `web\dist\.gitkeep` | `local-deploy-artifacts\keeper\cpa-usage-keeper.exe` |
| CPA-Qwen | see its README (c-shared plugin) | live plugin dir, only when its source changed |

`<ws>` is the CPA workspace. Build the OpenCode plugin **without** `-tags debug`; `debug` switches the plugin to dev behavior. Keeper's web build clears `web\dist`, so re-create the tracked `.gitkeep`.

## Verification commands

Run these from the repository root, targeted at what changed when a package-level scope is obvious (Core's full suite is large; `go test ./sdk/cliproxy/auth/` alone takes several minutes).

| Repository | Commands |
| --- | --- |
| CPA-Core-ex | `go build ./...`, `go vet ./...`, `go test ./internal/... ./sdk/...` (or the touched packages) |
| CPA-Manager-Plus-ex | `npm run test:web`, `npm run test:repo`, `npm run lint`, `npm run build`; in `apps\manager-server`: `go build ./...`, `go test ./...` |
| CPA-OpenCode-ex | `go build ./...`, `go test ./...` |
| CPA-Usage-Keeper-ex | `go build ./...`, `go test ./...`; `npm --prefix web run test`, `npm --prefix web run typecheck`, `npm --prefix web run lint` when the web UI changed |
| CPA-Qwen | `go build ./...`, `go test ./...` |

## Local deployment (Windows)

For an authorized deploy to 本机 only, run from the verified workspace root after checking artifact selection:

```powershell
& .\ops\invoke-local-cpa-core-deploy-task.ps1 -TimeoutSeconds 600
```

- It triggers the scheduled task `CPA-Local-Core-Deploy` (runs elevated, non-interactive) and waits for `ops\local-cpa-core-deploy-task-result.json`.
- Core source is always the newest `CPA-Core-ex\local-deploy-output-*\cli-proxy-api.exe`; if that hash equals the live binary, Core is skipped.
- When any of Keeper, Manager, panel or OpenCode plugin differs from live, it switches to `ops\deploy-local-cpa-stack.ps1`: stops Manager → Keeper → Core, replaces only changed artifacts, starts in reverse order, health-checks 8317 / 8318 / 18317, and restores every backup on failure.
- Receipts: `ops\local-cpa-core-deploy-task-result.json`, `ops\local-stack-redeploy\deploy-result.json`. `Hash: null` in the stack receipt means the artifact was identical and skipped. Backups: `ops\local-stack-redeploy\backup-<timestamp>\`.

Live paths and services:

| Thing | Path / name |
| --- | --- |
| Core binary | `runtime\CPA-Core\app\cli-proxy-api.exe` |
| Core plugin | `runtime\CPA-Core\plugins\windows\amd64\cpa-credential-opencode.dll` |
| Manager binary | `runtime\CPA-Manager-Plus\app\cpa-manager-plus.exe` |
| Manager panel (served) | `runtime\CPA-Manager-Plus\panel\management.html` — `panelPath` points here, not at the embedded copy |
| Keeper binary | `runtime\CPA-Usage-Keeper\cpa-usage-keeper.exe` |
| Services | `LocalCPA`, `CPAUsageKeeper`, `LocalCPAManagerPlus` |
| Ports | 8317 Core, 8318 Keeper, 18317 Manager |

The panel file matters: deploy the embedded copy **and** the `panel\management.html` artifact, or the UI silently keeps the old bundle.

## Post-deploy verification

1. Receipt `success: true`; live hash of each replaced artifact equals the artifact file.
2. The new version string is in the built artifact (`2.1.1+1.0.1`, `0.2.3+1.0.1`, …), and the running process path and start time match a fresh start.
3. `Get-Service LocalCPA, CPAUsageKeeper, LocalCPAManagerPlus` all `Running`; ports 8317 / 8318 / 18317 answer.
4. `runtime\CPA-Core\logs\main.log`: plugin unloaded/loaded around the restart and at least one `200` inference afterwards.
5. `runtime\CPA-Usage-Keeper\data\logs\cpa-usage-keeper-<date>.log`: Redis `subscribe_connected` and continuing usage events; startup errors that reference a not-yet-listening 8317 are ordering noise when they self-heal.

## Other devices

Local is the only prepared target. `小电脑` is `yosef-server` (Ubuntu 24.04, `ssh yosef-server`, direct Ethernet, `/data` as the work root, systemd) — it runs MarketHub / SearXNG / Task Center, and **no CPA deployment exists there**. CPA is a local Windows stack.

To deploy elsewhere, first establish from the user or the target host: destination directory, service manager units, ports, and rollback. Then package a versioned folder containing the binaries, the panel file and a `manifest.json` (`repo`, `version`, `sha256`, `builtAt`, source commit) before transferring. If those facts are missing, stop and ask; never reuse the local elevated task or invent paths.
