#!/usr/bin/env bash
# Build a standalone Auctus Agent for macOS using PyInstaller.
# The output needs NO Python installed on the user's machine.
#
# Usage: bash scripts/build_pyinstaller_mac.sh
# Output: release/Auctus-Agent-mac-standalone-v<VERSION>.zip
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION=$(python3 -c "import sys; sys.path.insert(0,'app'); from version import APP_VERSION; print(APP_VERSION)" 2>/dev/null || echo "0.1.0")
echo "Building Auctus Agent v$VERSION (standalone — no Python required)"

if [ ! -f ".venv/bin/python" ]; then
  echo "ERROR: .venv not found. Run scripts/install_mac.sh first."
  exit 1
fi

echo "Installing PyInstaller..."
.venv/bin/pip install pyinstaller --quiet --upgrade

echo "Warming tiktoken cache..."
.venv/bin/python - <<'PY'
import tiktoken

for name in ("cl100k_base", "o200k_base", "p50k_base", "r50k_base"):
    tiktoken.get_encoding(name)
PY

echo "Running PyInstaller (this takes a few minutes)..."
.venv/bin/pyinstaller auctus-agent.spec --clean --noconfirm

DIST_SRC="dist/AuctusAgent"
OUT_NAME="Auctus-Agent-mac-v${VERSION}"
OUT_DIR="release/$OUT_NAME"

if [ -d "$OUT_DIR" ]; then
  echo "ERROR: $OUT_DIR already exists. Bump the version in app/version.py first."
  exit 1
fi
mkdir -p "$OUT_DIR/_app"

# Never ship local credentials or user setup state.
rm -f "$DIST_SRC/.env"

# Binary + libs go into _app/ (hidden from user)
cp -R "$DIST_SRC/"* "$OUT_DIR/_app/"
cp .env.example "$OUT_DIR/_app/"
mkdir -p "$OUT_DIR/_app/prompts"
cp prompts/system.md "$OUT_DIR/_app/prompts/"

# Only one file at the top level that users need to see
LAUNCHER="$OUT_DIR/Launch Auctus Agent.command"
cat > "$LAUNCHER" <<'LAUNCH'
#!/usr/bin/env bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR/_app"
mkdir -p data inputs outputs logs
[ -f .env ] || cp .env.example .env

echo "Starting Auctus Agent..."
echo "Logs: $DIR/_app/logs/launcher.log"

if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
  CURRENT_DIR="$(pwd -P)"
  RUNNING_DIR="$(curl -fsS http://127.0.0.1:8000/api/runtime-dir 2>/dev/null || true)"
  if [ -n "$RUNNING_DIR" ] && [ "$RUNNING_DIR" = "$CURRENT_DIR" ]; then
    echo "Auctus Agent is already running from this folder."
    open http://127.0.0.1:8000/ >/dev/null 2>&1 || true
    echo "You can close this Terminal window. Auctus Agent will keep running."
    exit 0
  fi
  echo "Auctus Agent is already running from another folder."
  if [ -n "$RUNNING_DIR" ]; then
    echo "Running folder:  $RUNNING_DIR"
  else
    echo "Running folder:  unknown (older build)"
  fi
  echo "This launcher:   $CURRENT_DIR"
  echo ""
  echo "Open the existing Auctus Agent window and click 'Quit Auctus Agent', then run this launcher again."
  echo "If you cannot find the window, stop AuctusAgent in Activity Monitor or restart your Mac."
  exit 1
fi

nohup ./AuctusAgent >> logs/launcher.log 2>&1 &
APP_PID=$!

printf "Loading"
for i in {1..60}; do
  if ! kill -0 "$APP_PID" >/dev/null 2>&1; then
    echo ""
    echo "Auctus Agent stopped before it was ready."
    echo "See logs/launcher.log for details."
    exit 1
  fi
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    echo " ready!"
    open http://127.0.0.1:8000/ >/dev/null 2>&1 || true
    echo "You can close this Terminal window. Auctus Agent will keep running."
    exit 0
  fi
  printf "."
  sleep 1
done

echo "Auctus Agent is still starting. Keeping this window open for diagnostics."
echo "If the app window does not appear, check logs/launcher.log."
wait "$APP_PID"
LAUNCH
chmod +x "$LAUNCHER"

ZIP="release/Auctus-Agent-mac-standalone-v${VERSION}.zip"
rm -f "$ZIP"
(cd release && zip -qr "../$ZIP" "$OUT_NAME")

echo ""
echo "✓ Build complete!"
echo "  Folder: $OUT_DIR"
echo "  Zip:    $ZIP"
echo "  Size:   $(du -sh "$OUT_DIR" | cut -f1)"
echo ""
echo "Share the zip. Users unzip and double-click 'Launch Auctus Agent.command'."
echo "No Python required on their machine."
