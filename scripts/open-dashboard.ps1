$ErrorActionPreference = "Stop"
$envFile = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
if (-not (Test-Path -LiteralPath $envFile)) {
    throw ".env not found: $envFile"
}
$line = Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^QQ_ASSISTANT_WEB_TOKEN=' } | Select-Object -Last 1
if (-not $line) {
    throw "QQ_ASSISTANT_WEB_TOKEN is not configured."
}
$token = $line.Substring("QQ_ASSISTANT_WEB_TOKEN=".Length).Trim()
if ($token.Length -lt 16) {
    throw "QQ_ASSISTANT_WEB_TOKEN must contain at least 16 characters."
}
Set-Clipboard -Value $token
Start-Process "http://127.0.0.1:8080/qq-admin"
Write-Host "Dashboard opened. The admin token has been copied to the clipboard."
