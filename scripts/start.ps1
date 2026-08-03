param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [string]$Config = ".\config.yml"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment not found: $Python"
}
& $Python .\qq_assistant_cli.py validate --config $Config
if ($LASTEXITCODE -ne 0) {
    throw "Configuration validation failed."
}
$env:QQ_ASSISTANT_CONFIG = (Resolve-Path -LiteralPath $Config).Path
& $Python .\bot.py
