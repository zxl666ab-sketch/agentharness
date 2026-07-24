$ErrorActionPreference = "Continue"
Set-Location -LiteralPath "D:\个人通用agentharness"
Get-Content -LiteralPath ".\.env" | ForEach-Object {
  $line = $_.Trim()
  if (-not $line -or $line.StartsWith("#")) { return }
  $idx = $line.IndexOf("=")
  if ($idx -lt 1) { return }
  $key = $line.Substring(0, $idx).Trim()
  $val = $line.Substring($idx + 1).Trim()
  Set-Item -Path "Env:$key" -Value $val
}
Write-Host "ENV_OK model=$env:OPENAI_MODEL key_set=$([bool]$env:OPENAI_API_KEY) base=$env:OPENAI_BASE_URL"
Write-Host "Launching interactive CLI..."
uv run agentharness --provider openai --approval auto --cwd .
