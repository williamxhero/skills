# Inventory of the CPA child repositories against their parents.
#
# Prints one row per repository: branch, dirty-file count, VERSION, commits the
# parent has that the fork does not (behind), commits the fork has that the
# parent does not (ahead), and whether the fork remote already matches HEAD.
#
# Usage:
#   .\inventory.ps1              # read current refs
#   .\inventory.ps1 -Fetch       # fetch every remote first
[CmdletBinding()]
param(
    [string]$Root = 'D:\WILL\AGENT\CPA',
    [switch]$Fetch
)

$ErrorActionPreference = 'Stop'

# SourceRef is the tracked parent branch. PushRemote/PushBranch is where the
# fork publishes. CPA-Qwen has no parent; only its own remote matters.
$repositories = @(
    [ordered]@{ Name = 'CPA-Core-ex'; SourceRef = 'origin/main'; PushRemote = 'fork'; PushBranch = 'main' }
    [ordered]@{ Name = 'CPA-Manager-Plus-ex'; SourceRef = 'origin/main'; PushRemote = 'fork'; PushBranch = 'main' }
    [ordered]@{ Name = 'CPA-OpenCode-ex'; SourceRef = 'origin/main'; PushRemote = 'fork'; PushBranch = 'main' }
    [ordered]@{ Name = 'CPA-Usage-Keeper-ex'; SourceRef = 'upstream/main'; PushRemote = 'origin'; PushBranch = 'main' }
    [ordered]@{ Name = 'CPA-Qwen'; SourceRef = ''; PushRemote = 'origin'; PushBranch = 'main' }
)

function Invoke-Git {
    param([string]$Path, [string[]]$Arguments)
    $output = & git -C $Path @Arguments 2>$null
    $exit = $LASTEXITCODE
    return [pscustomobject]@{ Output = @($output); ExitCode = $exit }
}

function Get-Version([string]$Path) {
    $file = Join-Path $Path 'VERSION'
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { return '(no VERSION)' }
    return ([IO.File]::ReadAllText($file)).Trim()
}

$rows = foreach ($repository in $repositories) {
    $path = Join-Path $Root $repository.Name
    if (-not (Test-Path -LiteralPath (Join-Path $path '.git'))) {
        [pscustomobject]@{ Repository = $repository.Name; Branch = '(missing)'; Dirty = ''; Version = ''; Behind = ''; Ahead = ''; Pushed = '' }
        continue
    }

    if ($Fetch) {
        # Native stderr (git progress, a harmless `gc` warning on the Core
        # partial clone) must not abort the run under ErrorActionPreference=Stop.
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & git -C $path fetch --all --tags --prune 2>$null | Out-Null
        $fetchExit = $LASTEXITCODE
        $ErrorActionPreference = $previous
        if ($fetchExit -ne 0) {
            Write-Warning "fetch failed for $($repository.Name) (exit $fetchExit)"
        }
    }

    $branch = (Invoke-Git $path @('rev-parse', '--abbrev-ref', 'HEAD')).Output -join ''
    $status = (Invoke-Git $path @('status', '--porcelain')).Output
    $dirty = @($status | Where-Object { $_ -ne '' }).Count

    $behind = ''
    $ahead = ''
    if ($repository.SourceRef) {
        $counts = (Invoke-Git $path @('rev-list', '--left-right', '--count', "$($repository.SourceRef)...HEAD")).Output -join ''
        if ($counts -match '^(\d+)\s+(\d+)$') {
            $behind = [int]$Matches[1]
            $ahead = [int]$Matches[2]
        }
    }

    $localSha = (Invoke-Git $path @('rev-parse', 'HEAD')).Output -join ''
    $remoteSha = (Invoke-Git $path @('rev-parse', "$($repository.PushRemote)/$($repository.PushBranch)")).Output -join ''
    $pushed = if ($localSha -and $remoteSha -and $localSha -eq $remoteSha) { 'yes' } elseif ($remoteSha) { 'no' } else { '(no remote ref)' }

    [pscustomobject]@{
        Repository = $repository.Name
        Branch     = $branch
        Dirty      = $dirty
        Version    = Get-Version $path
        Behind     = $behind
        Ahead      = $ahead
        Pushed     = $pushed
        Source     = if ($repository.SourceRef) { $repository.SourceRef } else { '(self-owned)' }
        PushTo     = "$($repository.PushRemote)/$($repository.PushBranch)"
    }
}

$rows | Format-Table -AutoSize
