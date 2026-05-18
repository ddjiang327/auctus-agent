#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_DIR="$(pwd)"

# Auto-detect Python 3.10+ (Homebrew / pyenv / official installer / system)
_find_python() {
  local candidates=(
    python3.13 python3.12 python3.11 python3.10
    /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12
    /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.10
    /usr/local/bin/python3.13 /usr/local/bin/python3.12
    /usr/local/bin/python3.11 /usr/local/bin/python3.10
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3
    /Library/Frameworks/Python.framework/Versions/3.10/bin/python3
    python3 python
  )
  for cmd in "${candidates[@]}"; do
    if command -v "$cmd" >/dev/null 2>&1; then
      local ver
      ver=$("$cmd" -c "import sys; print(sys.version_info.major*100+sys.version_info.minor)" 2>/dev/null) || continue
      [ "$ver" -ge 310 ] && echo "$cmd" && return
    fi
  done
}

if [ -z "${PYTHON_BIN:-}" ]; then
  PYTHON_BIN="$(_find_python || true)"
fi
if [ -z "${PYTHON_BIN:-}" ]; then
  PYTHON_BIN="python3"  # will fail version check below with a clear message
fi

# ── User-friendly error handler ──────────────────────────────────────────────
_fail() {
  local step="$1"
  echo
  echo "╔══════════════════════════════════════════════════════════╗"
  echo "║  Installation failed: $step"
  echo "╠══════════════════════════════════════════════════════════╣"
  case "$step" in
    *Python*)
      echo "║  Please install Python 3.10 or later:"
      echo "║    https://www.python.org/downloads/"
      echo "║  After installing, double-click Setup.command to retry."
      ;;
    *venv*|*pip*|*deps*)
      echo "║  Dependency installation failed (network or disk space issue)."
      echo "║  Check your connection, then double-click Setup.command to retry."
      echo "║  If it still fails, run in Terminal:"
      echo "║    cd \"$PROJECT_DIR\""
      echo "║    scripts/install_mac.sh"
      ;;
    *)
      echo "║  Unknown error during installation ($step)."
      echo "║  Please screenshot this and contact Auctus support."
      ;;
  esac
  echo "╚══════════════════════════════════════════════════════════╝"
  echo
  exit 1
}

# ── Step 1: Python check ──────────────────────────────────────────────────────
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  _fail "Python not found"
fi

"$PYTHON_BIN" - <<'PY' || _fail "Python version check"
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f"Python version too old: need 3.10+, found {sys.version.split()[0]}")
PY

# ── Step 2: Directories ───────────────────────────────────────────────────────
mkdir -p inputs outputs logs data || _fail "Create directories"

# ── Step 3: Virtualenv ────────────────────────────────────────────────────────
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  "$PYTHON_BIN" -m venv .venv || _fail "Create venv"
fi

# ── Step 4: Dependencies ──────────────────────────────────────────────────────
echo "Installing dependencies (first time may take 1-3 minutes)..."
.venv/bin/python -m pip install --upgrade pip --quiet || _fail "Upgrade pip"
.venv/bin/python -m pip install -r requirements.txt --quiet --prefer-binary || _fail "Install deps"

echo "Installing desktop extensions (optional)..."
if [ -f "requirements-desktop.txt" ]; then
  .venv/bin/python -m pip install -r requirements-desktop.txt --quiet --prefer-binary 2>/dev/null || {
    echo "⚠ Desktop extension install failed (Web UI still works normally)"
  }
fi

# ── Step 5: .env ──────────────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Created .env from .env.example."
  echo "Tip: Start the Web UI first and complete model & API key setup in the setup wizard."
fi

# ── Step 6: Reset setup wizard so it always opens after install ──────────────
.venv/bin/python - <<'PY' 2>/dev/null || true
import sqlite3, pathlib
db = pathlib.Path("data/secretary.db")
if db.exists():
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM setup_state WHERE key='onboarding_completed'")
PY

# ── Step 7: Doctor check (non-fatal) ─────────────────────────────────────────
.venv/bin/python agent.py doctor 2>/dev/null || {
  echo
  echo "Doctor check did not pass (usually means API key not yet configured). You can still start the Web UI to complete setup."
}

# ── Step 8: Desktop launcher ──────────────────────────────────────────────────
DESKTOP="$HOME/Desktop"
LAUNCHER="$DESKTOP/Auctus Agent"

if [ -d "$DESKTOP" ]; then
  # Remove old launcher if exists
  rm -f "$DESKTOP/启动 Auctus Agent.command" 2>/dev/null || true
  
  # Create .app bundle for better icon support
  APP_BUNDLE="$LAUNCHER.app"
  rm -rf "$APP_BUNDLE" 2>/dev/null || true
  mkdir -p "$APP_BUNDLE/Contents/MacOS"
  mkdir -p "$APP_BUNDLE/Contents/Resources"
  
  # Create Info.plist
  cat > "$APP_BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundleName</key>
    <string>Auctus Agent</string>
    <key>CFBundleDisplayName</key>
    <string>Auctus Agent</string>
    <key>CFBundleIdentifier</key>
    <string>com.auctus.agent</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
</dict>
</plist>
PLIST

  # Create launcher script with wait-for-server
  cat > "$APP_BUNDLE/Contents/MacOS/launcher" <<LAUNCHER
#!/usr/bin/env bash
cd "$PROJECT_DIR"

# Start server in background
.venv/bin/uvicorn app.server:app --host 127.0.0.1 --port 8000 &
SERVER_PID=\$!

# Wait for server to be ready
echo "Starting Auctus Agent..."
for i in \$(seq 1 30); do
  if curl -s http://127.0.0.1:8000/healthz > /dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

# Open browser
open "http://127.0.0.1:8000"

# Wait for server
wait \$SERVER_PID
LAUNCHER
  chmod +x "$APP_BUNDLE/Contents/MacOS/launcher"
  
  # Copy icon if exists
  if [ -f "app/icon.icns" ]; then
    cp "app/icon.icns" "$APP_BUNDLE/Contents/Resources/AppIcon.icns"
  elif [ -f "app/icon.png" ]; then
    # Create iconset and convert to icns
    mkdir -p /tmp/icon.iconset
    sips -z 16 16 app/icon.png --out /tmp/icon.iconset/icon_16x16.png 2>/dev/null || true
    sips -z 32 32 app/icon.png --out /tmp/icon.iconset/icon_16x16@2x.png 2>/dev/null || true
    sips -z 32 32 app/icon.png --out /tmp/icon.iconset/icon_32x32.png 2>/dev/null || true
    sips -z 64 64 app/icon.png --out /tmp/icon.iconset/icon_32x32@2x.png 2>/dev/null || true
    sips -z 128 128 app/icon.png --out /tmp/icon.iconset/icon_128x128.png 2>/dev/null || true
    sips -z 256 256 app/icon.png --out /tmp/icon.iconset/icon_128x128@2x.png 2>/dev/null || true
    sips -z 256 256 app/icon.png --out /tmp/icon.iconset/icon_256x256.png 2>/dev/null || true
    sips -z 512 512 app/icon.png --out /tmp/icon.iconset/icon_256x256@2x.png 2>/dev/null || true
    sips -z 512 512 app/icon.png --out /tmp/icon.iconset/icon_512x512.png 2>/dev/null || true
    sips -z 1024 1024 app/icon.png --out /tmp/icon.iconset/icon_512x512@2x.png 2>/dev/null || true
    iconutil -c icns /tmp/icon.iconset -o "$APP_BUNDLE/Contents/Resources/AppIcon.icns" 2>/dev/null || true
    rm -rf /tmp/icon.iconset
  fi
  
  echo "Desktop launcher created: \"Auctus Agent\""
fi

echo
echo "✓ Auctus Agent installation complete."
echo "  Double-click 'Auctus Agent' on the Desktop to launch, or run: scripts/start_mac.sh"
