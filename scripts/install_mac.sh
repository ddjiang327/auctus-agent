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
  echo "║  安装失败：$step"
  echo "╠══════════════════════════════════════════════════════════╣"
  case "$step" in
    *Python*)
      echo "║  请安装 Python 3.10 或更新版本："
      echo "║    https://www.python.org/downloads/"
      echo "║  安装后双击 Setup.command 重试。"
      ;;
    *venv*|*pip*|*依赖*)
      echo "║  依赖安装出错，可能是网络问题或磁盘空间不足。"
      echo "║  请检查网络连接后，双击 Setup.command 重试。"
      echo "║  如仍失败，可在终端运行："
      echo "║    cd \"$PROJECT_DIR\""
      echo "║    scripts/install_mac.sh"
      ;;
    *)
      echo "║  安装中遇到未知错误（$step）。"
      echo "║  请截图此信息，联系 Auctus 支持。"
      ;;
  esac
  echo "╚══════════════════════════════════════════════════════════╝"
  echo
  exit 1
}

# ── Step 1: Python check ──────────────────────────────────────────────────────
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  _fail "Python 未找到"
fi

"$PYTHON_BIN" - <<'PY' || _fail "Python 版本检查"
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f"Python 版本过低：需要 3.10+，当前：{sys.version.split()[0]}")
PY

# ── Step 2: Directories ───────────────────────────────────────────────────────
mkdir -p inputs outputs logs data || _fail "创建目录"

# ── Step 3: Virtualenv ────────────────────────────────────────────────────────
if [ ! -d ".venv" ]; then
  echo "创建虚拟环境…"
  "$PYTHON_BIN" -m venv .venv || _fail "创建 venv"
fi

# ── Step 4: Dependencies ──────────────────────────────────────────────────────
echo "安装依赖（首次约需 1-3 分钟，请稍候）…"
.venv/bin/python -m pip install --upgrade pip --quiet || _fail "升级 pip"
.venv/bin/python -m pip install -r requirements.txt --quiet || _fail "安装依赖"

# ── Step 5: .env ──────────────────────────────────────────────────────────────
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Created .env from .env.example."
  echo "提示：你可以先启动 Web UI，在"首次设置向导/设置面板"里完成模型与 API key 配置。"
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
  echo "doctor 检查未通过（通常是还没配置 API key）。你仍然可以先启动 Web UI 继续完成设置。"
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
  
  echo "已在桌面创建启动入口：\"Auctus Agent\""
fi

echo
echo "✓ Auctus Agent 安装完成。"
echo "  桌面双击「启动 Auctus Agent」即可启动，或运行：scripts/start_mac.sh"
