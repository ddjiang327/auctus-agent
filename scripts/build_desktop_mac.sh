#!/usr/bin/env bash
# Build macOS .app bundle for Auctus Agent Desktop
# Usage: bash scripts/build_desktop_mac.sh [output_dir]
# Output: <output_dir>/Auctus Agent.app (and .zip)
#
# This creates a standalone .app bundle that can be:
# - Double-clicked to launch
# - Dragged to /Applications
# - Distributed via DMG or zip
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# ── Version ──────────────────────────────────────────────────────────────────
VERSION=$(python3 -c "import sys; sys.path.insert(0,'app'); from version import APP_VERSION; print(APP_VERSION)" 2>/dev/null || echo "0.1.0")
echo "Building Auctus Agent Desktop v$VERSION for macOS"

# ── Output dir ───────────────────────────────────────────────────────────────
OUT_DIR="${1:-$ROOT_DIR/release}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

# ── Temp staging area ────────────────────────────────────────────────────────
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

APP_NAME="Auctus Agent"
APP_BUNDLE="$TMP/$APP_NAME.app"
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

echo "Creating app bundle structure..."

# ── Step 1: Info.plist ───────────────────────────────────────────────────────
cat > "$APP_BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>AuctusAgent</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundleIdentifier</key>
    <string>com.auctus.agent.desktop</string>
    <key>CFBundleName</key>
    <string>Auctus Agent</string>
    <key>CFBundleDisplayName</key>
    <string>Auctus Agent</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>${VERSION}</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.15</string>
    <key>LSApplicationCategoryType</key>
    <string>public.app-category.productivity</string>
    <key>NSHumanReadableCopyright</key>
    <string>Copyright © 2026 Auctus. All rights reserved.</string>
    <key>NSPrincipalClass</key>
    <string>NSApplication</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSUIElement</key>
    <false/>
</dict>
</plist>
PLIST

# ── Step 2: Main executable (launcher) ───────────────────────────────────────
cat > "$APP_BUNDLE/Contents/MacOS/AuctusAgent" <<'LAUNCHER'
#!/usr/bin/env bash
# Auctus Agent Desktop Launcher

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RESOURCES_DIR="$APP_DIR/Resources"
AGENT_DIR="$RESOURCES_DIR/agent"

# Check Python
if ! command -v python3 >/dev/null 2>&1; then
    osascript -e 'display dialog "Python 3 is required but not found. Please install Python 3.10 or later." buttons {"OK"} default button 1 with title "Auctus Agent"'
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(sys.version_info.major * 100 + sys.version_info.minor)' 2>/dev/null || echo "0")
if [ "$PYTHON_VERSION" -lt 310 ]; then
    osascript -e 'display dialog "Python 3.10 or later is required. Please update Python." buttons {"OK"} default button 1 with title "Auctus Agent"'
    exit 1
fi

# Create user data directories
USER_DATA="$HOME/Library/Application Support/Auctus Agent"
mkdir -p "$USER_DATA/data"
mkdir -p "$USER_DATA/inputs"
mkdir -p "$USER_DATA/outputs"
mkdir -p "$USER_DATA/logs"

# Check if we need to run setup
if [ ! -f "$AGENT_DIR/.venv/bin/python" ]; then
    osascript -e 'display dialog "First time setup required. This may take a few minutes." buttons {"OK"} default button 1 with title "Auctus Agent" giving up after 2'
    
    cd "$AGENT_DIR"
    if [ -x "scripts/install_mac.sh" ]; then
        scripts/install_mac.sh || {
            osascript -e 'display dialog "Setup failed. Please check the logs." buttons {"OK"} default button 1 with title "Auctus Agent"'
            exit 1
        }
    fi
fi

# Launch desktop app
cd "$AGENT_DIR"
export PYTHONPATH="$AGENT_DIR"
exec .venv/bin/python -m app.desktop
LAUNCHER

chmod +x "$APP_BUNDLE/Contents/MacOS/AuctusAgent"

# ── Step 3: Copy agent files to Resources ────────────────────────────────────
echo "Copying agent files..."
AGENT_RESOURCES="$APP_BUNDLE/Contents/Resources/agent"
mkdir -p "$AGENT_RESOURCES"

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

for item in "${CORE_FILES[@]}"; do
    if [ -e "$item" ]; then
        if [ -d "$item" ]; then
            rsync -a \
                --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' \
                --exclude='.venv' --exclude='data' --exclude='inputs' --exclude='outputs' --exclude='logs' \
                "$item/" "$AGENT_RESOURCES/$item/"
        else
            cp "$item" "$AGENT_RESOURCES/"
        fi
    fi
done

# Clean up
find "$AGENT_RESOURCES" -name '*.pyc' -delete 2>/dev/null || true
find "$AGENT_RESOURCES" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$AGENT_RESOURCES" -name '.DS_Store' -delete 2>/dev/null || true

# ── Step 4: Create icon ──────────────────────────────────────────────────────
echo "Creating app icon..."
RESOURCES_DIR="$APP_BUNDLE/Contents/Resources"
if [ -f "app/icon.icns" ]; then
    cp "app/icon.icns" "$RESOURCES_DIR/AppIcon.icns"
    echo "  ✓ Icon added"
elif [ -f "app/icon.png" ]; then
    ICON_PYTHON="python3"
    if [ -x ".venv/bin/python" ]; then
        ICON_PYTHON=".venv/bin/python"
    fi
    ICONSET_DIR="$TMP/icon.iconset"
    if "$ICON_PYTHON" - "$RESOURCES_DIR/AppIcon.icns" <<'PY' >/dev/null 2>&1
import sys
from PIL import Image

img = Image.open("app/icon.png")
img.save(sys.argv[1], format="ICNS")
PY
    then
        echo "  ✓ Icon generated from app/icon.png"
    elif command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
        mkdir -p "$ICONSET_DIR"
        sips -z 16 16 app/icon.png --out "$ICONSET_DIR/icon_16x16.png" >/dev/null 2>&1
        sips -z 32 32 app/icon.png --out "$ICONSET_DIR/icon_16x16@2x.png" >/dev/null 2>&1
        sips -z 32 32 app/icon.png --out "$ICONSET_DIR/icon_32x32.png" >/dev/null 2>&1
        sips -z 64 64 app/icon.png --out "$ICONSET_DIR/icon_32x32@2x.png" >/dev/null 2>&1
        sips -z 128 128 app/icon.png --out "$ICONSET_DIR/icon_128x128.png" >/dev/null 2>&1
        sips -z 256 256 app/icon.png --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null 2>&1
        sips -z 256 256 app/icon.png --out "$ICONSET_DIR/icon_256x256.png" >/dev/null 2>&1
        sips -z 512 512 app/icon.png --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null 2>&1
        sips -z 512 512 app/icon.png --out "$ICONSET_DIR/icon_512x512.png" >/dev/null 2>&1
        sips -z 1024 1024 app/icon.png --out "$ICONSET_DIR/icon_512x512@2x.png" >/dev/null 2>&1
        if iconutil -c icns "$ICONSET_DIR" -o "$RESOURCES_DIR/AppIcon.icns" >/dev/null 2>&1; then
            echo "  ✓ Icon generated from app/icon.png"
        else
            echo "  ⚠ Could not generate .icns from app/icon.png, using default"
        fi
    else
        echo "  ⚠ Could not generate .icns from app/icon.png, using default"
    fi
else
    echo "  ⚠ No usable icon source found, using default"
fi

# ── Step 5: Sign the app (optional) ──────────────────────────────────────────
if [ -n "${CODESIGN_IDENTITY:-}" ]; then
    echo "Signing app bundle..."
    codesign --force --deep --sign "$CODESIGN_IDENTITY" "$APP_BUNDLE"
    echo "  ✓ App signed"
fi

# ── Step 6: Output ───────────────────────────────────────────────────────────
APP_OUTPUT="$OUT_DIR/$APP_NAME.app"
rm -rf "$APP_OUTPUT"
cp -R "$APP_BUNDLE" "$APP_OUTPUT"

# Also create a zip for distribution
ZIP_OUTPUT="$OUT_DIR/Auctus-Agent-Desktop-mac-v${VERSION}.zip"
rm -f "$ZIP_OUTPUT"
(cd "$TMP" && zip -qr "$ZIP_OUTPUT" "$APP_NAME.app")

echo ""
echo "✓ macOS Desktop app created:"
echo "  App: $APP_OUTPUT"
echo "  Zip: $ZIP_OUTPUT"
echo "  Size: $(du -sh "$APP_OUTPUT" | cut -f1)"

# ── Step 7: Verify ───────────────────────────────────────────────────────────
echo ""
echo "To test the app:"
echo "  1. Double-click: $APP_OUTPUT"
echo "  2. Or drag to /Applications and launch from there"
echo ""
echo "To distribute:"
echo "  - Share the .zip file"
echo "  - Or create a DMG with: hdiutil create -volname 'Auctus Agent' -srcfolder '$APP_OUTPUT' -ov -format UDZO Auctus-Agent.dmg"
echo ""
