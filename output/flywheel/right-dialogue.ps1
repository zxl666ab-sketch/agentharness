$ErrorActionPreference = "Continue"
Set-Location -LiteralPath "D:\个人通用agentharness"
Get-Content -LiteralPath ".\.env" | ForEach-Object {
  $line = $_.Trim(); if (-not $line -or $line.StartsWith("#")) { return }
  $i = $line.IndexOf("="); if ($i -lt 1) { return }
  Set-Item -Path ("Env:"+$line.Substring(0,$i).Trim()) -Value $line.Substring($i+1).Trim()
}
$env:TERM = "dumb"
$env:NO_COLOR = "1"

Write-Host ""
Write-Host "===== T1: 先对话 =====" -ForegroundColor Cyan
$o1 = & uv run agentharness run "你好，用两句话介绍你自己。不要调用工具。" --provider openai --approval auto --cwd . 2>&1 | Out-String
Write-Host $o1
$session = $null; if ($o1 -match "session=([0-9a-fA-F]+)") { $session = $Matches[1] }
Write-Host "SESSION=$session"

Write-Host ""
Write-Host "===== T2: 看反馈后继续问 =====" -ForegroundColor Cyan
$o2 = & uv run agentharness run "你现在能用哪些工具？只列名字。" --provider openai --approval auto --cwd . --session $session 2>&1 | Out-String
Write-Host $o2

Write-Host ""
Write-Host "===== T3: 让它实际用工具 =====" -ForegroundColor Cyan
$o3 = & uv run agentharness run "请用 read_file 读取 README.md 前几行，然后一句话告诉我项目名。" --provider openai --approval auto --cwd . --session $session 2>&1 | Out-String
Write-Host $o3

Write-Host ""
Write-Host "===== 最近 runs =====" -ForegroundColor Cyan
uv run agentharness runs --limit 5
Write-Host "===== DIALOGUE DONE =====" -ForegroundColor Green
Write-Host "然后去 http://127.0.0.1:8741 看这几轮的反馈"
