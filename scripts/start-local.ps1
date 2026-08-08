$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$config = Join-Path $projectRoot "config.yml"
$envFile = Join-Path $projectRoot ".env"
$runtimeRoot = Join-Path $projectRoot ".runtime"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Run 安装.bat first."
}
if (-not (Test-Path -LiteralPath $config) -or -not (Test-Path -LiteralPath $envFile)) {
    throw "config.yml or .env is missing. Run 安装.bat first."
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null

$napCatLine = Get-Content -LiteralPath $envFile |
    Where-Object { $_ -match '^NAPCAT_START_PATH=' } |
    Select-Object -Last 1
if ($napCatLine) {
    $napCatPath = $napCatLine.Substring("NAPCAT_START_PATH=".Length).Trim()
    if ($napCatPath -and (Test-Path -LiteralPath $napCatPath)) {
        $napCatName = [IO.Path]::GetFileNameWithoutExtension($napCatPath)
        if (-not (Get-Process -Name $napCatName -ErrorAction SilentlyContinue)) {
            Start-Process -FilePath $napCatPath -WindowStyle Hidden
        }
    }
}

& $python (Join-Path $projectRoot "qq_assistant_cli.py") validate --config $config
if ($LASTEXITCODE -ne 0) { throw "Configuration validation failed." }

$pidFile = Join-Path $runtimeRoot "bot.pid"
$running = $false
try {
    $existingClient = [Net.Sockets.TcpClient]::new()
    $existingClient.Connect("127.0.0.1", 8080)
    $existingClient.Dispose()
    $running = $true
} catch {
    $running = $false
}
if (Test-Path -LiteralPath $pidFile) {
    $savedPid = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($savedPid -and (Get-Process -Id $savedPid -ErrorAction SilentlyContinue)) {
        $running = $true
    }
}

if (-not $running) {
    $env:QQ_ASSISTANT_CONFIG = (Resolve-Path -LiteralPath $config).Path
    $bot = Start-Process `
        -FilePath $python `
        -ArgumentList "bot.py" `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $runtimeRoot "bot.stdout.log") `
        -RedirectStandardError (Join-Path $runtimeRoot "bot.stderr.log") `
        -PassThru
    [IO.File]::WriteAllText($pidFile, [string]$bot.Id)
}

$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $client = [Net.Sockets.TcpClient]::new()
        $client.Connect("127.0.0.1", 8080)
        $client.Dispose()
        $ready = $true
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}
if (-not $ready) {
    throw "The web console did not start. Check .runtime\bot.stderr.log."
}

& (Join-Path $PSScriptRoot "open-dashboard.ps1")
Write-Host "Robot service and web console are running." -ForegroundColor Green
