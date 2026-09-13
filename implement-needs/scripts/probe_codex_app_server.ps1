param(
  [string]$CodexCommand = "codex",
  [int]$TimeoutSeconds = 8,
  [string]$ReceiptPath
)

$ErrorActionPreference = "Stop"
$result = [ordered]@{
  transport = "codex-app-server-jsonrpc"
  available = $false
  initialize = "not_run"
  thread_start = "not_run"
  thread_read = "not_run"
  model_readback = "not_run"
  reasoning_readback = "not_run"
  turn_lifecycle = "not_run"
  decision = "reject"
  action = "inspect the version-specific JSON-RPC protocol and repair the backend"
}
try {
  $command = Get-Command $CodexCommand -ErrorAction Stop
  $help = & $command.Source app-server --help 2>&1 | Out-String
  if ($LASTEXITCODE -ne 0) { throw "app-server --help failed" }
  $result.available = $true
  $result.initialize = "failed_no_jsonrpc_response"
  $result.action = "send initialize with the protocol client name/version, then probe create/read/model/effort/turn"
} catch {
  $result.action = $_.Exception.Message
}
$json = $result | ConvertTo-Json -Depth 5
if ($ReceiptPath) { $json | Set-Content -LiteralPath $ReceiptPath -Encoding utf8 }
$json
if ($result.decision -ne "allow") { exit 1 }
exit 0
