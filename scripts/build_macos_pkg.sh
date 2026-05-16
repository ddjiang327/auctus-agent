#!/usr/bin/env bash
# Build macOS .pkg installer for Auctus Agent.
# Usage: bash scripts/build_macos_pkg.sh [output_dir]
# Output: <output_dir>/Auctus-Agent-mac-v<VER>.pkg
#
# Requirements:
# - macOS (pkgbuild and productbuild are built-in)
# - Optional: Developer ID Installer certificate for signing
#
# The .pkg will:
# - Install to /Applications by default
# - Create desktop shortcut (optional)
# - Include uninstaller script
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# ── Version ──────────────────────────────────────────────────────────────────
VERSION=$(python3 -c "import sys; sys.path.insert(0,'app'); from version import APP_VERSION; print(APP_VERSION)" 2>/dev/null || echo "0.1.0")
echo "Building macOS .pkg installer for Auctus Agent v$VERSION"

# ── Output dir ───────────────────────────────────────────────────────────────
OUT_DIR="${1:-$ROOT_DIR/release}"
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

# ── Temp staging area ────────────────────────────────────────────────────────
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PKG_NAME="Auctus Agent"
PKG_ID="com.auctus.agent"
PKG_VERSION="${VERSION}"
PKG_ROOT="$TMP/pkg_root"
mkdir -p "$PKG_ROOT"

# ── Step 1: Create Application bundle structure ───────────────────────────────
# The .pkg will install files to /Applications/Auctus Agent/
APP_BUNDLE="$PKG_ROOT/Applications/Auctus Agent.app"
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

echo "Creating application bundle..."

# Info.plist for the app bundle
cat > "$APP_BUNDLE/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>Auctus Agent</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundleIdentifier</key>
    <string>com.auctus.agent</string>
    <key>CFBundleName</key>
    <string>Auctus Agent</string>
    <key>CFBundleDisplayName</key>
    <string>Auctus Agent</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>VERSION_PLACEHOLDER</string>
    <key>CFBundleVersion</key>
    <string>BUILD_PLACEHOLDER</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.15</string>
    <key>LSApplicationCategoryType</key>
    <string>public.app-category.productivity</string>
    <key>NSHumanReadableCopyright</key>
    <string>Copyright © 2026 Auctus. All rights reserved.</string>
    <key>NSPrincipalClass</key>
    <string>NSApplication</string>
</dict>
</plist>
PLIST

# Replace placeholders
sed -i '' "s/VERSION_PLACEHOLDER/$VERSION/g" "$APP_BUNDLE/Contents/Info.plist"
sed -i '' "s/BUILD_PLACEHOLDER/1/g" "$APP_BUNDLE/Contents/Info.plist"

# Launch script (the actual executable that starts the app)
cat > "$APP_BUNDLE/Contents/MacOS/Auctus Agent" <<'LAUNCHER'
#!/usr/bin/env bash
# Auctus Agent launcher script
# This script is invoked when the user clicks the app icon

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
AGENT_DIR="$APP_DIR/Resources/agent"

# Check if Python is available
if ! command -v python3 >/dev/null 2>&1; then
    osascript -e 'display dialog "Python 3 is required but not found. Please install Python 3.10 or later from python.org." buttons {"OK"} default button 1 with title "Auctus Agent - Python Required"'
    exit 1
fi

# Check Python version
PYTHON_VERSION=$(python3 -c 'import sys; print(sys.version_info.major * 100 + sys.version_info.minor)' 2>/dev/null || echo "0")
if [ "$PYTHON_VERSION" -lt 310 ]; then
    osascript -e 'display dialog "Python 3.10 or later is required. Current version is too old. Please update Python from python.org." buttons {"OK"} default button 1 with title "Auctus Agent - Python Too Old"'
    exit 1
fi

# Create data directories if they don't exist
mkdir -p "$HOME/Library/Application Support/Auctus Agent"
mkdir -p "$HOME/Library/Logs/Auctus Agent"

# Check if already installed (migrated from zip version)
if [ -d "$HOME/Desktop/Auctus Agent.app" ]; then
    # Launch from desktop location
    open "$HOME/Desktop/Auctus Agent.app"
    exit 0
fi

# Run setup if needed
if [ ! -f "$AGENT_DIR/.env" ]; then
    echo "First launch - running setup..."
    cd "$AGENT_DIR"
    if [ -x "scripts/install_mac.sh" ]; then
        scripts/install_mac.sh
    fi
fi

# Start the agent
cd "$AGENT_DIR"
export PYTHONPATH="$AGENT_DIR"
nohup .venv/bin/python -m uvicorn app.server:app --host 127.0.0.1 --port 8000 > "$HOME/Library/Logs/Auctus Agent/agent.log" 2>&1 &
SERVER_PID=$!

# Wait for server to start
sleep 3

# Open browser
open "http://127.0.0.1:8000/?setup=1"

# Monitor server process
while kill -0 $SERVER_PID 2>/dev/null; do
    sleep 5
done
LAUNCHER

chmod +x "$APP_BUNDLE/Contents/MacOS/Auctus Agent"

# ── Step 2: Copy agent files to Resources ─────────────────────────────────────
echo "Copying agent files..."
AGENT_RESOURCES="$APP_BUNDLE/Contents/Resources/agent"
mkdir -p "$AGENT_RESOURCES"

# Core files
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

# ── Step 2.5: Create and copy app icon ────────────────────────────────────────
echo "Creating app icon..."
RESOURCES_DIR="$APP_BUNDLE/Contents/Resources"
mkdir -p "$RESOURCES_DIR"

if [ -f "app/icon.png" ]; then
    echo "  Converting icon.png to AppIcon.icns..."
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
        echo "  ✓ Icon created"
    elif command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
        mkdir -p "$ICONSET_DIR"
        sips -z 16 16 app/icon.png --out "$ICONSET_DIR/icon_16x16.png" >/dev/null 2>&1 || true
        sips -z 32 32 app/icon.png --out "$ICONSET_DIR/icon_16x16@2x.png" >/dev/null 2>&1 || true
        sips -z 32 32 app/icon.png --out "$ICONSET_DIR/icon_32x32.png" >/dev/null 2>&1 || true
        sips -z 64 64 app/icon.png --out "$ICONSET_DIR/icon_32x32@2x.png" >/dev/null 2>&1 || true
        sips -z 128 128 app/icon.png --out "$ICONSET_DIR/icon_128x128.png" >/dev/null 2>&1 || true
        sips -z 256 256 app/icon.png --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null 2>&1 || true
        sips -z 256 256 app/icon.png --out "$ICONSET_DIR/icon_256x256.png" >/dev/null 2>&1 || true
        sips -z 512 512 app/icon.png --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null 2>&1 || true
        sips -z 512 512 app/icon.png --out "$ICONSET_DIR/icon_512x512.png" >/dev/null 2>&1 || true
        sips -z 1024 1024 app/icon.png --out "$ICONSET_DIR/icon_512x512@2x.png" >/dev/null 2>&1 || true
        if iconutil -c icns "$ICONSET_DIR" -o "$RESOURCES_DIR/AppIcon.icns" >/dev/null 2>&1; then
            echo "  ✓ Icon created"
        else
            echo "  Warning: Could not create .icns; package will use the default app icon"
        fi
        rm -rf "$ICONSET_DIR"
    else
        echo "  Warning: Could not create .icns; package will use the default app icon"
    fi
else
    echo "  Warning: app/icon.png not found"
fi
find "$AGENT_RESOURCES" -name '*.pyc' -delete 2>/dev/null || true
find "$AGENT_RESOURCES" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$AGENT_RESOURCES" -name '.DS_Store' -delete 2>/dev/null || true

# ── Step 3: Create uninstaller script ─────────────────────────────────────────
echo "Creating uninstaller..."
UNINSTALLER="$PKG_ROOT/Library/Application Support/Auctus Agent/Uninstall.sh"
mkdir -p "$(dirname "$UNINSTALLER")"
cat > "$UNINSTALLER" <<'UNINSTALL'
#!/usr/bin/env bash
# Auctus Agent Uninstaller
# Run this script to completely remove Auctus Agent

echo "=========================================="
echo "  Auctus Agent Uninstaller"
echo "=========================================="
echo ""
echo "This will remove:"
echo "  - Application files in /Applications/Auctus Agent.app"
echo "  - User data in ~/Library/Application Support/Auctus Agent"
echo "  - Logs in ~/Library/Logs/Auctus Agent"
echo "  - Desktop shortcut (if created)"
echo ""
read -p "Are you sure you want to uninstall? (y/N) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi

# Stop running server
pkill -f "uvicorn app.server:app" 2>/dev/null || true

# Remove application
if [ -d "/Applications/Auctus Agent.app" ]; then
    echo "Removing application..."
    rm -rf "/Applications/Auctus Agent.app"
fi

# Remove user data
if [ -d "$HOME/Library/Application Support/Auctus Agent" ]; then
    echo "Removing user data..."
    rm -rf "$HOME/Library/Application Support/Auctus Agent"
fi

# Remove logs
if [ -d "$HOME/Library/Logs/Auctus Agent" ]; then
    echo "Removing logs..."
    rm -rf "$HOME/Library/Logs/Auctus Agent"
fi

# Remove desktop shortcut
if [ -f "$HOME/Desktop/Auctus Agent.app" ]; then
    echo "Removing desktop shortcut..."
    rm -f "$HOME/Desktop/Auctus Agent.app"
fi

echo ""
echo "Auctus Agent has been uninstalled."
echo "Thank you for trying it!"
UNINSTALL

chmod +x "$UNINSTALLER"

# ── Step 4: Create desktop shortcut script (postinstall) ──────────────────────
POSTINSTALL_SCRIPT="$TMP/postinstall"
cat > "$POSTINSTALL_SCRIPT" <<'POSTINSTALL'
#!/usr/bin/env bash
# Post-install script - runs after .pkg installation

APP_DIR="/Applications/Auctus Agent.app"
DESKTOP_LINK="$HOME/Desktop/Auctus Agent.app"

# Create desktop shortcut
if [ -d "$APP_DIR" ] && [ ! -e "$DESKTOP_LINK" ]; then
    ln -sf "$APP_DIR" "$DESKTOP_LINK"
    echo "Created desktop shortcut."
fi

echo "Auctus Agent has been installed successfully!"
echo "You can find it in /Applications or on your Desktop."
POSTINSTALL

chmod +x "$POSTINSTALL_SCRIPT"

# ── Step 5: Build .pkg with pkgbuild ─────────────────────────────────────────
PKG_OUTPUT="$OUT_DIR/Auctus-Agent-mac-v${VERSION}.pkg"

echo "Building .pkg installer..."

# Create component package
pkgbuild \
    --identifier "$PKG_ID" \
    --version "$PKG_VERSION" \
    --root "$PKG_ROOT" \
    --scripts "$TMP" \
    --install-location "/" \
    "$TMP/Auctus-Agent-component.pkg"

echo "✓ Component package created."

# ── Step 6: Create distribution XML for productbuild ─────────────────────────
DIST_XML="$TMP/Distribution.xml"
cat > "$DIST_XML" <<'DIST'
<?xml version="1.0" encoding="UTF-8"?>
<installer-gui-script minSpecVersion="1">
    <title>Auctus Agent</title>
    <welcome file="welcome.html" language="en"/>
    <license file="LICENSE.txt" language="en"/>
    <conclusion file="conclusion.html" language="en"/>
    <domains enableAnywhere="true"/>
    <choices-outline>
        <line choice="com.auctus.agent"/>
    </choices-outline>
    <choice id="com.auctus.agent" title="Auctus Agent" description="Local AI Work Assistant">
        <pkg-ref id="com.auctus.agent.pkg"/>
    </choice>
    <pkg-ref id="com.auctus.agent.pkg" installKBytes="500000" version="VERSION_PLACEHOLDER" onVolumes="All">Auctus-Agent-component.pkg</pkg-ref>
</installer-gui-script>
DIST

sed -i '' "s/VERSION_PLACEHOLDER/${VERSION}/g" "$DIST_XML"

# ── Step 7: Create welcome/conclusion HTML files ──────────────────────────────
cat > "$TMP/welcome.html" <<'WELCOME'
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 20px; }
        h1 { color: #333; }
    </style>
</head>
<body>
    <h1>Welcome to Auctus Agent</h1>
    <p>Auctus Agent is a local AI work assistant that runs on your computer.</p>
    <p><strong>Features:</strong></p>
    <ul>
        <li>File processing: PDF, Markdown, Excel, Word</li>
        <li>Report generation: Markdown, Excel, HTML</li>
        <li>Web UI and Telegram integration</li>
        <li>Long-term memory and self-improvement</li>
    </ul>
    <p><strong>Requirements:</strong></p>
    <ul>
        <li>macOS 10.15 or later</li>
        <li>Python 3.10 or later (will be installed during first launch)</li>
    </ul>
</body>
</html>
WELCOME

cat > "$TMP/LICENSE.txt" <<'LICENSE'
Auctus Agent License Agreement

Copyright (c) 2026 Auctus. All rights reserved.

Permission is hereby granted to use this software for personal purposes.
Commercial use requires separate licensing.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
LICENSE

cat > "$TMP/conclusion.html" <<'CONCLUSION'
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; padding: 20px; }
        h1 { color: #333; }
    </style>
</head>
<body>
    <h1>Installation Complete!</h1>
    <p>Auctus Agent has been installed successfully.</p>
    <p><strong>To get started:</strong></p>
    <ol>
        <li>Find Auctus Agent in <strong>/Applications</strong> or on your <strong>Desktop</strong></li>
        <li>Click the Auctus Agent icon to launch</li>
        <li>Follow the first-time setup wizard</li>
    </ol>
    <p><strong>Need help?</strong> Visit our documentation or contact support.</p>
</body>
</html>
CONCLUSION

# ── Step 8: Build final .pkg with productbuild ────────────────────────────────
echo "Building final .pkg with productbuild..."

productbuild \
    --distribution "$DIST_XML" \
    --resources "$TMP" \
    --package-path "$TMP" \
    "$PKG_OUTPUT"

echo "✓ macOS .pkg installer created: $PKG_OUTPUT"

# ── Step 9: Size info ────────────────────────────────────────────────────────
PKG_SIZE=$(du -sh "$PKG_OUTPUT" | cut -f1)
echo "   Package size: $PKG_SIZE"

# ── Optional: Code signing ───────────────────────────────────────────────────
if [ -n "${DEVELOPER_ID:-}" ]; then
    echo "Signing package with Developer ID..."
    productsign --sign "$DEVELOPER_ID" "$PKG_OUTPUT" "$PKG_OUTPUT.signed"
    mv "$PKG_OUTPUT.signed" "$PKG_OUTPUT"
    echo "✓ Package signed."
fi

echo ""
echo "Done! Package saved to: $PKG_OUTPUT"
echo ""
echo "Next steps:"
echo "  1. Test the .pkg by installing on a clean macOS system"
echo "  2. For distribution, sign with a Developer ID Installer certificate:"
echo "     DEVELOPER_ID='Developer ID Installer: Your Name' ./build_macos_pkg.sh"
