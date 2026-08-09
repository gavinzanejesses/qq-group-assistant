$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$config = Join-Path $projectRoot "config.yml"
$envFile = Join-Path $projectRoot ".env"
$runtimeRoot = Join-Path $projectRoot ".runtime"

function Get-EnvValue([string]$Name) {
    $line = Get-Content -LiteralPath $envFile |
        Where-Object { $_ -match ("^" + [Regex]::Escape($Name) + "=") } |
        Select-Object -Last 1
    if (-not $line) { return "" }
    return $line.Substring($Name.Length + 1).Trim().Trim('"')
}

function Test-TcpPort([int]$Port) {
    try {
        $client = [Net.Sockets.TcpClient]::new()
        $connect = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $ok = $connect.AsyncWaitHandle.WaitOne(700)
        if ($ok) { $client.EndConnect($connect) }
        $client.Dispose()
        return $ok
    } catch {
        return $false
    }
}

function Test-BotOnline([int]$Port, [string]$Token) {
    try {
        $headers = @{ "X-Admin-Token" = $Token }
        $status = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/qq-admin/api/status" -Headers $headers -TimeoutSec 2
        return [bool]$status.online
    } catch {
        return $false
    }
}

function Find-NapCatLauncher {
    $configured = Get-EnvValue "NAPCAT_START_PATH"
    $runtimePathFile = Join-Path $runtimeRoot "napcat-path.txt"
    $saved = if (Test-Path -LiteralPath $runtimePathFile) {
        (Get-Content -LiteralPath $runtimePathFile -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    } else { "" }
    $candidates = @(
        $configured,
        $saved,
        (Join-Path $projectRoot "NapCat\launcher-user.bat"),
        (Join-Path $projectRoot "NapCat\launcher.bat"),
        (Join-Path $projectRoot "napcat\launcher-user.bat"),
        (Join-Path $env:LOCALAPPDATA "NapCat\launcher-user.bat")
    ) | Where-Object { $_ }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return ""
}

function Get-NapCatAccount([string]$Launcher) {
    $configured = Get-EnvValue "NAPCAT_QQ"
    if ($configured -match '^\d{5,12}$') { return $configured }
    $configDirectory = Join-Path (Split-Path $Launcher -Parent) "config"
    if (Test-Path -LiteralPath $configDirectory) {
        $accounts = @(Get-ChildItem -LiteralPath $configDirectory -Filter "onebot11_*.json" -File |
            ForEach-Object { if ($_.BaseName -match '^onebot11_(\d{5,12})$') { $matches[1] } } |
            Select-Object -Unique)
        if ($accounts.Count -eq 1) { return $accounts[0] }
    }
    return ""
}

function Start-NapCat([string]$Launcher, [string]$Account) {
    $workingDirectory = Split-Path $Launcher -Parent
    $extension = [IO.Path]::GetExtension($Launcher).ToLowerInvariant()
    Write-Host "Starting NapCat: $Launcher" -ForegroundColor Cyan
    if ($Account) { Write-Host "Quick login account: $Account" -ForegroundColor Cyan }
    if ($extension -in ".bat", ".cmd") {
        $command = "call `"$Launcher`""
        if ($Account) { $command += " $Account" }
        Start-Process -FilePath "$env:ComSpec" `
            -ArgumentList "/d", "/c", $command `
            -WorkingDirectory $workingDirectory `
            -WindowStyle Hidden | Out-Null
    } else {
        Start-Process -FilePath $Launcher -WorkingDirectory $workingDirectory | Out-Null
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "请先双击 安装.bat。"
}
if (-not (Test-Path -LiteralPath $config) -or -not (Test-Path -LiteralPath $envFile)) {
    throw "缺少 config.yml 或 .env，请先双击 安装.bat。"
}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$portText = Get-EnvValue "PORT"
$port = if ($portText -match '^\d+$') { [int]$portText } else { 8080 }
$webToken = Get-EnvValue "QQ_ASSISTANT_WEB_TOKEN"

& $python (Join-Path $projectRoot "qq_assistant_cli.py") validate --config $config
if ($LASTEXITCODE -ne 0) { throw "配置检查失败。" }

$pidFile = Join-Path $runtimeRoot "bot.pid"
$serviceReady = Test-TcpPort $port
if (-not $serviceReady) {
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

for ($attempt = 0; $attempt -lt 30 -and -not (Test-TcpPort $port); $attempt++) {
    Start-Sleep -Seconds 1
}
if (-not (Test-TcpPort $port)) {
    throw "管理服务未能启动，请查看 .runtime\bot.stderr.log。"
}

$online = Test-BotOnline $port $webToken
if (-not $online) {
    $napCatLauncher = Find-NapCatLauncher
    if (-not $napCatLauncher) {
        & (Join-Path $PSScriptRoot "open-dashboard.ps1")
        throw "机器人服务已启动，但没有找到 NapCat 启动程序。请重新运行 安装.bat 并选择 NapCat 的 launcher-user.bat。"
    }
    $napCatAccount = Get-NapCatAccount $napCatLauncher
    Start-NapCat $napCatLauncher $napCatAccount
    Write-Host "Waiting for QQ/NapCat to connect..." -ForegroundColor Yellow
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if (Test-BotOnline $port $webToken) { $online = $true; break }
        Start-Sleep -Seconds 1
    }
}

& (Join-Path $PSScriptRoot "open-dashboard.ps1")
if ($online) {
    Write-Host "机器人、NapCat 和网页管理端均已启动，QQ 当前在线。" -ForegroundColor Green
} else {
    Write-Warning "网页管理端已启动，但 QQ 尚未连接。请在弹出的 QQ 窗口中完成机器人账号登录，然后刷新页面。"
}
