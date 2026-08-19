[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$WorktreePath,
    [string]$Repository = "D:\WILL\STOCK\apex_proj\strategies\research_method",
    [string]$BaseBranch = "master",
    [string]$CommitMessage = "",
    [switch]$Cleanup
)

$ErrorActionPreference = "Stop"
$mutex = [System.Threading.Mutex]::new($false, "CodexAiQuantResearchDocsMerge")
$locked = $false

function Invoke-Git {
    param([string[]]$Arguments, [switch]$AllowFailure)
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & git @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "git $($Arguments -join ' ') failed:`n$($output -join [Environment]::NewLine)"
    }
    return [PSCustomObject]@{ ExitCode = $exitCode; Output = @($output) }
}

try {
    $repositoryPath = (Resolve-Path -LiteralPath $Repository).Path
    $taskWorktreePath = (Resolve-Path -LiteralPath $WorktreePath).Path
    $taskRepoRoot = (Invoke-Git -Arguments @("-C", $taskWorktreePath, "rev-parse", "--show-toplevel")).Output[0].Trim()
    if ([IO.Path]::GetFullPath($taskRepoRoot) -ne [IO.Path]::GetFullPath($taskWorktreePath)) {
        throw "WorktreePath must be the root of a Git worktree."
    }

    $branch = (Invoke-Git -Arguments @("-C", $taskWorktreePath, "branch", "--show-current")).Output[0].Trim()
    if (-not $branch.StartsWith("codex/iterate-quant-strategy/")) {
        throw "Refusing to merge unexpected branch: $branch"
    }

    $commitScript = Join-Path $PSScriptRoot "commit_docs_worktree.ps1"
    if (-not (Test-Path -LiteralPath $commitScript -PathType Leaf)) {
        throw "Automatic documentation commit helper is missing: $commitScript"
    }
    $autoCommitOutput = & $commitScript -WorktreePath $taskWorktreePath -CommitMessage $CommitMessage
    $autoCommit = $autoCommitOutput | ConvertFrom-Json

    $changedFiles = @(Invoke-Git -Arguments @("-C", $taskWorktreePath, "diff", "--name-only", "$BaseBranch...HEAD")).Output | Where-Object { $_ }
    $allowedFiles = @(
        "ai_quant_research_experience_library.md",
        "ai_quant_research_method.md",
        "ai_quant_research_protocol.md"
    )
    $unexpected = @($changedFiles | Where-Object { $_ -notin $allowedFiles })
    if ($unexpected.Count -gt 0) {
        throw "Docs branch contains files outside the allowed set: $($unexpected -join ', ')"
    }
    if ($changedFiles.Count -eq 0) {
        throw "Docs branch has no changes relative to $BaseBranch."
    }

    $locked = $mutex.WaitOne([TimeSpan]::FromMinutes(2))
    if (-not $locked) {
        throw "Timed out waiting for the research-method merge lock."
    }

    $canonicalBranch = (Invoke-Git -Arguments @("-C", $repositoryPath, "branch", "--show-current")).Output[0].Trim()
    if ($canonicalBranch -ne $BaseBranch) {
        throw "Canonical worktree must remain on $BaseBranch; found $canonicalBranch."
    }
    $canonicalStatus = @(Invoke-Git -Arguments @("-C", $repositoryPath, "status", "--porcelain")).Output
    if ($canonicalStatus.Count -gt 0) {
        throw "Canonical worktree is dirty. Refusing to merge over shared changes."
    }

    $merge = Invoke-Git -Arguments @("-C", $repositoryPath, "merge", "--no-ff", "--no-edit", $branch) -AllowFailure
    if ($merge.ExitCode -ne 0) {
        Invoke-Git -Arguments @("-C", $repositoryPath, "merge", "--abort") -AllowFailure | Out-Null
        Write-Error "Concurrent changes conflict with $branch. Canonical was restored. Rebase the task worktree onto latest $BaseBranch, resolve semantically, commit, and retry."
        exit 3
    }

    $mergeCommit = (Invoke-Git -Arguments @("-C", $repositoryPath, "rev-parse", "HEAD")).Output[0].Trim()
    if ($Cleanup) {
        Invoke-Git -Arguments @("-C", $repositoryPath, "worktree", "remove", $taskWorktreePath) | Out-Null
        Invoke-Git -Arguments @("-C", $repositoryPath, "branch", "-d", $branch) | Out-Null
    }

    [PSCustomObject]@{
        merged_branch = $branch
        base_branch = $BaseBranch
        auto_commit_created = [bool]$autoCommit.committed
        docs_commit = [string]$autoCommit.commit
        merge_commit = $mergeCommit
        cleaned_up = [bool]$Cleanup
    } | ConvertTo-Json
}
finally {
    if ($locked) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
