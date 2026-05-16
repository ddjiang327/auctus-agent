# Auctus Agent Release Rules

## Repository

GitHub repository name:

```text
auctus-agent
```

This repository contains the local desktop/web Agent only:

```text
app/
agent.py
prompts/
scripts/
tests/
docs/
Setup.command
Setup.bat
Start.command
Start.bat
README.md
INSTALL.md
requirements.txt
```

Do not commit user data, local databases, generated outputs, logs, virtual environments, or API keys.

## Version Source

The local Agent version is defined in:

```text
app/version.py
```

The local server exposes it at:

```text
GET /api/version
```

Example:

```json
{
  "name": "Auctus Agent",
  "version": "0.1.0",
  "channel": "dev",
  "api_compat": "v1",
  "update_check_url": null
}
```

## Versioning

Use semantic versions:

```text
MAJOR.MINOR.PATCH
```

- PATCH: bug fixes and small UI copy changes.
- MINOR: new user-visible features or compatible API changes.
- MAJOR: breaking config, data, API, or installation changes.

Pre-1.0 rule: until payment, hosted API deployment, and installer UX are production-ready, keep versions under `1.0.0`.

## Git Tags

Use Agent-specific tags:

```text
agent-v0.1.0
agent-v0.1.1
agent-v0.2.0
```

## Release Package Formats

Starting from v0.1.16, we provide multiple distribution formats:

### 1. Zip Packages (Portable)

```text
Auctus-Agent-mac-v0.1.16.zip
Auctus-Agent-windows-v0.1.16.zip
```

Best for: Power users who prefer manual installation and control.

### 2. Native Installers

```text
Auctus-Agent-mac-v0.1.16.pkg          # macOS installer
Auctus-Agent-windows-v0.1.16-setup.exe # Windows installer
```

Best for: Standard users who want a familiar installation experience.

Features:
- Standard platform installation wizard
- Automatic shortcut creation
- Clean uninstallation support

### 3. Desktop Applications

```text
Auctus-Agent-Desktop-mac-v0.1.16.zip   # macOS .app bundle
Auctus-Agent-Desktop-win-v0.1.16.zip   # Windows .exe
```

Best for: Users who want a native desktop app experience without browser.

Features:
- Native window (no browser tab)
- System tray/Dock integration
- Auto-start backend on launch

### Package Contents

All packages include:

```text
app/                    # FastAPI application
agent.py               # CLI entry point
prompts/               # System prompts
scripts/               # Build and utility scripts
requirements.txt       # Python dependencies
README.md              # Documentation
INSTALL.md             # Installation guide
CHANGELOG.md           # Version history
docs/                  # Additional documentation
```

Excluded from all packages:

```text
.env                   # User configuration (created on first run)
data/                  # Local database (created on first run)
inputs/                # User uploads (created on first run)
outputs/               # Generated files (created on first run)
logs/                  # Application logs (created on first run)
.venv/                 # Python virtual environment (created on setup)
```

## Update Flow

Short-term:

1. App shows the current version from `/api/version`.
2. App checks the configured `UPDATE_CHECK_URL` or the Auctus API `/version` endpoint.
3. If a newer version exists, the app shows a download link.
4. User downloads and runs the new installer package.

Do not implement silent auto-update yet. The Python app may need dependency installs, data migrations, and user confirmation.

Long-term:

- Package as Electron/Tauri/native installer.
- Add signed installers.
- Add in-app download, install, and restart.

## Release Checklist

### Pre-Release

1. **Update version**
   ```bash
   # Edit app/version.py
   APP_VERSION = "0.1.16"
   ```

2. **Update changelog**
   ```bash
   # Edit CHANGELOG.md with new version details
   ```

3. **Run tests**
   ```bash
   python -m pytest -q
   ```

4. **Run compile check**
   ```bash
   PYTHONPYCACHEPREFIX=.pycache_check python -m compileall -q app agent.py
   rm -rf .pycache_check
   ```

5. **Confirm `.gitignore`** excludes local data and secrets

### Build Packages

6. **Build zip packages** (all platforms)
   ```bash
   bash scripts/build_release.sh
   ```

7. **Build native installers** (platform-specific)
   
   macOS:
   ```bash
   bash scripts/build_macos_pkg.sh
   bash scripts/build_desktop_mac.sh
   ```
   
   Windows:
   ```powershell
   .\scripts\build_windows_installer.ps1
   .\scripts\build_desktop_win.ps1
   ```

### Create Release

8. **Create and push tag**
   ```bash
   git add -A
   git commit -m "Release v0.1.16"
   git tag agent-v0.1.16
   git push origin main
   git push origin agent-v0.1.16
   ```

9. **Create GitHub Release**
   - Go to https://github.com/ddjiang327/auctus-agent/releases
   - Click "Draft a new release"
   - Choose tag: `agent-v0.1.16`
   - Title: `Auctus Agent v0.1.16`
   - Copy release notes from CHANGELOG.md
   - Upload all packages from `release/` directory:
     - `Auctus-Agent-mac-v0.1.16.zip`
     - `Auctus-Agent-windows-v0.1.16.zip`
     - `Auctus-Agent-mac-v0.1.16.pkg` (if built on macOS)
     - `Auctus-Agent-windows-v0.1.16-setup.exe` (if built on Windows)
     - `Auctus-Agent-Desktop-mac-v0.1.16.zip` (if built on macOS)
     - `Auctus-Agent-Desktop-win-v0.1.16.zip` (if built on Windows)

10. **Publish release**
    - Set as pre-release if not production-ready
    - Click "Publish release"

### Post-Release

11. **Verify download links work**
12. **Test installation on clean systems** (if possible)
13. **Announce release** (Discord, Twitter, etc.)

