[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,

    [string]$OutputStem
)

$ErrorActionPreference = "Stop"
$ProjectRoot = "D:\WILL\ASR.TTS\voice_clone_tts"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ModelConfig = Join-Path $ProjectRoot "config\model.json"
$StylesConfig = Join-Path $ProjectRoot "config\styles.json"
$ExpectedGpt = "checkpoints/gpt/will_v2proplus_r2-e5.ckpt"
$ExpectedSovits = "checkpoints/sovits/will_v2proplus_r2_e4_s408.pth"
$ExpectedStyle = "identity"
$ApiHealthUrl = "http://127.0.0.1:9880/docs"

if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
    throw "Personal voice project is missing: $ProjectRoot"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Personal voice Python environment is missing: $Python"
}
if (-not (Test-Path -LiteralPath $InputPath -PathType Leaf)) {
    throw "Input file is missing: $InputPath"
}

$model = Get-Content -LiteralPath $ModelConfig -Raw -Encoding UTF8 | ConvertFrom-Json
if ($model.gpt_weights -ne $ExpectedGpt -or $model.sovits_weights -ne $ExpectedSovits) {
    throw "Personal voice model selection differs from r2_A_early. Check config/model.json before synthesis."
}
$styles = Get-Content -LiteralPath $StylesConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$identityStyle = $styles.$ExpectedStyle
if (-not $identityStyle -or -not $identityStyle.approved) {
    throw "Personal voice style 'identity' is missing or unapproved. Check config/styles.json before synthesis."
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot $identityStyle.reference_audio) -PathType Leaf)) {
    throw "Personal voice reference is missing: $($identityStyle.reference_audio)"
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot $model.gpt_weights) -PathType Leaf)) {
    throw "GPT checkpoint is missing: $($model.gpt_weights)"
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot $model.sovits_weights) -PathType Leaf)) {
    throw "SoVITS checkpoint is missing: $($model.sovits_weights)"
}

function Test-PersonalVoiceApi {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $ApiHealthUrl -TimeoutSec 2
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

function Assert-PersonalVoiceApiIdentity {
    $connection = Get-NetTCPConnection `
        -State Listen `
        -LocalPort 9880 `
        -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -in @("127.0.0.1", "::1") } |
        Select-Object -First 1
    if (-not $connection) {
        throw "Personal voice API is healthy but its listener process cannot be identified."
    }

    $listener = Get-Process -Id $connection.OwningProcess -ErrorAction Stop
    $modelUpdated = (Get-Item -LiteralPath $ModelConfig).LastWriteTimeUtc
    $stylesUpdated = (Get-Item -LiteralPath $StylesConfig).LastWriteTimeUtc
    $selectionUpdated = if ($modelUpdated -gt $stylesUpdated) { $modelUpdated } else { $stylesUpdated }
    if ($listener.StartTime.ToUniversalTime() -lt $selectionUpdated) {
        throw "The running API predates the r2_A_early/identity selection. Stop it and run this command again so the approved voice is loaded."
    }
}

if (Test-PersonalVoiceApi) {
    Assert-PersonalVoiceApiIdentity
}
else {
    $logRoot = Join-Path $ProjectRoot "qa"
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    $stdoutLog = Join-Path $logRoot "personal-voice-api.out.log"
    $stderrLog = Join-Path $logRoot "personal-voice-api.err.log"
    $apiProcess = Start-Process `
        -FilePath $Python `
        -ArgumentList @("-m", "voice_clone_tts.engine") `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -WindowStyle Hidden `
        -PassThru

    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($apiProcess.HasExited) {
            throw "Personal voice API exited during startup. Inspect $stderrLog"
        }
        Start-Sleep -Seconds 2
        if (Test-PersonalVoiceApi) {
            $ready = $true
            break
        }
    }
    if (-not $ready) {
        throw "Personal voice API did not become ready within 60 seconds. Inspect $stderrLog"
    }
    Assert-PersonalVoiceApiIdentity
}

$resolvedInput = (Resolve-Path -LiteralPath $InputPath).Path
$batchArgs = @(
    "-m", "voice_clone_tts.batch",
    "--input", $resolvedInput,
    "--style", $ExpectedStyle
)

if ($OutputStem) {
    if ([IO.Path]::IsPathRooted($OutputStem)) {
        $resolvedOutput = [IO.Path]::GetFullPath($OutputStem)
    }
    else {
        $resolvedOutput = [IO.Path]::GetFullPath((Join-Path (Get-Location).Path $OutputStem))
    }
    $outputParent = Split-Path -Parent $resolvedOutput
    if ($outputParent) {
        New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
    }
    $batchArgs += @("--output", $resolvedOutput)
}

Push-Location $ProjectRoot
try {
    & $Python @batchArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Personal voice batch synthesis failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
