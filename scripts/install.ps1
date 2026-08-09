param([switch]$NoPause)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path $PSScriptRoot -Parent
$venvRoot = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$configPath = Join-Path $projectRoot "config.yml"
$envPath = Join-Path $projectRoot ".env"

Write-Host "QQ Group Assistant - installation" -ForegroundColor Cyan

$pyLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
$pythonCommand = Get-Command "python.exe" -ErrorAction SilentlyContinue
if (-not $pyLauncher -and -not $pythonCommand) {
    throw "Python 3.11 or later is required. Download it from https://www.python.org/downloads/"
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "[1/4] Creating Python environment..."
    if ($pyLauncher) {
        & $pyLauncher.Source -3 -m venv $venvRoot
    } else {
        & $pythonCommand.Source -m venv $venvRoot
    }
    if ($LASTEXITCODE -ne 0) { throw "Failed to create Python environment." }
}

Write-Host "[2/4] Installing dependencies..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e $projectRoot
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

$utf8 = [Text.UTF8Encoding]::new($false)
if (-not (Test-Path -LiteralPath $configPath)) {
    Write-Host "[3/4] Creating group configuration..."
    $groupId = Read-Host "Primary QQ group number"
    while ($groupId -notmatch '^\d{5,12}$') {
        $groupId = Read-Host "Enter a valid QQ group number (5-12 digits)"
    }
    $adminId = Read-Host "Administrator QQ number"
    while ($adminId -notmatch '^\d{5,12}$') {
        $adminId = Read-Host "Enter a valid administrator QQ number (5-12 digits)"
    }
    $configText = [IO.File]::ReadAllText((Join-Path $projectRoot "config.example.yml"), $utf8)
    $configText = [Regex]::Replace($configText, '(?m)^group_id:\s*\d+\s*$', "group_id: $groupId", 1)
    $configText = [Regex]::Replace(
        $configText,
        '(?ms)(admin_commands:\s*\r?\n.*?authorized_users:\s*\r?\n\s*-\s*)\d+',
        "`${1}$adminId",
        1
    )
    [IO.File]::WriteAllText($configPath, $configText, $utf8)
}

if (-not (Test-Path -LiteralPath $envPath)) {
    $envText = [IO.File]::ReadAllText((Join-Path $projectRoot ".env.example"), $utf8)
    $randomBytes = [byte[]]::new(32)
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    $generator.GetBytes($randomBytes)
    $generator.Dispose()
    $webToken = [Convert]::ToBase64String($randomBytes).Replace('+', '-').Replace('/', '_').TrimEnd('=')
    $envText = $envText.Replace("replace-with-a-long-random-token", $webToken)
    $zhipuKey = Read-Host "Zhipu API key (optional, press Enter to skip)"
    if ($zhipuKey) {
        $envText = [Regex]::Replace($envText, '(?m)^ZHIPU_API_KEY=.*$', "ZHIPU_API_KEY=$zhipuKey")
    }
    [IO.File]::WriteAllText($envPath, $envText, $utf8)
}

if (-not $NoPause) {
    $napCatLine = Get-Content -LiteralPath $envPath |
        Where-Object { $_ -match '^NAPCAT_START_PATH=' } |
        Select-Object -Last 1
    $currentNapCatPath = if ($napCatLine) {
        $napCatLine.Substring("NAPCAT_START_PATH=".Length).Trim().Trim('"')
    } else { "" }
    if (-not $currentNapCatPath -or -not (Test-Path -LiteralPath $currentNapCatPath -PathType Leaf)) {
        Write-Host "为了让一键启动同时打开 QQ/NapCat，请选择 NapCat 启动脚本。" -ForegroundColor Yellow
        $napCatPath = (Read-Host "NapCat launcher-user.bat 完整路径（尚未安装可按 Enter 跳过）").Trim().Trim('"')
        while ($napCatPath -and -not (Test-Path -LiteralPath $napCatPath -PathType Leaf)) {
            $napCatPath = (Read-Host "路径无效，请重新输入；按 Enter 暂时跳过").Trim().Trim('"')
        }
        if ($napCatPath) {
            $envText = [IO.File]::ReadAllText($envPath, $utf8)
            if ($envText -match '(?m)^NAPCAT_START_PATH=.*$') {
                $envText = [Regex]::Replace($envText, '(?m)^NAPCAT_START_PATH=.*$', "NAPCAT_START_PATH=$napCatPath")
            } else {
                $envText = $envText.TrimEnd() + [Environment]::NewLine + "NAPCAT_START_PATH=$napCatPath" + [Environment]::NewLine
            }
            [IO.File]::WriteAllText($envPath, $envText, $utf8)
        }
    }
}

Write-Host "[4/4] Validating configuration..."
& $venvPython (Join-Path $projectRoot "qq_assistant_cli.py") validate --config $configPath
if ($LASTEXITCODE -ne 0) { throw "Configuration validation failed." }

Write-Host "Installation completed." -ForegroundColor Green
Write-Host "NapCat must enable this Reverse WebSocket address:"
Write-Host "ws://127.0.0.1:8080/onebot/v11/ws" -ForegroundColor Yellow
Write-Host "NapCat releases: https://github.com/NapNeko/NapCatQQ/releases"
Write-Host "After NapCat is configured, double-click 启动机器人.bat; it will start both services and verify QQ online status."
if (-not $NoPause) {
    Read-Host "Press Enter to close"
}
