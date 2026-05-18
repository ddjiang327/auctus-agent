param(
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$ProjectDir = (Get-Location).Path

# Color helpers
function Write-Info { param([string]$Msg) Write-Host "  $Msg" }
function Write-Success { param([string]$Msg) Write-Host "✓ $Msg" -ForegroundColor Green }
function Write-Warn { param([string]$Msg) Write-Host "⚠ $Msg" -ForegroundColor Yellow }
function Write-Fail { param([string]$Msg) Write-Host "✗ $Msg" -ForegroundColor Red }

function Fail {
  param([string]$Step, [string]$Detail = "")
  Write-Host ""
  Write-Host "========================================================" -ForegroundColor Red
  Write-Host "  Installation failed: $Step" -ForegroundColor Red
  Write-Host "========================================================" -ForegroundColor Red
  Write-Host ""

  switch -Wildcard ($Step) {
    "*Python not found*" {
      Write-Host "  [Problem] Python is not installed or not found"
      Write-Host ""
      Write-Host "  [Fix]"
      Write-Host "  1. Download Python 3.10 or later:"
      Write-Host "     https://www.python.org/downloads/"
      Write-Host "  2. Run the installer and make sure to check:"
      Write-Host "     [x] Add Python to PATH"
      Write-Host "     [x] Install pip"
      Write-Host "  3. After installing, double-click Setup.bat again"
      Write-Host ""
      Write-Host "  [Verify] Run in Command Prompt: python --version"
    }
    "*Python version*" {
      Write-Host "  [Problem] Python version is too old"
      Write-Host ""
      Write-Host "  Python 3.10 or later is required."
      Write-Host "  Please download the latest version from:"
      Write-Host "     https://www.python.org/downloads/"
    }
    "*permission*" {
      Write-Host "  [Problem] Insufficient permissions to complete installation"
      Write-Host ""
      Write-Host "  [Fix]"
      Write-Host "  1. Right-click Setup.bat"
      Write-Host "  2. Select 'Run as administrator'"
      Write-Host ""
      Write-Host "  If the problem persists, your antivirus may be blocking the install."
    }
    "*venv*" {
      Write-Host "  [Problem] Cannot create virtual environment"
      Write-Host ""
      Write-Host "  Possible causes:"
      Write-Host "  1. A previous install was interrupted, leaving a broken .venv folder"
      Write-Host "  2. Insufficient permissions"
      Write-Host ""
      Write-Host "  [Fix]"
      Write-Host "  1. Close all command-line windows"
      Write-Host "  2. Delete the .venv folder (if it exists)"
      Write-Host "  3. Double-click Setup.bat again"
    }
    "*Dependency install failed*" {
      Write-Host "  [Problem] Dependency installation failed"
      Write-Host ""
      Write-Host "  Possible causes:"
      Write-Host "  1. Unstable network connection"
      Write-Host "  2. Insufficient disk space"
      Write-Host ""
      Write-Host "  [Fix]"
      Write-Host "  1. Check your network and retry"
      Write-Host "  2. Make sure you have at least 1GB of free disk space"
    }
    "*timeout*" {
      Write-Host "  [Problem] Installation timed out"
      Write-Host ""
      Write-Host "  Download is taking too long, possibly due to a slow network."
      Write-Host "  Please ensure a stable connection and retry."
    }
    "*path*" {
      Write-Host "  [Problem] Project path issue"
      Write-Host ""
      Write-Host "  Current path: $ProjectDir"
      Write-Host "  Please make sure:"
      Write-Host "  1. The project is not in a deeply nested folder"
      Write-Host "  2. The path does not contain special characters"
    }
    default {
      Write-Host "  [Problem] Unknown error during installation"
      if ($Detail) {
        Write-Host ""
        Write-Host "  Error details:"
        Write-Host "  $Detail"
      }
      Write-Host ""
      Write-Host "  [General fix]"
      Write-Host "  1. Close all command-line windows"
      Write-Host "  2. Delete the .venv folder (if it exists)"
      Write-Host "  3. Double-click Setup.bat again"
      Write-Host ""
      Write-Host "  If the problem persists, screenshot this error and contact support."
    }
  }

  Write-Host ""
  Write-Host "========================================================" -ForegroundColor Red
  Write-Host ""
  Write-Host "Press Enter to close..."
  Read-Host
  exit 1
}

function Check-DiskSpace {
  try {
    $drive = Split-Path -Qualifier $ProjectDir
    # Skip for UNC paths (\\server\share) — Get-PSDrive doesn't support them
    if ($drive -match '^\\\\') { return }
    $freeSpace = (Get-PSDrive -Name $drive.TrimEnd(':')).Free
    $minSpace = 500MB
    if ($freeSpace -lt $minSpace) {
      Write-Warn "Low disk space: $([math]::Round($freeSpace/1MB))MB remaining"
      Write-Info "At least 500MB of free space is recommended"
    }
  } catch { }
}

function Test-PythonCommand {
  param([string]$Cmd)
  try {
    $null = & $Cmd -c "import sys" 2>&1
    return $LASTEXITCODE -eq 0
  } catch {
    return $false
  }
}

function Test-PythonExe {
  param([string]$Path)
  try {
    if (!(Test-Path $Path)) { return $false }
    $null = & $Path -c "import sys; print(sys.executable)" 2>&1
    return $LASTEXITCODE -eq 0
  } catch {
    return $false
  }
}

function Resolve-PythonCommand {
  $candidates = @("python", "python3", "py")

  foreach ($cmd in $candidates) {
    if (Test-PythonCommand $cmd) {
      return $cmd
    }
  }

  $commonPaths = @(
    "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe",
    "$env:ProgramFiles\Python*\python.exe",
    "${env:ProgramFiles(x86)}\Python*\python.exe",
    "C:\Python*\python.exe"
  )

  foreach ($pattern in $commonPaths) {
    if ([string]::IsNullOrWhiteSpace($pattern)) { continue }
    foreach ($path in Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | Sort-Object FullName -Descending) {
      if (Test-PythonCommand $path.FullName) {
        return $path.FullName
      }
    }
  }

  return $null
}

# ── Step 0: Pre-flight checks ─────────────────────────────────────────────────
Write-Host ""
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Auctus Agent Installer" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Info "Checking system requirements..."

Check-DiskSpace

# ── Step 1: Python check ──────────────────────────────────────────────────────
Write-Host ""
Write-Info "Checking Python..."

$pythonCmd = Resolve-PythonCommand

if ($null -eq $pythonCmd) {
  Fail "Python not found" "python/python3 command not found on this system"
}

# Check Python version
try {
  $versionInfo = & $pythonCmd -c "import sys; print(sys.version_info.major, sys.version_info.minor)"
  $major, $minor = $versionInfo -split ' '
  if ([int]$major -lt 3 -or ([int]$major -eq 3 -and [int]$minor -lt 10)) {
    Fail "Python version too old" "Found $major.$minor, need 3.10 or later"
  }
  Write-Success "Python $major.$minor OK"
} catch {
  Fail "Python version check" $_
}

# ── Step 2: Directories ───────────────────────────────────────────────────────
Write-Host ""
Write-Info "Creating directories..."
New-Item -ItemType Directory -Force -Path inputs, outputs, logs, data | Out-Null
Write-Success "Directories ready"

# ── Step 3: Virtualenv ────────────────────────────────────────────────────────
Write-Host ""
Write-Info "Checking virtual environment..."

if (Test-Path ".venv") {
  if (Test-PythonExe ".\.venv\Scripts\python.exe") {
    Write-Success "Virtual environment already exists, skipping"
  } else {
    Write-Warn "Broken virtual environment detected, recreating..."
    Remove-Item -Recurse -Force ".venv" -ErrorAction SilentlyContinue
  }
}

if (!(Test-Path ".venv")) {
  Write-Info "Creating virtual environment..."
  try {
    & $pythonCmd -m venv .venv
    if (!(Test-PythonExe ".\.venv\Scripts\python.exe")) {
      throw "python.exe not runnable after venv creation"
    }
    Write-Success "Virtual environment created"
  } catch {
    Fail "Create venv" $_
  }
}

# Upgrade pip first
Write-Host ""
Write-Info "Upgrading pip..."
try {
  & ".\.venv\Scripts\python.exe" -m pip install --upgrade pip --quiet --no-warn-script-location
  Write-Success "pip upgraded"
} catch {
  Write-Warn "pip upgrade failed, continuing with dependency install..."
}

# ── Step 4: Dependencies ──────────────────────────────────────────────────────
Write-Host ""
Write-Info "Installing dependencies (first time may take 2-5 minutes)..."

try {
  & ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet --no-warn-script-location --prefer-binary
  Write-Success "Dependencies installed"
} catch {
  Fail "Dependency install failed" $_
}

# Desktop packages (pywebview etc.) require pythonnet on Windows which may fail
# to build on newer Python versions. Install separately and warn on failure.
Write-Host ""
Write-Info "Installing desktop extensions (optional, failure does not affect Web UI)..."
if (Test-Path "requirements-desktop.txt") {
  $desktopResult = & ".\.venv\Scripts\python.exe" -m pip install -r requirements-desktop.txt --quiet --no-warn-script-location --prefer-binary 2>&1
  if ($LASTEXITCODE -ne 0) {
    Write-Warn "Desktop extension install failed (Web UI still works normally)"
    Write-Host "  Reason: pywebview failed to compile on your Python version." -ForegroundColor DarkGray
    Write-Host "  Web UI is fully functional; desktop window mode is unavailable." -ForegroundColor DarkGray
  } else {
    Write-Success "Desktop extensions installed"
  }
}

# ── Step 5: .env ──────────────────────────────────────────────────────────────
Write-Host ""
Write-Info "Configuration file..."

if (!(Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Success ".env created"
  Write-Host ""
  Write-Host "  Tip: Start the Web UI and complete model & API key setup in the setup wizard."
  Write-Host "       No need to edit .env manually."
} else {
  Write-Success "Configuration file already exists"
}

# Trial packages include a temporary DeepSeek key. Keep .env in sync so older
# extracted folders do not keep a blank or stale key from a previous attempt.
try {
  $exampleKey = Select-String -Path ".env.example" -Pattern "^DEEPSEEK_API_KEY=(.+)$" | Select-Object -First 1
  if ($exampleKey -and $exampleKey.Matches[0].Groups[1].Value.Trim()) {
    $trialKey = $exampleKey.Matches[0].Groups[1].Value.Trim()
    $envText = Get-Content ".env" -Raw
    if ($envText -match "(?m)^DEEPSEEK_API_KEY=") {
      $envText = $envText -replace "(?m)^DEEPSEEK_API_KEY=.*$", "DEEPSEEK_API_KEY=$trialKey"
    } else {
      $envText = $envText.TrimEnd() + "`r`nDEEPSEEK_API_KEY=$trialKey`r`n"
    }
    [System.IO.File]::WriteAllText((Join-Path $ProjectDir ".env"), $envText, [System.Text.Encoding]::UTF8)
    Write-Success "Trial DeepSeek key written"
  }
} catch {
  Write-Warn "Trial key write incomplete — continue startup; check .env if model auth fails."
}

# ── Step 6: Doctor check (non-fatal) ─────────────────────────────────────────
Write-Host ""
Write-Info "Running environment check..."
try {
  $doctorOutput = & ".\.venv\Scripts\python.exe" agent.py doctor 2>&1
  $doctorOutput = $doctorOutput -join "`n"
  if ($doctorOutput -match "missing API key|not found") {
    Write-Warn "API key not configured (normal for first run)"
    Write-Host "  Complete setup in the Web UI setup wizard."
  } else {
    Write-Success "Environment check passed"
  }
} catch {
  Write-Warn "Environment check incomplete, but installation is fine."
}

# ── Step 7: Desktop launcher ──────────────────────────────────────────────────
Write-Host ""
Write-Info "Creating desktop launcher..."
$Desktop = [System.Environment]::GetFolderPath("Desktop")
$LauncherPath = Join-Path $Desktop "Launch Auctus Agent.bat"

if (Test-Path $Desktop) {
  $LauncherContent = "@echo off`r`ntitle Auctus Agent`r`nset POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe`r`nif not exist `"%POWERSHELL%`" set POWERSHELL=powershell`r`ncd /d `"$ProjectDir`"`r`nif not exist `"$ProjectDir\.venv\Scripts\python.exe`" (`r`n  echo Dependencies are missing. Running installer first...`r`n  `"%POWERSHELL%`" -NoProfile -ExecutionPolicy Bypass -File scripts\install_windows.ps1`r`n  if errorlevel 1 pause & exit /b 1`r`n)`r`nstart /min `"Auctus Agent Server`" `"%POWERSHELL%`" -NoProfile -WindowStyle Minimized -ExecutionPolicy Bypass -File scripts\start_windows.ps1`r`nfor /l %%i in (1,1,30) do (`r`n  `"%POWERSHELL%`" -NoProfile -Command `"try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/healthz -TimeoutSec 1 ^| Out-Null; exit 0 } catch { exit 1 }`" >nul 2>nul`r`n  if not errorlevel 1 goto open_ui`r`n  timeout /t 1 /nobreak >nul`r`n)`r`necho Auctus Agent did not start. Check the `"Auctus Agent Server`" window for errors.`r`npause`r`nexit /b 1`r`n:open_ui`r`nstart `"`" `"http://127.0.0.1:8000`"`r`n"
  [System.IO.File]::WriteAllText($LauncherPath, $LauncherContent, [System.Text.Encoding]::ASCII)
  Write-Success "Desktop launcher created: 'Launch Auctus Agent.bat'"
} else {
  Write-Warn "Cannot access Desktop, skipping launcher creation"
}

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Green
Write-Success "Auctus Agent installation complete!"
Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "  Next steps:"
Write-Host "  1. Double-click 'Launch Auctus Agent.bat' on your Desktop"
Write-Host "  2. Your browser will open http://127.0.0.1:8000 automatically"
Write-Host "  3. Follow the setup wizard to complete configuration"
Write-Host ""
Write-Host "  Tips:"
Write-Host "  - First-time setup requires selecting a model and entering an API key"
Write-Host "  - Telegram integration is optional (for remote control from your phone)"
Write-Host ""
