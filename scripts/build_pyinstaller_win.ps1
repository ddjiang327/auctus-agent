# Build a standalone Auctus Agent for Windows using PyInstaller.
# The output needs NO Python installed on the user's machine.
#
# Usage: powershell -ExecutionPolicy Bypass -File scripts\build_pyinstaller_win.ps1
# Output: release\Auctus-Agent-win-standalone-v<VERSION>.zip
param()
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Version = & ".\.venv\Scripts\python.exe" -c `
  "import sys; sys.path.insert(0,'app'); from version import APP_VERSION; print(APP_VERSION)" `
  2>$null
if (-not $Version) { $Version = "0.1.0" }

Write-Host "Building Auctus Agent v$Version (standalone - no Python required)"

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
  Write-Host "ERROR: .venv not found. Run scripts\install_windows.ps1 first."
  exit 1
}

Write-Host "Installing PyInstaller..."
& ".\.venv\Scripts\python.exe" -m pip install pyinstaller --quiet --upgrade

Write-Host "Running PyInstaller (this takes a few minutes)..."
& ".\.venv\Scripts\pyinstaller.exe" auctus-agent.spec --clean --noconfirm

$DistSrc = "dist\AuctusAgent"
$OutName  = "Auctus-Agent-win-v$Version"
$OutDir   = "release\$OutName"

if (Test-Path $OutDir) { Remove-Item -Recurse -Force $OutDir }
New-Item -ItemType Directory -Force -Path "$OutDir\_app" | Out-Null

# App files go into _app\ (hidden from user, just like Mac's _app/)
Copy-Item -Recurse "$DistSrc\*" "$OutDir\_app\"
Copy-Item ".env.example" "$OutDir\_app\"
New-Item -ItemType Directory -Force -Path "$OutDir\_app\prompts" | Out-Null
Copy-Item "prompts\system.md" "$OutDir\_app\prompts\"

# Launcher — the only file users need to see
$LauncherPath = "$OutDir\Launch Auctus Agent.bat"
$LauncherContent = @'
@echo off
setlocal
cd /d "%~dp0_app"

echo ================================================
echo   Auctus Agent
echo ================================================
echo.

:: Check if already running from this folder
curl -fsS http://127.0.0.1:8000/healthz >nul 2>&1
if %errorlevel% == 0 (
    echo Auctus Agent is already running.
    start http://127.0.0.1:8000/
    echo You can close this window.
    timeout /t 3 >nul
    exit /b 0
)

:: Create required directories and default config
if not exist data   mkdir data
if not exist inputs  mkdir inputs
if not exist outputs mkdir outputs
if not exist logs    mkdir logs
if not exist .env    copy .env.example .env >nul

echo Starting Auctus Agent...
echo Logs: %CD%\logs\startup.log
echo.

:: Start server in background (no console window)
start /B "" AuctusAgent.exe

:: Wait for server (up to 60 seconds)
set /a tries=0
:wait_loop
if %tries% geq 60 (
    echo.
    echo ERROR: Server did not start within 60 seconds.
    echo Check logs\startup.log for details.
    pause
    exit /b 1
)
timeout /t 1 >nul
curl -fsS http://127.0.0.1:8000/healthz >nul 2>&1
if %errorlevel% == 0 goto ready
set /a tries=%tries%+1
<nul set /p ="."
goto wait_loop

:ready
echo.
echo Auctus Agent is ready at http://127.0.0.1:8000
echo Opening browser...
start http://127.0.0.1:8000/
echo.
echo You can close this window. Auctus Agent will keep running in the background.
timeout /t 4 >nul
exit /b 0
'@
[System.IO.File]::WriteAllText($LauncherPath, $LauncherContent, [System.Text.Encoding]::ASCII)

$ZipPath = "release\Auctus-Agent-win-standalone-v$Version.zip"
if (Test-Path $ZipPath) { Remove-Item $ZipPath }
Compress-Archive -Path $OutDir -DestinationPath $ZipPath

$SizeMB = [math]::Round((Get-Item $ZipPath).Length / 1MB)
Write-Host ""
Write-Host "Build complete!"
Write-Host "  Folder: $OutDir"
Write-Host "  Zip:    $ZipPath  ($SizeMB MB)"
Write-Host ""
Write-Host "Share the zip. Users unzip and double-click 'Launch Auctus Agent.bat'."
Write-Host "No Python required on their machine."
