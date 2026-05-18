# Standalone Build (PyInstaller)

Starting from v0.1.21, Auctus Agent can be distributed as a **standalone package** that requires no Python installation on the user's machine. Everything - Python runtime, all dependencies, and the app - is bundled into a single folder.

## What It Is

The standalone build uses [PyInstaller](https://pyinstaller.org/) to bundle:

- Python interpreter (ARM64 on Apple Silicon, x64 on Windows)
- All pip dependencies (FastAPI, uvicorn, litellm, anthropic SDK, etc.)
- The `app/` source code
- Static files (`app/ui.html`, `prompts/system.md`)
- Data files required by third-party libraries (litellm JSON files, tiktoken encoding cache, tiktoken namespace plugins)

The user receives a zip. They unzip it and double-click one file. No setup wizard, no Python, no terminal.

## Output Structure

```
Auctus-Agent-mac-v0.1.22/
├── Launch Auctus Agent.command   <- only file the user needs to touch
└── _app/                         <- internal, do not distribute separately
    ├── AuctusAgent               <- PyInstaller binary
    ├── _internal/                <- bundled Python runtime + libraries
    │   ├── tiktoken_cache/       <- bundled encoding cache for offline startup
    │   └── ...
    ├── prompts/
    │   └── system.md
    ├── .env.example
    ├── data/                     <- created on first run
    ├── inputs/                   <- created on first run
    ├── outputs/                  <- created on first run
    └── logs/                     <- created on first run
```

The launcher script handles first-run setup, starts the server with a loading indicator, writes startup output to `logs/server.log`, and opens the browser automatically.

## Prerequisites

You must build **on the same platform** you are targeting. PyInstaller does not support cross-compilation.

| Target | Build machine |
|--------|--------------|
| Mac (Apple Silicon) | macOS ARM64 |
| Mac (Intel) | macOS x86_64 |
| Windows | Windows 10/11 |

Before building, the project's `.venv` must exist:

```bash
bash scripts/install_mac.sh     # Mac
scripts\install_windows.ps1     # Windows
```

## Build Steps

### 1. Bump the version

Edit `app/version.py`:

```python
APP_VERSION = "0.1.24"   # increment from the previous build
```

### 2. Pre-download tiktoken cache

tiktoken downloads its BPE encoding file on first use. Run this before each standalone build so the file exists in the current build environment and can be bundled:

```bash
.venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base'); print('ok')"
```

The file is cached under Python's temp directory, in `data-gym-cache/` (for example `/var/folders/.../T/data-gym-cache/` on macOS, or `%TEMP%\data-gym-cache\` on Windows). The build script picks it up with `tempfile.gettempdir()`.

For `tiktoken==0.12.x`, `cl100k_base` is loaded through the `tiktoken_ext.openai_public` namespace module. The spec must include `collect_submodules("tiktoken_ext")`; otherwise the app may fail at launch with `ValueError: Unknown encoding cl100k_base`.

### 3. Run the build script

**Mac:**
```bash
bash scripts/build_pyinstaller_mac.sh
```

**Windows:**
```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_pyinstaller_win.ps1
```

Build takes 3–5 minutes. Output goes to `release/`:

```
release/
├── Auctus-Agent-mac-v0.1.22/                   <- folder (use for local testing)
└── Auctus-Agent-mac-standalone-v0.1.22.zip     <- zip (share this)
```

Old version folders are **never overwritten**. If the output folder already exists, the script exits with an error asking you to bump the version first.

## Key Files

| File | Purpose |
|------|---------|
| `auctus-agent.spec` | PyInstaller build configuration - declares what to bundle |
| `scripts/pyinstaller_entry.py` | Entry point - sets env vars, patches libraries, starts server |
| `scripts/build_pyinstaller_mac.sh` | Mac build + packaging script |
| `scripts/build_pyinstaller_win.ps1` | Windows build + packaging script |

## Known Issues and Fixes

### litellm data files (Python 3.9 + PyInstaller)

**Problem:** `litellm` uses `importlib.resources` to load JSON config files at import time. Python 3.9's `importlib.resources` is broken in PyInstaller frozen environments — it cannot resolve subpackage paths correctly and raises `FileNotFoundError` or `TypeError`.

**Fix - two-part:**
1. `auctus-agent.spec` uses `collect_data_files("litellm", includes=["**/*.json", ...])` to copy all JSON/YAML files into `_internal/litellm/`.
2. `pyinstaller_entry.py` patches `importlib.resources.open_text` and `open_binary` at startup to fall back to the PyInstaller bundle directories when the standard call fails.

### tiktoken encoding files

**Problem:** `tiktoken` loads `cl100k_base` through `tiktoken_ext.openai_public`, then reads a BPE encoding file from its cache. If the namespace plugin is missing, `get_encoding('cl100k_base')` fails with `ValueError: Unknown encoding cl100k_base`. If the cache path is wrong or empty, the frozen app tries to download `https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken` during startup, which breaks offline startup.

**Fix - three-part:**
1. `auctus-agent.spec` uses `collect_submodules("tiktoken_ext")` so PyInstaller includes `tiktoken_ext.openai_public`.
2. `auctus-agent.spec` bundles the cache files found in `tempfile.gettempdir()/data-gym-cache/` into `_internal/tiktoken_cache/`.
3. `pyinstaller_entry.py` sets `os.environ["TIKTOKEN_CACHE_DIR"]` before importing `litellm` and patches `tiktoken.load.read_file_cached` to read the bundled cache by URL hash before falling back to network access.

### Launch timeout

The Mac launcher writes stdout/stderr to `_app/logs/server.log`. If startup times out, the launcher prints the last 80 log lines so packaging issues are visible without manually rerunning the binary from Terminal.

### Telegram can send test messages but does not receive replies

**Problem:** The settings page can send a Telegram test message with `sendMessage`, but the Agent does not receive messages from Telegram. This can happen for two reasons:

1. The app was already running before `TELEGRAM_BOT_TOKEN` was saved, so the startup hook skipped the bot thread.
2. `python-telegram-bot` is running in a background thread and needs an asyncio event loop created in that thread before `Application.builder()` is called.

**Fix:**
1. `server.save_telegram_config()` starts the Telegram bot thread immediately after saving a valid token and allowlist.
2. `telegram_bot.run_in_thread()` creates and installs a new asyncio event loop inside the background thread before calling `run_polling(stop_signals=None)`.

## Troubleshooting

### "Hidden import not found" warnings during build

Warnings for `markdown`, `pysqlite2`, `MySQLdb`, `psycopg2` are safe to ignore — these packages are not installed or not needed at runtime.

### New missing file error at launch

If a library update adds a new data file that isn't bundled, you'll see a `FileNotFoundError` pointing to a path inside `_internal/`. Fix: add the affected package to `collect_data_files(...)` in `auctus-agent.spec` and rebuild.

### tiktoken cache not found at build time

If step 2 (pre-download) was skipped on the build machine, the cache file won't be bundled. Run the one-liner from step 2 and rebuild. For `cl100k_base`, the bundled cache file should be named `9b5ad71b2ce5302211f9c61530b329a4922fc6a4`.

## Distribution

Share the `.zip` file from `release/`. User instructions:

1. Unzip the file
2. Double-click `Launch Auctus Agent.command` (Mac) or `Launch Auctus Agent.bat` (Windows)
3. Wait for `Starting Auctus Agent...✓` — browser opens automatically
4. Complete the setup wizard on first run
