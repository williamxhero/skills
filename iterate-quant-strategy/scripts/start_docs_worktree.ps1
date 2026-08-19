[CmdletBinding()]
param(
    [string]$Repository = "D:\WILL\STOCK\apex_proj\strategies\research_method",
    [string]$WorktreeRoot = "D:\WILL\STOCK\apex_proj\runtime\research-method-worktrees",
    [string]$BaseBranch = "master",
    [string]$ThreadId = ""
)

$ErrorActionPreference = "Stop"
$mutex = [System.Threading.Mutex]::new($false, "CodexAiQuantResearchDocsMerge")
$locked = $false

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
    return $output
}

try {
    $locked = $mutex.WaitOne([TimeSpan]::FromMinutes(2))
    if (-not $locked) {
        throw "Timed out waiting for the research-method worktree lock."
    }

    $repositoryPath = (Resolve-Path -LiteralPath $Repository).Path
    $repoRoot = (Invoke-Git -Arguments @("-C", $repositoryPath, "rev-parse", "--show-toplevel") | Select-Object -First 1).Trim()
    if ([IO.Path]::GetFullPath($repoRoot) -ne [IO.Path]::GetFullPath($repositoryPath)) {
        throw "Repository must be the research_method Git root: $repositoryPath"
    }

    $status = @(Invoke-Git -Arguments @("-C", $repositoryPath, "status", "--porcelain"))
    if ($status.Count -gt 0) {
        throw "Canonical worktree is dirty. Do not start from uncommitted shared changes."
    }

    Invoke-Git -Arguments @("-C", $repositoryPath, "show-ref", "--verify", "--quiet", "refs/heads/$BaseBranch") | Out-Null
    New-Item -ItemType Directory -Force -Path $WorktreeRoot | Out-Null

    $token = if ([string]::IsNullOrWhiteSpace($ThreadId)) {
        "thread-$PID-$(Get-Date -Format 'yyyyMMddHHmmss')-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
    } else {
        $ThreadId.ToLowerInvariant() -replace "[^a-z0-9-]", "-"
    }
    $token = $token.Trim("-")
    if ([string]::IsNullOrWhiteSpace($token)) {
        throw "ThreadId did not contain a usable branch token."
    }

    $branch = "codex/iterate-quant-strategy/$token"
    $worktreePath = Join-Path $WorktreeRoot $token
    if (Test-Path -LiteralPath $worktreePath) {
        throw "Worktree path already exists: $worktreePath"
    }

    Invoke-Git -Arguments @("-C", $repositoryPath, "worktree", "add", "-b", $branch, $worktreePath, $BaseBranch) | Out-Null

    [PSCustomObject]@{
        repository = $repositoryPath
        base_branch = $BaseBranch
        branch = $branch
        worktree_path = [IO.Path]::GetFullPath($worktreePath)
    } | ConvertTo-Json
}
finally {
    if ($locked) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
