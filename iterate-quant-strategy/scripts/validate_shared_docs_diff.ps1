[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$WorktreePath
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
$diffArguments = @("-C", $taskWorktreePath, "diff", "--cached", "--unified=0", "--no-color", "--") + $allowedFiles
$diff = @(Invoke-Git -Arguments $diffArguments)
$addedLines = @($diff | Where-Object { $_ -match '^\+(?!\+\+)' } | ForEach-Object { $_.Substring(1) })

$forbiddenPatterns = @(
    @{ Pattern = '(?i)<!--[^>]*checkpoint[^>]*-->'; Reason = 'checkpoint HTML marker' },
    @{ Pattern = '(?i)\bG\d+\s*(?:-|\p{Pd})\s*G\d+\b'; Reason = 'generation range' },
    @{ Pattern = '(?i)\b(?:C|S|H|E|D|RT)\d{4,}(?:-v\d+)?\b'; Reason = 'project research identifier' },
    @{ Pattern = '(?i)\bBH\s*q\s*[=:]?\s*[-+]?\d'; Reason = 'project-specific BH q metric' },
    @{ Pattern = '(?i)\brank\s+IC\s*[=:]?\s*[-+]?\d'; Reason = 'project-specific rank IC metric' }
)

$violations = @()
foreach ($line in $addedLines) {
    foreach ($rule in $forbiddenPatterns) {
        if ($line -match $rule.Pattern) {
            $violations += "[$($rule.Reason)] $line"
        }
    }
}

if ($violations.Count -gt 0) {
    throw "Shared documentation diff contains project-level records. Move them to the strategy workspace:`n$($violations -join [Environment]::NewLine)"
}

[PSCustomObject]@{
    valid = $true
    checked_added_lines = $addedLines.Count
} | ConvertTo-Json
