# Changelog

All notable changes to Auctus Agent will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.17] - 2026-05-16

### Added

#### Phase 13.10: Desktop Application Enhanced Features
- Added `app/desktop_enhanced.py` - enhanced desktop app with system tray and hotkeys
- **System Tray (pystray)**:
  - Minimize to tray instead of closing
  - Tray menu: Show/Hide window, Quit
  - Status indicator in tray icon
- **Global Hotkeys (pynput)**:
  - Cmd/Ctrl+Shift+A to toggle window visibility
  - Cross-platform support (macOS/Windows/Linux)
- **File Drag & Drop Upload**:
  - Drag files anywhere on the window to upload
  - Visual drop overlay with instructions
  - Automatic upload to `inputs/` directory
  - Progress and result feedback in chat
- **System Notifications (plyer)**:
  - Startup notification with hotkey hint
  - Background running notification when minimized
  - Task completion and error notifications
- Updated `requirements.txt` with new dependencies: pystray, Pillow, pynput, plyer

### Changed

- Updated roadmap with Phase 13.10 and revised future extensions list

## [0.1.16] - 2026-05-16

### Added

#### Phase 13.7: macOS Native Installer (.pkg)
- Added `scripts/build_macos_pkg.sh` to build native macOS installer packages
- Created `.pkg` installer with standard macOS installation wizard
- Supports installation to `/Applications` with optional custom location
- Includes welcome screen, license agreement, and installation summary
- Provides uninstaller script at `/Library/Application Support/Auctus Agent/Uninstall.sh`

#### Phase 13.8: Windows Native Installer (.exe)
- Added `scripts/build_windows_setup.iss` for Inno Setup-based installer
- Added `scripts/build_windows_installer.ps1` for automated Windows installer builds
- Created standard Windows installation wizard (similar to Chrome installer experience)
- Supports installation to `C:\Program Files\Auctus Agent` with custom path selection
- Automatically creates desktop shortcuts and Start Menu entries
- Registers in "Control Panel → Programs and Features" for easy uninstallation

#### Phase 13.9: Desktop Application Shell (PyWebView)
- Added `app/desktop.py` - native desktop application wrapper using PyWebView
- Implemented auto-starting FastAPI backend on random available port
- Added health check polling to ensure backend is ready before opening window
- Created native window with system title bar (1400x900, resizable, min 900x600)
- Implemented graceful shutdown - backend process cleaned up when window closes
- Added single instance enforcement to prevent multiple app windows
- Cross-platform support: Windows (Edge WebView2), macOS (WKWebView), Linux (GTK WebKit)
- Added `scripts/build_desktop_mac.sh` to build macOS `.app` bundles
- Added `scripts/build_desktop_win.ps1` to build Windows `.exe` executables

#### Build System Updates
- Updated `scripts/build_release.sh` with `--native` and `--all` flags
- Added `pywebview>=5.0` to `requirements.txt`

### Fixed

- Fixed installation script path references (`secretary/` → `auctus-agent/`)
- Updated `Setup.command`, `Setup.bat`, `Start.command`, `Start.bat` to use correct directory names
- Updated documentation (`README.md`, `INSTALL.md`) with correct paths
- Fixed code comments referencing old directory structure

### Changed

- Improved build release process to support multiple output formats (zip, pkg, exe, app)
- Enhanced installation experience with native platform installers

## [0.1.15] - 2026-05-15

### Added

- Phase 12.5: Local Cron Jobs for automated tasks
- Phase 13: Cloud API infrastructure foundation
- Phase 13.5: GitHub repository restructuring

### Fixed

- Various bug fixes and stability improvements

## [0.1.0] - 2026-05-XX

### Added

- Initial release of Auctus Agent
- CLI interface with natural language task processing
- Web UI for interactive chat and file management
- Telegram bot integration
- File processing: PDF, Markdown, Excel, Word documents
- Report generation in multiple formats
- Long-term memory with SQLite and ChromaDB
- Tool system with extensible architecture
- Self-evolution framework (Closed Learning Loop)

[Unreleased]: https://github.com/ddjiang327/auctus-agent/compare/v0.1.17...HEAD
[0.1.17]: https://github.com/ddjiang327/auctus-agent/compare/v0.1.16...v0.1.17
[0.1.16]: https://github.com/ddjiang327/auctus-agent/compare/v0.1.15...v0.1.16
[0.1.15]: https://github.com/ddjiang327/auctus-agent/compare/v0.1.0...v0.1.15
[0.1.0]: https://github.com/ddjiang327/auctus-agent/releases/tag/v0.1.0
