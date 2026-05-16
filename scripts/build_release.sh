#!/usr/bin/env bash
# Build Mac and Windows distribution packages.
# Usage: bash scripts/build_release.sh [output_dir]
#
# Options:
#   --zip         Build only zip packages (default)
#   --native      Build native installers (.pkg for Mac, .exe for Windows)
#   --all         Build all packages (zip + native)
#
# Outputs:
#   <output_dir>/Auctus-Agent-mac-v<VER>.zip
#   <output_dir>/Auctus-Agent-windows-v<VER>.zip
#   <output_dir>/Auctus-Agent-mac-v<VER>.pkg  (with --native or --all)
#   <output_dir>/Auctus-Agent-windows-v<VER>-setup.exe  (with --native or --all)
#
# Resulting layout (after user unzips):
#   Auctus-Agent-mac-v0.x.x/
#     Setup.command        ← double-click to install & launch
#     Start.command        ← double-click after setup
#     core/                ← all internal files, out of the way
set -euo pipefail

# Parse arguments
BUILD_TYPE="zip"
while [[ $# -gt 0 ]]; do
    case $1 in
        --zip|--native|--all)
            BUILD_TYPE="${1#--}"
            shift
            ;;
        *)
            # Assume it's the output directory
            break
            ;;
    esac
done

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

# ── Version ──────────────────────────────────────────────────────────────────
VERSION=$(python3 -c "import sys; sys.path.insert(0,'app'); from version import APP_VERSION; print(APP_VERSION)" 2>/dev/null || echo "0.1.0")
echo "Building Auctus Agent v$VERSION"

# ── Output dir ───────────────────────────────────────────────────────────────
OUT_DIR="${1:-$ROOT_DIR/release}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

# ── Temp staging area ────────────────────────────────────────────────────────
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Core files/dirs that go inside core/ subfolder
CORE_FILES=(
  agent.py
  requirements.txt
  README.md
  INSTALL.md
  .env.example
  .gitignore
  app
  prompts
  docs
  scripts
)

_copy_core() {
  local dest="$1"  # destination core/ directory
  for item in "${CORE_FILES[@]}"; do
    [ -e "$item" ] || continue
    if [ -d "$item" ]; then
      rsync -a \
        --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' \
        "$item/" "$dest/$item/"
    else
      cp "$item" "$dest/"
    fi
  done
  find "$dest" -name '*.pyc' -delete
  find "$dest" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
  find "$dest" -name '.DS_Store' -delete 2>/dev/null || true
}

# ── Mac package ──────────────────────────────────────────────────────────────
MAC_NAME="Auctus-Agent-mac-v${VERSION}"
MAC_DIR="$TMP/$MAC_NAME"
mkdir -p "$MAC_DIR/core"

echo "Copying files…"
_copy_core "$MAC_DIR/core"
chmod +x "$MAC_DIR/core/scripts/"*.sh 2>/dev/null || true

# Wrapper Setup.command: cd into core/, then run the real install script
cat > "$MAC_DIR/Setup.command" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR/core"
echo "== Auctus Agent Setup =="
echo "1) Install dependencies"
scripts/install_mac.sh
echo
echo "2) Start local Web UI"
scripts/start_mac.sh &
sleep 3
(command -v open >/dev/null 2>&1 && open "http://127.0.0.1:8000/?setup=1" || true)
EOF

# Wrapper Start.command
cat > "$MAC_DIR/Start.command" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR/core"
scripts/start_mac.sh &
sleep 3
(command -v open >/dev/null 2>&1 && open "http://127.0.0.1:8000" || true)
EOF

chmod +x "$MAC_DIR/Setup.command" "$MAC_DIR/Start.command"

MAC_ZIP="$OUT_DIR/${MAC_NAME}.zip"
rm -f "$MAC_ZIP"
(cd "$TMP" && zip -qr "$MAC_ZIP" "$MAC_NAME")
echo "✓  Mac  → $MAC_ZIP  ($(du -sh "$MAC_ZIP" | cut -f1))"

# ── Windows package ──────────────────────────────────────────────────────────
WIN_NAME="Auctus-Agent-windows-v${VERSION}"
WIN_DIR="$TMP/$WIN_NAME"
mkdir -p "$WIN_DIR/core"

_copy_core "$WIN_DIR/core"

_write_windows_wait_helper() {
  cat <<'EOF'
:wait_for_server
for /l %%i in (1,1,30) do (
  powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/healthz -TimeoutSec 1 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>nul
  if not errorlevel 1 goto open_ui
  timeout /t 1 /nobreak >nul
)
echo Auctus Agent did not start. Check the "Auctus Agent Server" window for errors.
pause
exit /b 1
:open_ui
start "" "http://127.0.0.1:8000"
exit /b 0
EOF
}

# Wrapper Setup.cmd: pushd handles UNC paths by mapping to a temp drive letter
{
cat <<'EOF'
@echo off
setlocal
set ROOT=%~dp0
set POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe
if not exist "%POWERSHELL%" set POWERSHELL=powershell
pushd "%ROOT%core"
if errorlevel 1 (
  echo ERROR: Cannot enter directory. Try copying files to a local drive first.
  pause & exit /b 1
)
echo == Auctus Agent Setup ==
echo 1) Install dependencies
"%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%core\scripts\install_windows.ps1"
if errorlevel 1 ( popd & pause & exit /b 1 )
echo.
echo 2) Start local Web UI
if not exist "%ROOT%core\.venv\Scripts\python.exe" (
  echo Dependencies are missing. Running installer first...
  "%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%core\scripts\install_windows.ps1"
  if errorlevel 1 ( popd & pause & exit /b 1 )
)
start /min "Auctus Agent Server" "%POWERSHELL%" -NoProfile -WindowStyle Minimized -ExecutionPolicy Bypass -File "%ROOT%core\scripts\start_windows.ps1"
call :wait_for_server
set START_RESULT=%ERRORLEVEL%
popd
endlocal & exit /b %START_RESULT%
EOF
_write_windows_wait_helper
} > "$WIN_DIR/Setup.cmd"

# Wrapper Start.cmd
{
cat <<'EOF'
@echo off
setlocal
set ROOT=%~dp0
set POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe
if not exist "%POWERSHELL%" set POWERSHELL=powershell
pushd "%ROOT%core"
if not exist "%ROOT%core\.venv\Scripts\python.exe" (
  echo Dependencies are missing. Running installer first...
  "%POWERSHELL%" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%core\scripts\install_windows.ps1"
  if errorlevel 1 ( popd & pause & exit /b 1 )
)
start /min "Auctus Agent Server" "%POWERSHELL%" -NoProfile -WindowStyle Minimized -ExecutionPolicy Bypass -File "%ROOT%core\scripts\start_windows.ps1"
call :wait_for_server
set START_RESULT=%ERRORLEVEL%
popd
endlocal & exit /b %START_RESULT%
EOF
_write_windows_wait_helper
} > "$WIN_DIR/Start.cmd"

# Re-encode all .ps1 files to UTF-8 BOM so Windows PowerShell reads Chinese correctly
python3 - <<'PY' "$WIN_DIR"
import sys, pathlib
for ps1 in pathlib.Path(sys.argv[1]).rglob("*.ps1"):
    text = ps1.read_text(encoding="utf-8")
    ps1.write_text(text, encoding="utf-8-sig")
PY

WIN_ZIP="$OUT_DIR/${WIN_NAME}.zip"
rm -f "$WIN_ZIP"
(cd "$TMP" && zip -qr "$WIN_ZIP" "$WIN_NAME")
echo "✓  Win  → $WIN_ZIP  ($(du -sh "$WIN_ZIP" | cut -f1))"

echo
echo "Done. Packages saved to: $OUT_DIR"

# ── Build native installers if requested ──────────────────────────────────────
if [[ "$BUILD_TYPE" == "native" || "$BUILD_TYPE" == "all" ]]; then
    echo
    echo "═══════════════════════════════════════════════════════"
    echo "  Building Native Installers"
    echo "═══════════════════════════════════════════════════════"

    # macOS .pkg
    if [[ "$(uname)" == "Darwin" ]]; then
        echo
        echo "Building macOS .pkg installer..."
        if [ -x "$(command -v pkgbuild)" ] && [ -x "$(command -v productbuild)" ]; then
            bash scripts/build_macos_pkg.sh "$OUT_DIR"
        else
            echo "⚠ pkgbuild/productbuild not available. Skipping .pkg build."
            echo "  Run on macOS to build .pkg installer."
        fi
    fi

    # Windows .exe (Inno Setup)
    if [[ "$(uname)" == "Linux" || "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
        echo
        echo "Building Windows .exe installer..."
        if [ -x "/c/Program Files (x86)/Inno Setup 6/ISCC.exe" ] || [ -x "/c/Program Files/Inno Setup 6/ISCC.exe" ]; then
            powershell -ExecutionPolicy Bypass -File scripts/build_windows_installer.ps1
        else
            echo "⚠ Inno Setup not found. Skipping .exe build."
            echo "  Install Inno Setup from: https://jrsoftware.org/isinfo.php"
            echo "  Or run on Windows to build .exe installer."
        fi
    fi
fi
