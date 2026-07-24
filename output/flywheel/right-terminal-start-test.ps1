# Agent Harness - start + human-sim test (for Codex right terminal)
$ErrorActionPreference = "Continue"
Set-Location -LiteralPath "D:\个人通用agentharness"

Write-Host ""
Write-Host "======== 1) Load .env ========" -ForegroundColor Cyan
Get-Content -LiteralPath ".\.env" | ForEach-Object {
  $line = $_.Trim()
  if (-not $line -or $line.StartsWith("#")) { return }
  $idx = $line.IndexOf("=")
  if ($idx -lt 1) { return }
  $k = $line.Substring(0, $idx).Trim()
  $v = $line.Substring($idx + 1).Trim()
  Set-Item -Path "Env:$k" -Value $v
}
Write-Host ("OPENAI_MODEL=" + $env:OPENAI_MODEL)
Write-Host ("OPENAI_BASE_URL=" + $env:OPENAI_BASE_URL)
Write-Host ("OPENAI_API_KEY set=" + [bool]$env:OPENAI_API_KEY)

Write-Host ""
Write-Host "======== 2) Doctor ========" -ForegroundColor Cyan
uv run agentharness doctor

Write-Host ""
Write-Host "======== 3) Ensure Web inspector ========" -ForegroundColor Cyan
$webUp = $false
try {
  $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8741" -UseBasicParsing -TimeoutSec 2
  if ($resp.StatusCode -eq 200) { $webUp = $true }
} catch {}
if ($webUp) {
  Write-Host "Web already up: http://127.0.0.1:8741"
} else {
  Write-Host "Starting web on 127.0.0.1:8741 ..."
  Start-Process -FilePath "uv" -ArgumentList @("run","agentharness","web","--host","127.0.0.1","--port","8741") -WorkingDirectory "D:\个人通用agentharness" -WindowStyle Hidden
  Start-Sleep -Seconds 3
  try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8741" -UseBasicParsing -TimeoutSec 3
    Write-Host ("Web status=" + $resp.StatusCode + " http://127.0.0.1:8741")
  } catch {
    Write-Host ("Web start issue: " + $_.Exception.Message)
  }
}

Write-Host ""
Write-Host "======== 4) Human-sim Turn 1 ========" -ForegroundColor Cyan
$out1 = & uv run agentharness run "新用户验收：用一句话说明 Agent Harness 是做什么的，并回复标记 OK_TURN1" --provider openai --approval auto --cwd . 2>&1 | Out-String
Write-Host $out1
$session = $null
if ($out1 -match "session=([0-9a-fA-F]+)") { $session = $Matches[1] }
Write-Host ("captured session=" + $session)

Write-Host ""
Write-Host "======== 5) Human-sim Turn 2 (same session) ========" -ForegroundColor Cyan
if ($session) {
  $out2 = & uv run agentharness run "接着上一步：再给我 2 个适合手工验收的小场景标题即可，结尾写 OK_TURN2" --provider openai --approval auto --cwd . --session $session 2>&1 | Out-String
} else {
  $out2 = & uv run agentharness run "给我 2 个适合手工验收的小场景标题即可，结尾写 OK_TURN2" --provider openai --approval auto --cwd . 2>&1 | Out-String
}
Write-Host $out2

Write-Host ""
Write-Host "======== 6) Recent runs ========" -ForegroundColor Cyan
uv run agentharness runs --limit 6

Write-Host ""
Write-Host "======== DONE ========" -ForegroundColor Green
Write-Host "Inspector: http://127.0.0.1:8741"
