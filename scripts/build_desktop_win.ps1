# Build Windows .exe for Auctus Agent Desktop
# Usage: .\scripts\build_desktop_win.ps1 [-Version "0.1.0"]
#
# This creates a standalone .exe that can be:
# - Double-clicked to launch
# - Installed via installer
# - Distributed via installer or zip
#
# Requirements:
# - Python 3.10+
# - PyInstaller: pip install pyinstaller
# - Windows 10+

param(
    [string]$Version = "",
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Get version
if ([string]::IsNullOrWhiteSpace($Version)) {
    $versionMatch = Select-String -Path "app/version.py" -Pattern 'APP_VERSION\s*=\s*["\'']([^"\'']+)["\'']' | Select-Object -First 1
    if ($versionMatch) {
        $Version = $versionMatch.Matches.Groups[1].Value
    } else {
        $Version = "0.1.0"
    }
}

# Output directory
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $Root "release"
}
if (!(Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
}
$OutputDir = Resolve-Path $OutputDir

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Building Auctus Agent Desktop v$Version for Windows" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# Check PyInstaller
$pyinstaller = Get-Command pyinstaller -ErrorAction SilentlyContinue
if (!$pyinstaller) {
    Write-Host "PyInstaller not found. Installing..." -ForegroundColor Yellow
    python -m pip install pyinstaller
}

# Temp directory
$TMP = Join-Path $env:TEMP "auctus_desktop_build_$(Get-Random)"
New-Item -ItemType Directory -Force -Path $TMP | Out-Null

function Cleanup {
    if (Test-Path $TMP) {
        Remove-Item -Recurse -Force $TMP -ErrorAction SilentlyContinue
    }
}
trap { Cleanup; throw }

# ── Step 1: Prepare spec file ─────────────────────────────────────────────────
Write-Host "[1/4] Creating PyInstaller spec..." -ForegroundColor Yellow

$SpecContent = @"
# -*- mode: python ; coding: utf-8 -*-
import sys
sys.path.insert(0, '$($Root -replace '\\', '/')')

block_cipher = None

a = Analysis(
    ['app/desktop.py'],
    pathex=['$($Root -replace '\\', '/')'],
    binaries=[],
    datas=[
        ('app', 'app'),
        ('prompts', 'prompts'),
        ('docs', 'docs'),
        ('scripts', 'scripts'),
        ('agent.py', '.'),
        ('requirements.txt', '.'),
        ('README.md', '.'),
        ('INSTALL.md', '.'),
        ('.env.example', '.'),
    ],
    hiddenimports=[
        'webview',
        'webview.platforms.edgechromium',
        'fastapi',
        'uvicorn',
        'pydantic',
        'sqlalchemy',
        'chromadb',
        'openai',
        'anthropic',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AuctusAgent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='$($Root -replace '\\', '/')/app/icon.ico' if os.path.exists('$($Root -replace '\\', '/')/app/icon.ico') else None,
)
"@

$SpecFile = Join-Path $TMP "AuctusAgent.spec"
Set-Content -Path $SpecFile -Value $SpecContent -Encoding UTF8

Write-Host "  ✓ Spec file created" -ForegroundColor Green

# ── Step 2: Build with PyInstaller ────────────────────────────────────────────
Write-Host ""
Write-Host "[2/4] Building executable with PyInstaller..." -ForegroundColor Yellow
Write-Host "  This may take several minutes..." -ForegroundColor Gray

& pyinstaller $SpecFile --distpath "$TMP/dist" --workpath "$TMP/build" --noconfirm

if ($LASTEXITCODE -ne 0) {
    Write-Host "  ✗ PyInstaller build failed" -ForegroundColor Red
    exit 1
}

Write-Host "  ✓ Build complete" -ForegroundColor Green

# ── Step 3: Create distribution package ───────────────────────────────────────
Write-Host ""
Write-Host "[3/4] Creating distribution package..." -ForegroundColor Yellow

$DistDir = Join-Path $TMP "dist" "AuctusAgent"
$FinalDir = Join-Path $TMP "Auctus Agent"

# Copy to final location with proper name
Copy-Item -Recurse $DistDir $FinalDir

# Create launcher batch file for convenience
$LauncherBat = @"
@echo off
start "" "%~dp0AuctusAgent.exe"
"@
Set-Content -Path (Join-Path $FinalDir "Launch Auctus Agent.bat") -Value $LauncherBat -Encoding ASCII

# Create README for users
$UserReadme = @"
Auctus Agent Desktop
====================

Getting Started:
1. Double-click "AuctusAgent.exe" or "Launch Auctus Agent.bat"
2. First launch will set up the environment (may take a few minutes)
3. The app will open in a native window

Requirements:
- Windows 10 or later
- Python 3.10 or later (will be installed automatically if not present)

Support:
- Visit: https://github.com/ddjiang327/auctus-agent
- Email: support@auctus.io
"@
Set-Content -Path (Join-Path $FinalDir "README.txt") -Value $UserReadme -Encoding UTF8

Write-Host "  ✓ Package prepared" -ForegroundColor Green

# ── Step 4: Create outputs ────────────────────────────────────────────────────
Write-Host ""
Write-Host "[4/4] Creating distribution files..." -ForegroundColor Yellow

# Create zip
$ZipOutput = Join-Path $OutputDir "Auctus-Agent-Desktop-win-v${Version}.zip"
if (Test-Path $ZipOutput) {
    Remove-Item $ZipOutput -Force
}
Compress-Archive -Path "$FinalDir\*" -DestinationPath $ZipOutput -Force

# Copy exe for standalone distribution
$ExeOutput = Join-Path $OutputDir "AuctusAgent.exe"
Copy-Item (Join-Path $FinalDir "AuctusAgent.exe") $ExeOutput -Force

Write-Host "  ✓ Distribution files created" -ForegroundColor Green

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  Build Complete!" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "  Output files:" -ForegroundColor White
Write-Host "    Zip: $ZipOutput" -ForegroundColor Gray
Write-Host "    Exe: $ExeOutput" -ForegroundColor Gray
Write-Host ""
Write-Host "  To test:" -ForegroundColor Yellow
Write-Host "    Double-click: $ExeOutput" -ForegroundColor White
Write-Host ""
Write-Host "  To distribute:" -ForegroundColor Yellow
Write-Host "    - Share the .zip file for portable distribution" -ForegroundColor White
Write-Host "    - Or use with Inno Setup to create an installer" -ForegroundColor White
Write-Host ""

# Cleanup
Cleanup
