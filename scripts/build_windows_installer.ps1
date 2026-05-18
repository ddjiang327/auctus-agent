# Build Windows .exe installer for Auctus Agent.
# This script:
# 1. Downloads Python embeddable package
# 2. Sets up virtual environment with dependencies
# 3. Runs Inno Setup to create the installer
#
# Requirements:
# - PowerShell 5.1+
# - Inno Setup 6.0+ (https://jrsoftware.org/isinfo.php)
#
# Usage:
#   .\build_windows_installer.ps1 [-Version "0.1.0"]

param(
    [string]$Version = "",
    [string]$InnoSetupPath = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    # Optional: path to PFX certificate for code signing (avoids SmartScreen / SAC blocks)
    # Example: -CertPath ".\cert.pfx" -CertPassword "mypassword"
    [string]$CertPath = "",
    [string]$CertPassword = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Get version from app/version.py if not provided
if ([string]::IsNullOrWhiteSpace($Version)) {
    $versionMatch = Select-String -Path "app/version.py" -Pattern 'APP_VERSION\s*=\s*["\'']([^"\'']+)["\'']' | Select-Object -First 1
    if ($versionMatch) {
        $Version = $versionMatch.Matches.Groups[1].Value
    } else {
        $Version = "0.1.0"
    }
}

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Building Windows Installer for Auctus Agent v$Version" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# ── Temp directory ──────────────────────────────────────────────────────────
$TMP = Join-Path $env:TEMP "auctus_build_$(Get-Random)"
New-Item -ItemType Directory -Force -Path $TMP | Out-Null

function Cleanup {
    if (Test-Path $TMP) {
        Remove-Item -Recurse -Force $TMP -ErrorAction SilentlyContinue
    }
}
trap { Cleanup; throw }

# ── Step 1: Prepare staging directory ─────────────────────────────────────────
Write-Host "[1/5] Preparing staging directory..." -ForegroundColor Yellow

$STAGING = Join-Path $TMP "staging"
New-Item -ItemType Directory -Force -Path $STAGING | Out-Null

# Copy core files
$CORE_FILES = @("agent.py", "requirements.txt", "README.md", "INSTALL.md", ".env.example", ".gitignore", "app", "prompts", "docs", "scripts")
foreach ($item in $CORE_FILES) {
    if (Test-Path $item) {
        if ((Get-Item $item).PSIsContainer) {
            Copy-Item -Path $item -Destination (Join-Path $STAGING $item) -Recurse -Force
        } else {
            Copy-Item -Path $item -Destination $STAGING -Force
        }
    }
}

# Clean up
Get-ChildItem -Path $STAGING -Recurse -Include "__pycache__","*.pyc","*.pyo" -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path $STAGING -Recurse -Filter ".DS_Store" -Force | Remove-Item -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path $STAGING -Recurse -Filter ".venv" -Force -Directory | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "  ✓ Staging directory prepared" -ForegroundColor Green

# ── Step 2: Download and prepare Python Embeddable ─────────────────────────────
Write-Host ""
Write-Host "[2/5] Downloading Python Embeddable..." -ForegroundColor Yellow

$PYTHON_VERSION = "3.11.8"
$PYTHON_ZIP = "python-$PYTHON_VERSION-embed-amd64.zip"
$PYTHON_URL = "https://www.python.org/ftp/python/$PYTHON_VERSION/$PYTHON_ZIP"
$PYTHON_DIR = Join-Path $TMP "python_embed"
$PYTHON_DEST = Join-Path $STAGING "python"

if (!(Test-Path $PYTHON_DEST)) {
    New-Item -ItemType Directory -Force -Path $PYTHON_DIR | Out-Null
    New-Item -ItemType Directory -Force -Path $PYTHON_DEST | Out-Null

    Write-Host "  Downloading Python $PYTHON_VERSION embeddable..."
    try {
        Invoke-WebRequest -Uri $PYTHON_URL -OutFile (Join-Path $TMP $PYTHON_ZIP) -UseBasicParsing
        Expand-Archive -Path (Join-Path $TMP $PYTHON_ZIP) -DestinationPath $PYTHON_DIR -Force

        # Copy Python files to destination
        Copy-Item -Path "$PYTHON_DIR\*" -Destination $PYTHON_DEST -Force

        # Create python3.exe symlink
        Copy-Item -Path (Join-Path $PYTHON_DIR "python.exe") -Destination (Join-Path $PYTHON_DEST "python3.exe") -Force

        Write-Host "  ✓ Python embeddable downloaded" -ForegroundColor Green
    } catch {
        Write-Host "  ⚠ Failed to download Python embeddable: $_" -ForegroundColor Yellow
        Write-Host "  Falling back to system Python (if available)" -ForegroundColor Yellow
    }
} else {
    Write-Host "  ✓ Python already present" -ForegroundColor Green
}

# ── Step 3: Install dependencies ──────────────────────────────────────────────
Write-Host ""
Write-Host "[3/5] Installing Python dependencies..." -ForegroundColor Yellow

$VENV_DIR = Join-Path $STAGING ".venv"
if (!(Test-Path $VENV_DIR)) {
    Write-Host "  Creating virtual environment..."
    python -m venv $VENV_DIR
}

$PIP_EXE = Join-Path $VENV_DIR "Scripts\pip.exe"
$PYTHON_EXE = Join-Path $VENV_DIR "Scripts\python.exe"

# Upgrade pip
Write-Host "  Upgrading pip..."
& $PYTHON_EXE -m pip install --upgrade pip --quiet 2>$null

# Install dependencies
Write-Host "  Installing dependencies..."
$REQUIREMENTS_TXT = Join-Path $STAGING "requirements.txt"
if (Test-Path $REQUIREMENTS_TXT) {
    & $PYTHON_EXE -m pip install -r $REQUIREMENTS_TXT --quiet 2>$null
}

Write-Host "  ✓ Dependencies installed" -ForegroundColor Green

# ── Step 4: Create launcher executable ────────────────────────────────────────
Write-Host ""
Write-Host "[4/5] Creating launcher..." -ForegroundColor Yellow

# Create a simple launcher script that will be converted to exe
$LAUNCHER_SCRIPT = @"
@echo off
setlocal

REM Auctus Agent Launcher
set APP_DIR=%~dp0
cd /d "%APP_DIR%"

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python is not installed or not in PATH.
    echo Please install Python 3.10 or later.
    pause
    exit /b 1
)

REM Create data directories if needed
if not exist "data" mkdir data
if not exist "inputs" mkdir inputs
if not exist "outputs" mkdir outputs
if not exist "logs" mkdir logs

REM Start the server
echo Starting Auctus Agent...
.venv\Scripts\python.exe -m uvicorn app.server:app --host 127.0.0.1 --port 8000

pause
"@

$LAUNCHER_PATH = Join-Path $STAGING "Auctus Agent.bat"
Set-Content -Path $LAUNCHER_PATH -Value $LAUNCHER_SCRIPT -Encoding ASCII

# Create a more sophisticated launcher using PowerShell
$PS_LAUNCHER = @'
#!/usr/bin/env pwsh
# Auctus Agent Launcher (PowerShell)

$APP_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $APP_DIR

$VENV_PYTHON = Join-Path $APP_DIR ".venv\Scripts\python.exe"

if (!(Test-Path $VENV_PYTHON)) {
    Write-Host "Auctus Agent needs to be set up first..."
    Write-Host "Running installation..."

    if (Test-Path "scripts\install_windows.ps1") {
        & "scripts\install_windows.ps1"
    }

    if (!(Test-Path $VENV_PYTHON)) {
        Write-Host "Installation failed. Please check the error messages above."
        Read-Host "Press Enter to exit"
        exit 1
    }
}

# Create data directories
$null = New-Item -ItemType Directory -Force -Path "$APP_DIR\data" -ErrorAction SilentlyContinue
$null = New-Item -ItemType Directory -Force -Path "$APP_DIR\inputs" -ErrorAction SilentlyContinue
$null = New-Item -ItemType Directory -Force -Path "$APP_DIR\outputs" -ErrorAction SilentlyContinue
$null = New-Item -ItemType Directory -Force -Path "$APP_DIR\logs" -ErrorAction SilentlyContinue

Write-Host "Starting Auctus Agent on http://127.0.0.1:8000"
Write-Host ""

& $VENV_PYTHON -m uvicorn app.server:app --host 127.0.0.1 --port 8000
'@

$PS_LAUNCHER_PATH = Join-Path $STAGING "Auctus Agent.ps1"
# Save as UTF-8 with BOM for Windows compatibility
$PS_LAUNCHER | Out-File -FilePath $PS_LAUNCHER_PATH -Encoding UTF8

Write-Host "  ✓ Launchers created" -ForegroundColor Green

# ── Step 5: Build installer with Inno Setup ────────────────────────────────────
Write-Host ""
Write-Host "[5/5] Building installer with Inno Setup..." -ForegroundColor Yellow

$ISS_TEMPLATE = Join-Path $Root "scripts\build_windows_setup.iss"

if (!(Test-Path $ISS_TEMPLATE)) {
    Write-Host "  ✗ Inno Setup script not found: $ISS_TEMPLATE" -ForegroundColor Red
    Write-Host "  Creating default installer as zip instead..." -ForegroundColor Yellow

    # Create zip as fallback
    $ZIP_OUTPUT = Join-Path $Root "release\Auctus-Agent-windows-v${Version}.zip"
    if (!(Test-Path (Split-Path $ZIP_OUTPUT))) {
        New-Item -ItemType Directory -Force -Path (Split-Path $ZIP_OUTPUT) | Out-Null
    }

    Compress-Archive -Path "$STAGING\*" -DestinationPath $ZIP_OUTPUT -Force
    Write-Host "  ✓ Installer created: $ZIP_OUTPUT" -ForegroundColor Green
} else {
    # Update version in ISS file
    $ISS_CONTENT = Get-Content $ISS_TEMPLATE -Raw
    $ISS_CONTENT = $ISS_CONTENT -replace 'VERSION_PLACEHOLDER', $Version
    $ISS_CONTENT = $ISS_CONTENT -replace 'OutputDir=.*', "OutputDir=`nOutputBaseFilename=Auctus-Agent-windows-v${Version}-setup"

    $ISS_TEMP = Join-Path $TMP "build_windows_setup.iss"
    Set-Content -Path $ISS_TEMP -Value $ISS_CONTENT -Encoding UTF8

    # Check if Inno Setup is installed
    if (!(Test-Path $InnoSetupPath)) {
        Write-Host "  ⚠ Inno Setup not found at: $InnoSetupPath" -ForegroundColor Yellow
        Write-Host "  Please install Inno Setup from https://jrsoftware.org/isinfo.php" -ForegroundColor Yellow
        Write-Host "  Or provide path: -InnoSetupPath 'C:\path\to\ISCC.exe'" -ForegroundColor Yellow
        Write-Host "  Creating zip as fallback..." -ForegroundColor Yellow

        $ZIP_OUTPUT = Join-Path $Root "release\Auctus-Agent-windows-v${Version}.zip"
        if (!(Test-Path (Split-Path $ZIP_OUTPUT))) {
            New-Item -ItemType Directory -Force -Path (Split-Path $ZIP_OUTPUT) | Out-Null
        }
        Compress-Archive -Path "$STAGING\*" -DestinationPath $ZIP_OUTPUT -Force
        Write-Host "  ✓ Installer created: $ZIP_OUTPUT" -ForegroundColor Green
    } else {
        # Run Inno Setup
        $OUTPUT_DIR = Join-Path $Root "release"
        if (!(Test-Path $OUTPUT_DIR)) {
            New-Item -ItemType Directory -Force -Path $OUTPUT_DIR | Out-Null
        }

        Write-Host "  Running Inno Setup..."
        & $InnoSetupPath /O"$OUTPUT_DIR" /F"Auctus-Agent-windows-v${Version}-setup" $ISS_TEMP

        $EXE_OUTPUT = Join-Path $OUTPUT_DIR "Auctus-Agent-windows-v${Version}-setup.exe"
        if (Test-Path $EXE_OUTPUT) {
            Write-Host "  ✓ Installer created: $EXE_OUTPUT" -ForegroundColor Green
            Write-Host "    Size: $([math]::Round((Get-Item $EXE_OUTPUT).Length / 1MB, 2)) MB"

            # ── Optional: Code signing (prevents SmartScreen / Smart App Control blocks) ──
            if (![string]::IsNullOrWhiteSpace($CertPath) -and (Test-Path $CertPath)) {
                Write-Host ""
                Write-Host "  Signing installer with certificate: $CertPath" -ForegroundColor Yellow
                $signtool = "${env:ProgramFiles(x86)}\Windows Kits\10\bin\x64\signtool.exe"
                if (!(Test-Path $signtool)) {
                    $signtool = "signtool.exe"  # fall back to PATH
                }
                if (![string]::IsNullOrWhiteSpace($CertPassword)) {
                    & $signtool sign /fd SHA256 /p $CertPassword /f $CertPath /tr http://timestamp.digicert.com /td SHA256 $EXE_OUTPUT
                } else {
                    & $signtool sign /fd SHA256 /f $CertPath /tr http://timestamp.digicert.com /td SHA256 $EXE_OUTPUT
                }
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "  ✓ Installer signed" -ForegroundColor Green
                } else {
                    Write-Host "  ⚠ Code signing failed — installer will trigger SmartScreen warnings" -ForegroundColor Yellow
                }
            }
        }
    }
}

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  Build Complete!" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "  Output files are in: release/" -ForegroundColor White
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Yellow
Write-Host "    1. Test the installer on a clean Windows system" -ForegroundColor White
Write-Host "    2. Sign the installer (optional, for fewer warnings)" -ForegroundColor White
Write-Host "    3. Publish to GitHub Releases" -ForegroundColor White
Write-Host ""
