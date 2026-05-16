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
LAUNCHER="$DESKTOP/启动 Auctus Agent.command"

if [ -d "$DESKTOP" ] && [ ! -f "$LAUNCHER" ]; then
  cat > "$LAUNCHER" <<LAUNCHER
#!/usr/bin/env bash
cd "$PROJECT_DIR"
command -v open >/dev/null 2>&1 && open "http://127.0.0.1:8000" || true
scripts/start_mac.sh
LAUNCHER
  chmod +x "$LAUNCHER"
  echo "已在桌面创建启动入口：\"启动 Auctus Agent.command\""
fi

echo
echo "✓ Auctus Agent 安装完成。"
echo "  桌面双击「启动 Auctus Agent」即可启动，或运行：scripts/start_mac.sh"
