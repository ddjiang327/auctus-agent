# Auctus Agent v0.1.16

## 🎉 What's New

This release introduces **native desktop application support** and **platform-native installers**, significantly improving the installation and user experience.

### ✨ New Features

#### 🍎 macOS Native Installer (.pkg)
- Standard macOS installation wizard experience
- Install to `/Applications` or custom location
- Includes welcome screen, license agreement, and summary
- Built-in uninstaller support

#### 🪟 Windows Native Installer (.exe)
- Standard Windows installation wizard (Chrome-like experience)
- Install to `C:\Program Files\Auctus Agent` with custom path selection
- Automatic desktop shortcuts and Start Menu entries
- Registered in Control Panel for easy uninstallation

#### 🖥️ Desktop Application Shell (PyWebView)
- **No browser needed** - native desktop window
- Auto-starts backend and opens window in 3 seconds
- System title bar and native window controls
- Graceful shutdown - backend cleaned up on close
- Single instance enforcement (prevents multiple windows)
- Cross-platform: Windows (Edge WebView2), macOS (WKWebView), Linux (GTK)

### 📦 Distribution Formats

| Format | macOS | Windows | Best For |
|--------|-------|---------|----------|
| Zip | ✅ | ✅ | Power users, portable use |
| Installer (.pkg/.exe) | ✅ | ✅ | Standard users, familiar experience |
| Desktop App (.app/.exe) | ✅ | ✅ | Native app experience, no browser |

### 🔧 Installation

**macOS:**
```bash
# Option 1: Zip (portable)
curl -L -o auctus-agent.zip https://github.com/ddjiang327/auctus-agent/releases/download/agent-v0.1.16/Auctus-Agent-mac-v0.1.16.zip
unzip auctus-agent.zip
cd Auctus-Agent-mac-v0.1.16
./Setup.command

# Option 2: Native installer
curl -L -o auctus-agent.pkg https://github.com/ddjiang327/auctus-agent/releases/download/agent-v0.1.16/Auctus-Agent-mac-v0.1.16.pkg
open auctus-agent.pkg

# Option 3: Desktop app
curl -L -o auctus-agent-desktop.zip https://github.com/ddjiang327/auctus-agent/releases/download/agent-v0.1.16/Auctus-Agent-Desktop-mac-v0.1.16.zip
unzip auctus-agent-desktop.zip
open Auctus\ Agent.app
```

**Windows:**
```powershell
# Option 1: Zip (portable)
Invoke-WebRequest -Uri "https://github.com/ddjiang327/auctus-agent/releases/download/agent-v0.1.16/Auctus-Agent-windows-v0.1.16.zip" -OutFile "auctus-agent.zip"
Expand-Archive -Path "auctus-agent.zip" -DestinationPath "."
.\Auctus-Agent-windows-v0.1.16\Setup.bat

# Option 2: Native installer
Invoke-WebRequest -Uri "https://github.com/ddjiang327/auctus-agent/releases/download/agent-v0.1.16/Auctus-Agent-windows-v0.1.16-setup.exe" -OutFile "auctus-agent-setup.exe"
.\auctus-agent-setup.exe

# Option 3: Desktop app
Invoke-WebRequest -Uri "https://github.com/ddjiang327/auctus-agent/releases/download/agent-v0.1.16/Auctus-Agent-Desktop-win-v0.1.16.zip" -OutFile "auctus-agent-desktop.zip"
Expand-Archive -Path "auctus-agent-desktop.zip" -DestinationPath "."
.\Auctus\ Agent\AuctusAgent.exe
```

### 🐛 Bug Fixes

- Fixed installation script path references (`secretary/` → `auctus-agent/`)
- Updated all setup scripts and documentation with correct paths

### 📋 Known Issues

- Desktop app builds require platform-specific testing
- macOS .pkg and Windows .exe installers need to be built on their respective platforms
- Icon files not yet included (will use system defaults)

### 🚀 Coming Next

- Phase 13.6: Friend trial distribution with staging deployment
- Phase 13: Payment integration (Stripe/Alipay/WeChat Pay)
- System tray integration for desktop app
- Global hotkey support

### 📝 Full Changelog

See [CHANGELOG.md](CHANGELOG.md) for detailed changes.

---

**Note:** This is a development release. Some features may require platform-specific testing. Please report any issues on GitHub.
