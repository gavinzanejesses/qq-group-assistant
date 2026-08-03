param(
    [string]$Python = ".\.venv\Scripts\python.exe",
    [string]$Config = ".\config.yml"
)

$ErrorActionPreference = "Stop"
& $Python .\qq_assistant_cli.py doctor --config $Config
exit $LASTEXITCODE
