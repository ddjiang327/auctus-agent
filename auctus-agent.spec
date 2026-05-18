# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Auctus Agent
# Build: pyinstaller auctus-agent.spec --clean --noconfirm

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH)

# Collect all data files from litellm
litellm_datas = collect_data_files("litellm", includes=["**/*.json", "**/*.txt", "**/*.yaml", "**/*.yml"])
tiktoken_datas = collect_data_files("tiktoken", includes=["**/*.tiktoken", "**/*.json"])
tiktoken_ext_hiddenimports = collect_submodules("tiktoken_ext")
lark_oapi_hiddenimports = collect_submodules("lark_oapi")

# Bundle tiktoken encoding cache files (downloaded to /tmp/data-gym-cache on first use)
import hashlib, tempfile, os as _os
_tiktoken_cache_src = _os.path.join(tempfile.gettempdir(), "data-gym-cache")
tiktoken_cache_datas = [
    (_os.path.join(_tiktoken_cache_src, f), "tiktoken_cache")
    for f in (_os.listdir(_tiktoken_cache_src) if _os.path.exists(_tiktoken_cache_src) else [])
]

a = Analysis(
    [str(ROOT / "scripts" / "pyinstaller_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Static UI
        (str(ROOT / "app" / "ui.html"), "app"),
        # System prompt
        (str(ROOT / "prompts"), "prompts"),
        # Default config template
        (str(ROOT / ".env.example"), "."),
    ] + litellm_datas + tiktoken_datas + tiktoken_cache_datas,
    hiddenimports=[
        # App modules — ensure the full app package is bundled
        "app",
        "app.server",
        "app.agent",
        "app.tools",
        "app.config",
        "app.memory",
        "app.llm",
        "app.accounting",
        "app.cronjobs",
        "app.email_client",
        "app.evolution",
        "app.feishu_bot",
        "app.maintenance",
        "app.relay",
        "app.telegram_bot",
        "app.version",
        # uvicorn internals not auto-detected by PyInstaller
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "uvicorn.main",
        "uvicorn.config",
        # Email stdlib
        "email.mime.text",
        "email.mime.multipart",
        "email.mime.base",
        "imaplib",
        "smtplib",
        # Office / report generation
        "openpyxl",
        "openpyxl.cell._writer",
        # Markdown / document parsing
        "markdown",
        # HTTP / async
        "anyio",
        "anyio._backends._asyncio",
        "h11",
        "httpcore",
        "httpx",
        # QR code generation (mobile pairing page)
        "qrcode",
        "qrcode.image.svg",
        "qrcode.image.pure_pil",
        "qrcode.main",
        # PyWebView — native desktop window
        "webview",
        "webview.platforms.cocoa",           # macOS
        "webview.platforms.edgechromium",    # Windows (Edge WebView2)
        "webview.platforms.winforms",        # Windows fallback
        "webview.platforms.gtk",             # Linux
        # relay client
        "app.relay_client",
    ] + tiktoken_ext_hiddenimports + lark_oapi_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "notebook",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AuctusAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="app/icon.ico" if (ROOT / "app" / "icon.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AuctusAgent",
)
