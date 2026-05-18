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
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Copy-Item -Recurse "$DistSrc\*" $OutDir
Copy-Item ".env.example" $OutDir
New-Item -ItemType Directory -Force -Path "$OutDir\prompts" | Out-Null
Copy-Item "prompts\system.md" "$OutDir\prompts\"

$LauncherPath = "$OutDir\Launch Auctus Agent.bat"
$LauncherContent = @"
@echo off
cd /d "%~dp0"
if not exist data mkdir data
if not exist inputs mkdir inputs
if not exist outputs mkdir outputs
if not exist logs mkdir logs
if not exist .env copy .env.example .env >nul
start "" AuctusAgent.exe
"@
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
