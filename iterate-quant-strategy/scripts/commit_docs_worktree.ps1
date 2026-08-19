[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$WorktreePath,
    [string]$CommitMessage = ""
)

$ErrorActionPreference = "Stop"
$allowedFiles = @(
    "ai_quant_research_experience_library.md",
    "ai_quant_research_method.md",
    "ai_quant_research_protocol.md"
)

function Invoke-Git {
    param([string[]]$Arguments)
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & git @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        throw "git $($Arguments -join ' ') failed:`n$($output -join [Environment]::NewLine)"
    }
    return @($output)
}

$taskWorktreePath = (Resolve-Path -LiteralPath $WorktreePath).Path
$taskRepoRoot = (Invoke-Git -Arguments @("-C", $taskWorktreePath, "rev-parse", "--show-toplevel") | Select-Object -First 1).Trim()
if ([IO.Path]::GetFullPath($taskRepoRoot) -ne [IO.Path]::GetFullPath($taskWorktreePath)) {
    throw "WorktreePath must be the root of a Git worktree."
}

$branch = (Invoke-Git -Arguments @("-C", $taskWorktreePath, "branch", "--show-current") | Select-Object -First 1).Trim()
if (-not $branch.StartsWith("codex/iterate-quant-strategy/")) {
    throw "Refusing to commit unexpected branch: $branch"
}

foreach ($file in $allowedFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $taskWorktreePath $file) -PathType Leaf)) {
        throw "Required canonical document is missing: $file"
    }
}

$trackedChanges = @(Invoke-Git -Arguments @("-C", $taskWorktreePath, "diff", "--name-only", "HEAD", "--")) | Where-Object { $_ }
$untrackedChanges = @(Invoke-Git -Arguments @("-C", $taskWorktreePath, "ls-files", "--others", "--exclude-standard")) | Where-Object { $_ }
$dirtyFiles = @($trackedChanges + $untrackedChanges | Sort-Object -Unique)
$unexpected = @($dirtyFiles | Where-Object { $_ -notin $allowedFiles })
if ($unexpected.Count -gt 0) {
    throw "Worktree contains changes outside the allowed documentation set: $($unexpected -join ', ')"
}

if ($dirtyFiles.Count -eq 0) {
    $head = (Invoke-Git -Arguments @("-C", $taskWorktreePath, "rev-parse", "HEAD") | Select-Object -First 1).Trim()
    [PSCustomObject]@{
        committed = $false
        branch = $branch
        commit = $head
        files = @()
    } | ConvertTo-Json
    exit 0
}

$addArguments = @("-C", $taskWorktreePath, "add", "--") + $allowedFiles
Invoke-Git -Arguments $addArguments | Out-Null
$stagedFiles = @(Invoke-Git -Arguments @("-C", $taskWorktreePath, "diff", "--cached", "--name-only", "--")) | Where-Object { $_ }
$unexpectedStaged = @($stagedFiles | Where-Object { $_ -notin $allowedFiles })
if ($unexpectedStaged.Count -gt 0) {
    throw "Refusing to commit unexpected staged files: $($unexpectedStaged -join ', ')"
}
if ($stagedFiles.Count -eq 0) {
    throw "Worktree reported changes, but no allowed documentation changes were staged."
}

$sharedDiffValidator = Join-Path $PSScriptRoot "validate_shared_docs_diff.ps1"
if (-not (Test-Path -LiteralPath $sharedDiffValidator -PathType Leaf)) {
    throw "Shared documentation diff validator is missing: $sharedDiffValidator"
}
& $sharedDiffValidator -WorktreePath $taskWorktreePath | Out-Null

if ([string]::IsNullOrWhiteSpace($CommitMessage)) {
    $CommitMessage = "docs: record quant research feedback $(Get-Date -Format 'yyyyMMdd-HHmmss')"
}

Invoke-Git -Arguments @("-C", $taskWorktreePath, "commit", "-m", $CommitMessage) | Out-Null
$commit = (Invoke-Git -Arguments @("-C", $taskWorktreePath, "rev-parse", "HEAD") | Select-Object -First 1).Trim()

[PSCustomObject]@{
    committed = $true
    branch = $branch
    commit = $commit
    files = @($stagedFiles)
} | ConvertTo-Json
