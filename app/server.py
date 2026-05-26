"""FastAPI 入口：提供 /api/chat、静态文件、网页 UI。"""
from __future__ import annotations

import json
import mimetypes
import re
import sqlite3
import struct
import threading
import uuid
import zlib
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
import litellm
from pydantic import BaseModel

from . import accounting, agent, evidence, intent, memory, preferences, relay, routing, runtime_state, session_control, task_mode, tools
from .config import settings
from .version import API_COMPAT_VERSION, APP_NAME, APP_VERSION, RELEASE_CHANNEL

from contextlib import asynccontextmanager

def _ensure_device_token() -> None:
    """Generate and persist MOBILE_RELAY_DEVICE_TOKEN on first run."""
    if settings.mobile_relay_device_token:
        return
    import secrets
    from pathlib import Path
    token = secrets.token_urlsafe(32)
    env_path = Path(".env")
    if env_path.exists():
        existing = env_path.read_text(encoding="utf-8")
        if "MOBILE_RELAY_DEVICE_TOKEN=" in existing:
            updated = "\n".join(
                f"MOBILE_RELAY_DEVICE_TOKEN={token}" if line.startswith("MOBILE_RELAY_DEVICE_TOKEN=") else line
                for line in existing.splitlines()
            )
            env_path.write_text(updated, encoding="utf-8")
        else:
            env_path.write_text(existing.rstrip() + f"\nMOBILE_RELAY_DEVICE_TOKEN={token}\n", encoding="utf-8")
    settings.mobile_relay_device_token = token
    print(f"[Relay] Generated device token: {token[:8]}...")


def _relay_chat_handler(session_id: str, content: str) -> str:
    """Called by relay_client when a mobile message arrives. Reuses the /api/chat logic."""
    import urllib.request as _urlreq
    payload = json.dumps({"session_id": session_id, "message": content}).encode()
    req = _urlreq.Request(
        "http://127.0.0.1:8000/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with _urlreq.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read())
    return data.get("reply", "")


@asynccontextmanager
async def _lifespan(app):
    from . import telegram_bot as _tg_bot
    from . import feishu_bot as _feishu_bot
    from . import discord_bot as _dc_bot
    from . import cronjobs as _cronjobs
    from .relay_client import start_relay_client, stop_relay_client
    _ensure_runtime_dirs()
    accounting.init_db()
    accounting.restore_route_from_db()
    accounting.restore_model_from_db()
    _tg_bot.run_in_thread()
    _feishu_bot.run_in_thread()
    _dc_bot.run_in_thread()
    _ensure_device_token()
    start_relay_client(_relay_chat_handler)
    _cronjobs.start_scheduler()
    yield
    _cronjobs.stop_scheduler()
    stop_relay_client()

app = FastAPI(title="Auctus Agent", lifespan=_lifespan)
app.include_router(relay.router)

DEFAULT_PERMISSION_SCOPE = "full_computer"
DEFAULT_TERMINAL_ACCESS = "enabled"


def _ensure_runtime_dirs() -> None:
    for runtime_dir in (settings.data_dir, settings.output_dir, settings.workspace_dir, settings.logs_dir):
        runtime_dir.mkdir(parents=True, exist_ok=True)

# 输出目录公开下载（仅本地服务，不暴露公网）
app.mount("/files", StaticFiles(directory=str(settings.output_dir)), name="files")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    print(f"[server] unhandled error on {request.url.path}: {type(exc).__name__}: {exc}")
    if isinstance(exc, FileNotFoundError):
        missing = getattr(exc, "filename", "") or str(exc)
        return JSONResponse(
            status_code=500,
            content={"detail": f"本地文件或目录不存在，无法完成操作：{missing}"},
        )
    if isinstance(exc, sqlite3.OperationalError) and "unable to open database file" in str(exc).lower():
        db_file = accounting.db_path()
        return JSONResponse(
            status_code=500,
            content={"detail": f"本地数据库打不开：{db_file}。请把 Auctus Agent 解压到桌面或 Applications 后重新启动，不要从 Trash 或 zip 预览里运行。"},
        )
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


# ---------- PWA 支持 ----------

def _make_png(size: int) -> bytes:
    """Generate a minimal PNG icon in dark navy + blue orb style (pure stdlib)."""
    bg = (7, 10, 18)
    ring = (45, 124, 255)
    glow = (0, 245, 255)
    cx = cy = size // 2
    r_outer = int(size * 0.42)
    r_inner = int(size * 0.28)
    r_dot = int(size * 0.08)

    rows = bytearray()
    for y in range(size):
        rows.append(0)  # PNG filter byte: None
        for x in range(size):
            dx = x - cx
            dy = y - cy
            d = (dx * dx + dy * dy) ** 0.5
            # glow dot center
            if d <= r_dot:
                t = 1.0 - d / max(r_dot, 1)
                px = (
                    int(glow[0] + t * (255 - glow[0])),
                    int(glow[1] + t * (255 - glow[1])),
                    int(glow[2] + t * (255 - glow[2])),
                    255,
                )
            # blue ring
            elif r_inner <= d <= r_outer:
                band = r_outer - r_inner
                t = 1.0 - abs(d - (r_inner + band / 2)) / (band / 2)
                t = max(0.0, t)
                px = (
                    int(bg[0] + t * (ring[0] - bg[0])),
                    int(bg[1] + t * (ring[1] - bg[1])),
                    int(bg[2] + t * (ring[2] - bg[2])),
                    255,
                )
            else:
                px = (*bg, 255)
            rows.extend(px)

    compressed = zlib.compress(bytes(rows), 6)

    def _chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    idat = _chunk(b"IDAT", compressed)
    iend = _chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


_ICON_192 = _make_png(192)
_ICON_512 = _make_png(512)

_SW_JS = """\
self.addEventListener('install', e => {
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.map(k => caches.delete(k))))
      .then(() => self.registration.unregister())
  );
  self.clients.claim();
});
"""

_MANIFEST = json.dumps({
    "name": "Auctus Agent",
    "short_name": "Auctus",
    "description": "你的私人 AI 秘书",
    "start_url": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#070A12",
    "theme_color": "#0B1020",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
    ],
    "categories": ["productivity", "utilities"],
}, ensure_ascii=False)


@app.get("/manifest.json")
def pwa_manifest():
    return Response(content=_MANIFEST, media_type="application/manifest+json")


@app.get("/sw.js")
def pwa_sw():
    return Response(content=_SW_JS, media_type="application/javascript",
                    headers={
                        "Service-Worker-Allowed": "/",
                        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                    })


@app.get("/icon-192.png")
def icon_192():
    return Response(content=_ICON_192, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=604800"})


@app.get("/icon-512.png")
def icon_512():
    return Response(content=_ICON_512, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=604800"})


@app.get("/api/version")
def version_info():
    """Return local app version and update metadata."""
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "channel": RELEASE_CHANNEL,
        "api_compat": API_COMPAT_VERSION,
        "update_check_url": settings.update_check_url,
        "download_url": settings.agent_download_url,
    }


@app.get("/api/version/check")
def version_check():
    """Return local version plus latest Agent release metadata when configured."""
    data = {
        "name": APP_NAME,
        "version": APP_VERSION,
        "channel": RELEASE_CHANNEL,
        "api_compat": API_COMPAT_VERSION,
        "latest_version": APP_VERSION,
        "update_available": False,
        "download_url": settings.agent_download_url,
        "update_check_url": settings.update_check_url,
        "source": "local",
    }
    if not settings.update_check_url:
        return data

    try:
        response = httpx.get(settings.update_check_url, timeout=5)
        response.raise_for_status()
        remote = response.json()
    except Exception as exc:
        data["source"] = "error"
        data["error"] = f"{type(exc).__name__}: {exc}"
        return data

    latest = str(remote.get("latest_agent_version") or remote.get("latest_version") or APP_VERSION)
    download_url = str(remote.get("agent_download_url") or remote.get("download_url") or settings.agent_download_url)
    data.update(
        {
            "latest_version": latest,
            "update_available": _is_newer_version(latest, APP_VERSION),
            "download_url": download_url,
            "source": "remote",
        }
    )
    return data


WEB_UI = (Path(__file__).parent / "ui.html").read_text(encoding="utf-8") if (Path(__file__).parent / "ui.html").exists() else """
<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Auctus Agent</title>
<style>
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f7f7f4;color:#222}
header{height:52px;display:flex;align-items:center;justify-content:space-between;padding:0 18px;border-bottom:1px solid #ddd;background:#fff}
main{height:calc(100vh - 53px);min-height:0}
.chat{height:100%;display:flex;flex-direction:column;min-width:0;min-height:0}
#messages{flex:1;min-height:0;overflow:auto;padding:18px;white-space:pre-wrap;scroll-behavior:smooth}
.msg{max-width:840px;margin:0 0 14px;padding:10px 12px;border:1px solid #ddd;background:#fff;border-radius:6px}
.user{background:#eef6ff;border-color:#cde4ff;margin-left:auto}
.agent{background:#fff}
.pending{color:#555;background:#fbfbfb}
.pending-main{display:block}
.pending-detail{display:block;margin-top:6px;color:#8a8a8a;font-size:12px;line-height:1.35}
.typing{display:inline-flex;gap:4px;margin-left:6px;vertical-align:middle}
.typing span{width:5px;height:5px;border-radius:50%;background:#777;display:inline-block;animation:typingPulse 1s infinite ease-in-out}
.typing span:nth-child(2){animation-delay:.15s}
.typing span:nth-child(3){animation-delay:.3s}
@keyframes typingPulse{0%,80%,100%{opacity:.3;transform:translateY(0)}40%{opacity:1;transform:translateY(-3px)}}
form{display:flex;gap:8px;padding:12px 18px;border-top:1px solid #ddd;background:#fff}
input,select,button{font:inherit}
select{color:#222;background:#fff;color-scheme:light}
select option{color:#222;background:#fff}
#message{flex:1;padding:10px;border:1px solid #bbb;border-radius:6px}
button{padding:9px 12px;border:1px solid #999;border-radius:6px;background:#fff;cursor:pointer}
button.primary{background:#1f6feb;color:white;border-color:#1f6feb}
button.icon{width:38px;height:38px;padding:0;display:inline-flex;align-items:center;justify-content:center;font-size:20px;line-height:1;border-radius:6px}
aside{position:fixed;top:0;right:0;width:min(420px,92vw);height:100vh;box-sizing:border-box;border-left:1px solid #ddd;background:#fff;overflow:auto;padding:14px;z-index:20;transform:translateX(100%);transition:transform .18s ease;box-shadow:-18px 0 50px rgba(0,0,0,.14)}
aside.open{transform:translateX(0)}
.settings-backdrop{position:fixed;inset:0;background:rgba(20,24,31,.28);display:none;z-index:19}
.settings-backdrop.open{display:block}
.settings-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid #e5e5e5}
.settings-head h1{font-size:18px;line-height:1.2;margin:0;letter-spacing:0}
section{margin-bottom:18px}
h2{font-size:14px;margin:0 0 8px;color:#555;text-transform:uppercase;letter-spacing:.04em}
.row{display:flex;gap:8px;align-items:center;margin-bottom:8px}
.row>*{min-width:0}
.stack{display:grid;gap:8px}
.item{border:1px solid #ddd;border-radius:6px;padding:8px;background:#fafafa}
.muted{color:#666;font-size:12px}
.file{display:block;margin-top:6px}
.overlay{position:fixed;inset:0;background:rgba(20,24,31,.38);display:none;align-items:center;justify-content:center;padding:18px;z-index:10}
.folder-overlay{position:fixed;inset:0;background:rgba(20,24,31,.38);display:none;align-items:center;justify-content:center;padding:18px;z-index:30}
.folder-dialog{width:min(720px,96vw);max-height:86vh;display:flex;flex-direction:column;background:#fff;border:1px solid #d4d4d4;border-radius:8px;box-shadow:0 24px 80px rgba(0,0,0,.22)}
.folder-head{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:14px;border-bottom:1px solid #e3e3e3}
.folder-head h1{font-size:18px;line-height:1.2;margin:0;letter-spacing:0}
.folder-path{padding:10px 14px;border-bottom:1px solid #eee;font-size:12px;color:#666;word-break:break-all}
.folder-list{overflow:auto;padding:8px;display:grid;gap:6px}
.folder-row{display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;text-align:left;border:1px solid #e1e1e1;border-radius:6px;background:#fff;padding:9px 10px}
.folder-row:hover{background:#f6f8fa}
.folder-actions{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 14px;border-top:1px solid #e3e3e3}
.wizard{width:min(760px,100%);max-height:92vh;overflow:auto;background:#fff;border:1px solid #d4d4d4;border-radius:8px;box-shadow:0 24px 80px rgba(0,0,0,.22)}
.wizard header{height:auto;display:block;padding:18px;border-bottom:1px solid #e3e3e3}
.wizard h1{font-size:22px;line-height:1.2;margin:0 0 6px;letter-spacing:0}
.wizard form{display:block;padding:18px;border:0}
.choice-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:12px 0 16px}
.choice{border:1px solid #ccc;border-radius:8px;padding:12px;background:#fafafa;cursor:pointer}
.choice input{margin-right:6px}
.choice strong{display:block;margin-bottom:4px}
.choice span{display:block;font-size:12px;color:#666}
.field{display:grid;gap:6px;margin-bottom:12px}
.field label{font-size:13px;color:#555;font-weight:600}
.field input,.field textarea,.field select{padding:9px;border:1px solid #bbb;border-radius:6px}
.field textarea{min-height:76px;resize:vertical}
.wizard-panel{display:none;border:1px solid #e0e0e0;border-radius:8px;padding:12px;margin-bottom:12px;background:#fbfbfb}
.wizard-panel.active{display:block}
.wizard-actions{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:14px}
.error{color:#9d1c1c;font-size:13px}
@media(max-width:860px){main{height:calc(100vh - 53px)}.chat{height:100%}}
@media(max-width:720px){.choice-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <strong>Auctus Agent</strong>
  <div style="display:flex;align-items:center;gap:10px">
    <div id="mobileStatusIndicator" style="display:flex;align-items:center;gap:6px;font-size:13px"></div>
    <button id="openSettings" class="icon" type="button" title="设置" aria-label="设置">⚙</button>
  </div>
</header>
<main>
  <div class="chat">
    <div id="messages"></div>
    <div id="tgInboxBanner" style="display:none;padding:6px 12px;background:#e8f4fd;border-top:1px solid #bee3f8;font-size:12px;color:#2c5282">📱 <span id="tgInboxText"></span></div>
    <form id="chatForm">
      <input id="message" autocomplete="off" placeholder="输入任务，例如：读取 test_prd.md 并生成总结">
      <button class="primary">发送</button>
    </form>
  </div>
</main>
<div id="settingsBackdrop" class="settings-backdrop"></div>
<aside id="settingsPanel" aria-label="设置">
    <div class="settings-head">
      <h1>设置</h1>
      <button id="closeSettings" class="icon" type="button" title="关闭" aria-label="关闭">×</button>
    </div>
    <section>
      <h2>模型</h2>
      <div class="row">
        <select id="model"></select>
        <button id="saveModel">切换</button>
        <button id="openSetup" type="button">重新设置</button>
      </div>
      <div class="row" style="align-items:center;gap:8px;margin-top:4px">
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px">
          <input type="checkbox" id="autoModelToggle" style="width:14px;height:14px">
          自动选模型（按任务类型）
        </label>
      </div>
      <div class="row">
        <select id="systemLanguage">
          <option value="zh">中文</option>
          <option value="en">English</option>
        </select>
        <button id="saveLanguage" type="button">保存语言</button>
      </div>
      <div class="row">
        <select id="hostedRegion">
          <option value="auto">自动选择区域</option>
          <option value="global">海外 Vercel</option>
          <option value="cn">国内阿里云</option>
        </select>
        <button id="saveHostedRegion" type="button">保存区域</button>
      </div>
      <div id="languageStatus" class="muted"></div>
      <div id="hostedRegionStatus" class="muted"></div>
      <div class="row">
        <select id="permissionScope">
          <option value="workspace">仅授权文件夹</option>
          <option value="full_computer">整台电脑</option>
        </select>
        <button id="savePermissionScope" type="button">保存权限</button>
      </div>
      <div id="permissionScopeStatus" class="muted"></div>
      <div class="row">
        <select id="terminalAccess">
          <option value="disabled">关闭终端命令</option>
          <option value="enabled">允许终端命令</option>
        </select>
        <button id="saveTerminalAccess" type="button">保存终端权限</button>
      </div>
      <div id="terminalAccessStatus" class="muted"></div>
    </section>
    <section>
      <h2>模型路由</h2>
      <div class="row">
        <select id="route"></select>
        <button id="saveRoute">保存</button>
      </div>
      <div class="row">
        <select id="provider"></select>
        <input id="apiKey" type="password" placeholder="BYO API key" style="flex:1;padding:8px;border:1px solid #bbb;border-radius:6px">
        <button id="saveKey">保存 Key</button>
      </div>
      <div id="keyStatus" class="muted"></div>
	    </section>
	    <section>
	      <h2>文件权限</h2>
	      <div class="muted" style="margin-bottom:8px">建议授权一个专门的工作文件夹，例如 Documents/Auctus Workspace。不要直接授权软件所在目录。</div>
	      <div class="row">
	        <input id="workspacePath" placeholder="授权文件夹路径" style="flex:1;padding:8px;border:1px solid #bbb;border-radius:6px">
	        <button id="openFolderPicker" type="button">选择</button>
	        <button id="saveWorkspace">授权</button>
	      </div>
	      <div id="workspaceStatus" class="muted"></div>
	    </section>
	    <section>
	      <h2>文件</h2>
      <div class="row">
        <input id="file" type="file">
        <button id="upload">上传</button>
      </div>
      <div id="uploadStatus" class="muted"></div>
    </section>
    <section>
      <h2>候选记忆</h2>
      <div id="memories" class="stack"></div>
    </section>
    <section>
      <h2>工具日志</h2>
      <button id="refreshLogs">刷新</button>
      <div id="logs" class="stack" style="margin-top:8px"></div>
    </section>
    <section id="mobileAppSection">
      <h2>Auctus Mobile App</h2>
      <div id="mobileSettingsStatus" class="muted" style="margin-bottom:8px"></div>
      <div class="row">
        <button id="openPairingPage" type="button">扫码连接手机</button>
      </div>
    </section>
    <section id="communicationSection">
      <h2>通信连接</h2>
      <div class="field">
        <label for="commProvider">渠道</label>
        <select id="commProvider">
          <option value="telegram">Telegram</option>
          <option value="feishu">飞书</option>
          <option value="lark">Lark</option>
        </select>
      </div>
      <div id="commPanelTelegram" data-provider-panel="telegram">
        <div id="tgConfiguredInfo" style="display:none;padding:8px;background:#f0faf0;border-radius:6px;margin-bottom:10px;font-size:13px"></div>
        <div class="field">
          <label for="tgBotToken">Bot Token</label>
          <input id="tgBotToken" type="password" placeholder="从 @BotFather 获取">
        </div>
        <div class="row">
          <button id="tgVerifyBtn" type="button">验证</button>
          <span id="tgVerifyStatus" class="muted"></span>
        </div>
        <div id="tgUserIdsBlock" style="margin-top:10px">
          <div class="field">
            <label for="tgUserIds">允许的用户 ID</label>
            <input id="tgUserIds" placeholder="多个用逗号分隔">
          </div>
          <div class="row">
            <button id="tgFetchIdsBtn" type="button">自动获取</button>
            <span id="tgFetchStatus" class="muted"></span>
          </div>
        </div>
        <div class="row" style="margin-top:10px">
          <button id="tgSaveBtn" type="button">保存并启用</button>
          <button id="tgTestBtn" type="button">发送测试消息</button>
        </div>
        <div id="tgSaveStatus" class="muted" style="margin-top:6px"></div>
      </div>
      <div id="commPanelFeishu" data-provider-panel="feishu" style="display:none">
        <div id="feishuConfiguredInfo" style="display:none;padding:8px;background:#f0faf0;border-radius:6px;margin-bottom:10px;font-size:13px"></div>
        <div class="muted" style="margin-bottom:8px">
          WebSocket 长连接模式：Auctus 会主动连接飞书开放平台，不需要公网地址。请在飞书开放平台的事件订阅中选择「使用长连接接收事件」，订阅 im.message.receive_v1，并开通接收/发送消息权限。
        </div>
        <div class="field">
          <label for="feishuAppId">App ID</label>
          <input id="feishuAppId" placeholder="cli_xxxxxxxxxxxxxxxx">
        </div>
        <div class="field">
          <label for="feishuAppSecret">App Secret</label>
          <input id="feishuAppSecret" type="password" placeholder="飞书开放平台 App Secret">
        </div>
        <div class="field">
          <label for="feishuVerifyToken">Verification Token</label>
          <input id="feishuVerifyToken" placeholder="仅 Webhook 模式需要，可留空">
        </div>
        <div class="muted" style="margin-bottom:8px">当前接收模式：WebSocket 长连接。Webhook 地址仅作为高级备用：<code id="feishuWebhookUrl">http://127.0.0.1:8000/webhook/feishu</code></div>
        <div class="row">
          <button id="feishuSaveBtn" type="button">保存并启动长连接</button>
          <span id="feishuSaveStatus" class="muted"></span>
        </div>
      </div>
      <div id="commPanelLark" data-provider-panel="lark" style="display:none">
        <div id="larkConfiguredInfo" style="display:none;padding:8px;background:#f0faf0;border-radius:6px;margin-bottom:10px;font-size:13px"></div>
        <div class="muted" style="margin-bottom:8px">Lark 使用 WebSocket 长连接模式，不需要公网地址。请在 Lark Developer Console 选择 Long Connection / WebSocket，订阅 im.message.receive_v1，并开通接收/发送消息权限。</div>
        <div class="field">
          <label for="larkAppId">App ID</label>
          <input id="larkAppId" placeholder="cli_xxxxxxxxxxxxxxxx">
        </div>
        <div class="field">
          <label for="larkAppSecret">App Secret</label>
          <input id="larkAppSecret" type="password" placeholder="Lark App Secret">
        </div>
        <div class="row">
          <button id="larkSaveBtn" type="button">保存并启动长连接</button>
          <span id="larkSaveStatus" class="muted"></span>
        </div>
      </div>
    </section>
  </aside>
<div id="onboardingOverlay" class="overlay">
  <div class="wizard">
    <header>
      <h1>首次设置</h1>
      <div class="muted">选择模型接入方式，之后可以在右侧设置里修改。</div>
    </header>
    <form id="onboardingForm">
      <div class="choice-grid">
        <label class="choice"><input type="radio" name="mode" value="own_api" checked><strong>自己的 API</strong><span>填写服务商 key，本地加密保存。</span></label>
        <label class="choice"><input type="radio" name="mode" value="hosted_api"><strong>Auctus 托管 API</strong><span>登录后使用余额和免费额度。</span></label>
        <label class="choice"><input type="radio" name="mode" value="local_model"><strong>本地模型</strong><span>使用 Ollama 或 LM Studio。</span></label>
      </div>
      <div class="field">
        <label for="setupModel">模型</label>
        <select id="setupModel"></select>
      </div>
      <div class="field">
        <label for="setupLanguage">系统语言</label>
        <select id="setupLanguage">
          <option value="zh">中文</option>
          <option value="en">English</option>
        </select>
      </div>
      <div id="ownApiPanel" class="wizard-panel active">
        <div class="field">
          <label for="setupProvider">服务商</label>
          <select id="setupProvider"></select>
        </div>
        <div class="field">
          <label for="setupApiKey">API key</label>
          <input id="setupApiKey" type="password" autocomplete="off" placeholder="已内置试用 Key，可留空">
        </div>
        <div class="row">
          <button id="verifySetupKey" type="button">验证 Key</button>
          <div id="setupKeyStatus" class="muted"></div>
        </div>
      </div>
      <div id="hostedApiPanel" class="wizard-panel">
        <div class="field">
          <label for="setupEmail">登录邮箱</label>
          <input id="setupEmail" type="email" autocomplete="email" placeholder="you@example.com">
        </div>
        <div class="field">
          <label for="setupHostedPassword">密码</label>
          <input id="setupHostedPassword" type="password" autocomplete="current-password" placeholder="账号密码">
        </div>
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px">
          <button id="hostedLoginBtn" type="button">登录</button>
          <span id="hostedLoginStatus" class="muted"></span>
        </div>
        <div id="hostedAccountInfo" style="display:none;padding:10px;background:#f0faf0;border:1px solid #c3e6cb;border-radius:6px;margin-bottom:12px;font-size:13px"></div>
        <div style="display:none">
          <select id="setupHostedRegion">
            <option value="auto">自动选择区域</option>
            <option value="global">海外 Vercel</option>
            <option value="cn">国内阿里云</option>
          </select>
        </div>
        <div class="muted">登录后自动获取 API Key，调用费用从账号余额中扣除。</div>
      </div>
      <div id="localModelPanel" class="wizard-panel">
        <div class="muted">本地模型不会走云端计费。请确保本机模型服务已启动，并在模型框中填写对应模型名。</div>
      </div>
      <div class="field">
        <label for="agentName">给 Agent 起个名字</label>
        <input id="agentName" placeholder="可留空">
      </div>
      <div class="field">
        <label for="persona">AI 性格</label>
        <select id="persona">
          <option value="professional">专业简洁</option>
          <option value="cool_sister">高冷御姐</option>
          <option value="warm_uncle">知心大叔</option>
          <option value="reliable_bro">可靠小哥</option>
          <option value="cheerful_girl">元气萌妹</option>
        </select>
      </div>
      <div class="field">
        <label for="userIntro">简短自我介绍</label>
        <textarea id="userIntro" placeholder="可选，例如你的工作、偏好、常用语言或项目背景"></textarea>
      </div>
      <div class="field">
        <label for="setupWorkspacePath">授权工作文件夹</label>
        <div class="row" style="margin:0">
          <input id="setupWorkspacePath" placeholder="建议新建一个专门文件夹，例如 ~/Documents/Auctus Workspace" style="flex:1">
          <button id="setupOpenFolderPicker" type="button">选择</button>
        </div>
        <div class="muted">Agent 只能在这个文件夹里读写文件。建议不要用软件安装目录；整机和 Terminal 权限需要以后单独加更严格的开关。</div>
      </div>
      <div class="field">
        <label for="setupPermissionScope">文件权限范围</label>
        <select id="setupPermissionScope">
          <option value="workspace">仅授权文件夹</option>
          <option value="full_computer">整台电脑</option>
        </select>
        <div class="muted">整台电脑权限会允许 Agent 读取/写入任意路径内支持的文本文件。请只在你确定需要时启用。</div>
      </div>
      <div class="field">
        <label for="setupTerminalAccess">终端命令权限</label>
        <select id="setupTerminalAccess">
          <option value="disabled">关闭终端命令</option>
          <option value="enabled">允许终端命令</option>
        </select>
        <div class="muted">开启后，Agent 只有在你当前消息明确要求执行命令时才会运行终端命令。</div>
      </div>
	        <label class="row"><input id="saveProfile" type="checkbox"> <span>把名字和自我介绍直接写入长期记忆</span></label>
      <div class="wizard-actions">
        <div id="setupError" class="error"></div>
        <button class="primary" type="submit">完成设置</button>
      </div>
    </form>
  </div>
</div>
<div id="folderOverlay" class="folder-overlay">
  <div class="folder-dialog">
    <div class="folder-head">
      <h1>选择工作文件夹</h1>
      <button id="closeFolderPicker" class="icon" type="button" title="关闭" aria-label="关闭">×</button>
    </div>
    <div id="folderPath" class="folder-path"></div>
    <div id="folderList" class="folder-list"></div>
    <div class="folder-actions">
      <button id="folderUp" type="button">上一级</button>
      <button id="chooseFolder" class="primary" type="button">授权此文件夹</button>
    </div>
  </div>
</div>
<script>
const sid = crypto.randomUUID();
const messages = document.getElementById('messages');
const overlay = document.getElementById('onboardingOverlay');
const settingsPanel = document.getElementById('settingsPanel');
const settingsBackdrop = document.getElementById('settingsBackdrop');
const folderOverlay = document.getElementById('folderOverlay');
let folderPickerTarget = 'workspacePath';
let currentFolderPath = '';
let currentLanguage = 'zh';
let _hostedApiKey = null;
const UI = {
  zh: {
    settings: '设置',
    close: '关闭',
    model: '模型',
    switchModel: '切换',
    setupAgain: '重新设置',
    saveLanguage: '保存语言',
    languageStatus: '当前语言：中文',
    hostedRegion: '服务区域',
    saveRegion: '保存区域',
    hostedRegionStatus: label => `当前托管 API 区域：${label}`,
    regionSaved: label => `托管 API 服务区域已保存：${label}`,
    permissionScope: '文件权限范围',
    savePermission: '保存权限',
    permissionWorkspace: '仅授权文件夹',
    permissionFull: '整台电脑',
    permissionStatus: label => `当前文件权限：${label}`,
    permissionSaved: label => `文件权限已保存：${label}`,
    terminalAccess: '终端命令权限',
    saveTerminal: '保存终端权限',
    terminalDisabled: '关闭终端命令',
    terminalEnabled: '允许终端命令',
    terminalStatus: label => `当前终端权限：${label}`,
    terminalSaved: label => `终端权限已保存：${label}`,
    terminalRequest: '这个任务需要执行终端命令。是否授权？',
    terminalOnce: '仅本次',
    terminalAlways: '始终允许',
    terminalNo: '拒绝',
    route: '模型路由',
    save: '保存',
    saveKey: '保存 Key',
    verifyKey: '验证 Key',
    verifyingKey: '正在验证...',
    keyValid: '验证成功',
    keyInvalid: '验证失败：',
    noKey: '暂无 BYO key',
    filePermission: '文件权限',
    workspaceHint: '建议授权一个专门的工作文件夹，例如 Documents/Auctus Workspace。不要直接授权软件所在目录。',
    workspacePlaceholder: '授权文件夹路径',
    choose: '选择',
    authorize: '授权',
    files: '文件',
    upload: '上传',
    memories: '候选记忆',
    logs: '工具日志',
    refresh: '刷新',
    messagePlaceholder: '输入任务，例如：读取 test_prd.md 并生成总结',
    send: '发送',
    pending: '问题已收到，正在处理',
    progressSteps: [
      '已收到请求',
      '正在联系模型',
      '正在判断是否需要工具',
      '可能正在读取网页、文件或记忆',
      '正在整理结果',
      '任务较复杂，请再等一下'
    ],
    requestFailed: '请求失败：',
    uploaded: '已上传：',
    noMemories: '暂无候选记忆',
    confirm: '确认',
    reject: '拒绝',
    keySaved: 'API key 已保存，路由已切换到 byo。现在可以再发一条消息测试。',
    workspaceCurrent: '当前授权：',
    workspaceUnset: '未设置',
    workspaceWarning: ' 建议改成软件目录外的专门工作文件夹。',
    workspaceSaved: '文件夹已授权。之后我只能在这个目录里读写文件。',
    firstSetup: '首次设置',
    setupDesc: '选择模型接入方式，之后可以在右侧设置里修改。',
    ownApi: '自己的 API',
    ownApiDesc: '填写服务商 key，本地加密保存。',
    hostedApi: 'Auctus 托管 API',
    hostedApiDesc: '登录后使用余额和免费额度。',
    regionAuto: '自动选择区域',
    regionGlobal: '海外 Vercel',
    regionCn: '国内阿里云',
    localModel: '本地模型',
    localModelDesc: '使用 Ollama 或 LM Studio。',
    systemLanguage: '系统语言',
    provider: '服务商',
    loginEmail: '登录邮箱',
    hostedPassword: '密码',
    hostedLoginBtn: '登录',
    hostedLoginSuccess: (email, balance) => `✓ 登录成功：${email}，余额 ${balance}`,
    hostedLoginRequired: '请先点击"登录"按钮完成登录',
    hostedHint: '登录后自动获取 API Key，调用费用从账号余额中扣除。',
    localHint: '本地模型不会走云端计费。请确保本机模型服务已启动，并在模型框中填写对应模型名。',
    agentName: '给 Agent 起个名字',
    agentNamePlaceholder: '可留空',
    persona: 'AI 性格',
    userIntro: '简短自我介绍',
    userIntroPlaceholder: '可选，例如你的工作、偏好、常用语言或项目背景',
    setupWorkspace: '授权工作文件夹',
    setupWorkspacePlaceholder: '建议新建一个专门文件夹，例如 ~/Documents/Auctus Workspace',
    workspaceHelp: '默认情况下 Agent 只能在这个文件夹里读写文件。建议不要用软件安装目录。',
    permissionHelp: '整台电脑权限会允许 Agent 读取/写入任意路径内支持的文本文件。请只在你确定需要时启用。',
    terminalHelp: '开启后，Agent 只有在你当前消息明确要求执行命令时才会运行终端命令。',
    saveProfile: '把名字和自我介绍直接写入长期记忆',
    finishSetup: '完成设置',
    folderTitle: '选择工作文件夹',
    folderUp: '上一级',
    chooseFolder: '授权此文件夹',
    folderEmpty: '这个文件夹下没有可显示的子文件夹。',
    unreadable: '不可读',
    helloAgent: name => `你好，我是 ${name}。`,
    languageSaved: '系统语言已保存。之后我默认用中文回复。',
    setupDone: '首次设置已完成。你现在可以发第一条任务。',
    hostedPreview: '首次设置已完成。当前没有配置云端 Relay，本地预览会使用 .env 里的平台模型 key；如果仍然认证失败，请点“重新设置”选择“自己的 API”。',
    profileSaved: '知道了，名字和自我介绍已记住。'
  },
  en: {
    settings: 'Settings',
    close: 'Close',
    model: 'Model',
    switchModel: 'Switch',
    setupAgain: 'Setup',
    saveLanguage: 'Save Language',
    languageStatus: 'Current language: English',
    hostedRegion: 'Service Region',
    saveRegion: 'Save Region',
    hostedRegionStatus: label => `Hosted API region: ${label}`,
    regionSaved: label => `Hosted API service region saved: ${label}`,
    permissionScope: 'File Permission Scope',
    savePermission: 'Save Permission',
    permissionWorkspace: 'Authorized Folder Only',
    permissionFull: 'Whole Computer',
    permissionStatus: label => `Current file permission: ${label}`,
    permissionSaved: label => `File permission saved: ${label}`,
    terminalAccess: 'Terminal Command Access',
    saveTerminal: 'Save Terminal Access',
    terminalDisabled: 'Disable Terminal Commands',
    terminalEnabled: 'Allow Terminal Commands',
    terminalStatus: label => `Current terminal access: ${label}`,
    terminalSaved: label => `Terminal access saved: ${label}`,
    terminalRequest: 'This task needs to run a terminal command. Allow it?',
    terminalOnce: 'This Task',
    terminalAlways: 'Always Allow',
    terminalNo: 'No',
    route: 'Model Route',
    save: 'Save',
    saveKey: 'Save Key',
    verifyKey: 'Verify Key',
    verifyingKey: 'Verifying...',
    keyValid: 'Verification successful',
    keyInvalid: 'Verification failed: ',
    noKey: 'No BYO key',
    filePermission: 'File Permission',
    workspaceHint: 'Choose a dedicated workspace folder, such as Documents/Auctus Workspace. Avoid authorizing the app folder itself.',
    workspacePlaceholder: 'Authorized folder path',
    choose: 'Choose',
    authorize: 'Authorize',
    files: 'Files',
    upload: 'Upload',
    memories: 'Memory Candidates',
    logs: 'Tool Logs',
    refresh: 'Refresh',
    messagePlaceholder: 'Type a task, e.g. read test_prd.md and summarize it',
    send: 'Send',
    pending: 'Question received. Working on it',
    progressSteps: [
      'Request received',
      'Contacting the model',
      'Checking whether tools are needed',
      'May be reading webpages, files, or memory',
      'Organizing the result',
      'This is taking longer than usual'
    ],
    requestFailed: 'Request failed: ',
    uploaded: 'Uploaded: ',
    noMemories: 'No candidate memories',
    confirm: 'Confirm',
    reject: 'Reject',
    keySaved: 'API key saved. Route switched to byo. Send another message to test it.',
    workspaceCurrent: 'Authorized folder: ',
    workspaceUnset: 'Not set',
    workspaceWarning: ' Consider using a dedicated workspace outside the app folder.',
    workspaceSaved: 'Folder authorized. I can read and write files only inside this folder.',
    firstSetup: 'First Setup',
    setupDesc: 'Choose how to connect a model. You can change this later in Settings.',
    ownApi: 'Own API',
    ownApiDesc: 'Paste a provider key. It is encrypted locally.',
    hostedApi: 'Auctus Hosted API',
    hostedApiDesc: 'Sign in to use balance and free quota.',
    regionAuto: 'Auto Select',
    regionGlobal: 'Global Vercel',
    regionCn: 'China Aliyun',
    localModel: 'Local Model',
    localModelDesc: 'Use Ollama or LM Studio.',
    systemLanguage: 'System Language',
    provider: 'Provider',
    loginEmail: 'Login Email',
    hostedPassword: 'Password',
    hostedLoginBtn: 'Sign In',
    hostedLoginSuccess: (email, balance) => `✓ Signed in: ${email}, balance ${balance}`,
    hostedLoginRequired: 'Please sign in first before finishing setup',
    hostedHint: 'Sign in to get an API Key automatically. Usage is billed from your account balance.',
    localHint: 'Local models do not use cloud billing. Make sure your local model server is running and enter the matching model name.',
    agentName: 'Name Your Agent',
    agentNamePlaceholder: 'Optional',
    persona: 'AI Personality',
    userIntro: 'Short Self Introduction',
    userIntroPlaceholder: 'Optional, such as your work, preferences, usual language, or project background',
    setupWorkspace: 'Authorize Workspace Folder',
    setupWorkspacePlaceholder: 'Create a dedicated folder, e.g. ~/Documents/Auctus Workspace',
    workspaceHelp: 'By default, the Agent can read and write files only inside this folder. Avoid the app install folder.',
    permissionHelp: 'Whole-computer permission allows the Agent to read/write supported text files under any path. Enable it only when you truly need it.',
    terminalHelp: 'When enabled, the Agent runs terminal commands only if your current message explicitly asks for it.',
    saveProfile: 'Save the name and introduction to long-term memory',
    finishSetup: 'Finish Setup',
    folderTitle: 'Choose Workspace Folder',
    folderUp: 'Up One Level',
    chooseFolder: 'Authorize This Folder',
    folderEmpty: 'No visible subfolders in this folder.',
    unreadable: 'Unreadable',
    helloAgent: name => `Hi, I am ${name}.`,
    languageSaved: 'System language saved. I will reply in English by default.',
    setupDone: 'First setup is complete. You can send your first task now.',
    hostedPreview: 'First setup is complete. No cloud Relay is configured, so this local preview will use the platform model key from .env. If authentication still fails, open Setup and choose Own API.',
    profileSaved: 'Got it. The name and self introduction have been saved.'
  }
};
const PERSONA_LABELS = {
  zh: {
    professional: '专业简洁',
    cool_sister: '高冷御姐',
    warm_uncle: '知心大叔',
    reliable_bro: '可靠小哥',
    cheerful_girl: '元气萌妹'
  },
  en: {
    professional: 'Professional',
    cool_sister: 'Cool Big Sister',
    warm_uncle: 'Warm Uncle',
    reliable_bro: 'Reliable Bro',
    cheerful_girl: 'Cheerful Girl'
  }
};
function t(key) {
  return (UI[currentLanguage] && UI[currentLanguage][key]) || UI.zh[key] || key;
}
function regionLabel(region) {
  const labels = {
    auto: t('regionAuto'),
    global: t('regionGlobal'),
    cn: t('regionCn')
  };
  return labels[region] || labels.auto;
}
function permissionLabel(scope) {
  const labels = {
    workspace: t('permissionWorkspace'),
    full_computer: t('permissionFull')
  };
  return labels[scope] || labels.workspace;
}
function terminalLabel(access) {
  const labels = {
    disabled: t('terminalDisabled'),
    enabled: t('terminalEnabled')
  };
  return labels[access] || labels.disabled;
}
function setText(el, value) {
  if (el) el.textContent = value;
}
function setPlaceholder(id, value) {
  const el = document.getElementById(id);
  if (el) el.placeholder = value;
}
function updatePersonaLabels() {
  const labels = PERSONA_LABELS[currentLanguage] || PERSONA_LABELS.zh;
  document.querySelectorAll('#persona option').forEach(opt => {
    opt.textContent = labels[opt.value] || opt.textContent;
  });
}
function updateRegionLabels() {
  ['hostedRegion', 'setupHostedRegion'].forEach(id => {
    const sel = document.getElementById(id);
    if (!sel) return;
    Array.from(sel.options).forEach(opt => {
      opt.textContent = regionLabel(opt.value);
    });
  });
}
function updatePermissionLabels() {
  ['permissionScope', 'setupPermissionScope'].forEach(id => {
    const sel = document.getElementById(id);
    if (!sel) return;
    Array.from(sel.options).forEach(opt => {
      opt.textContent = permissionLabel(opt.value);
    });
  });
}
function updateTerminalLabels() {
  ['terminalAccess', 'setupTerminalAccess'].forEach(id => {
    const sel = document.getElementById(id);
    if (!sel) return;
    Array.from(sel.options).forEach(opt => {
      opt.textContent = terminalLabel(opt.value);
    });
  });
}
function applyLanguage(language) {
  currentLanguage = language === 'en' ? 'en' : 'zh';
  document.documentElement.lang = currentLanguage;
  document.getElementById('systemLanguage').value = currentLanguage;
  document.getElementById('setupLanguage').value = currentLanguage;
  document.getElementById('openSettings').title = t('settings');
  document.getElementById('openSettings').setAttribute('aria-label', t('settings'));
  document.getElementById('closeSettings').title = t('close');
  document.getElementById('closeSettings').setAttribute('aria-label', t('close'));
  settingsPanel.setAttribute('aria-label', t('settings'));
  setPlaceholder('message', t('messagePlaceholder'));
  setText(document.querySelector('#chatForm button.primary'), t('send'));
  setPlaceholder('workspacePath', t('workspacePlaceholder'));
  setPlaceholder('agentName', t('agentNamePlaceholder'));
  setPlaceholder('userIntro', t('userIntroPlaceholder'));
  setPlaceholder('setupWorkspacePath', t('setupWorkspacePlaceholder'));

  setText(document.querySelector('.settings-head h1'), t('settings'));
  const sections = settingsPanel.querySelectorAll('section');
  setText(sections[0]?.querySelector('h2'), t('model'));
  setText(document.getElementById('saveModel'), t('switchModel'));
  setText(document.getElementById('openSetup'), t('setupAgain'));
  setText(document.getElementById('saveLanguage'), t('saveLanguage'));
  setText(document.getElementById('languageStatus'), t('languageStatus'));
  setText(document.getElementById('saveHostedRegion'), t('saveRegion'));
  const selectedRegion = document.getElementById('hostedRegion')?.value || 'auto';
  setText(document.getElementById('hostedRegionStatus'), t('hostedRegionStatus')(regionLabel(selectedRegion)));
  setText(document.getElementById('savePermissionScope'), t('savePermission'));
  const selectedPermission = document.getElementById('permissionScope')?.value || 'workspace';
  setText(document.getElementById('permissionScopeStatus'), t('permissionStatus')(permissionLabel(selectedPermission)));
  setText(document.getElementById('saveTerminalAccess'), t('saveTerminal'));
  const selectedTerminal = document.getElementById('terminalAccess')?.value || 'disabled';
  setText(document.getElementById('terminalAccessStatus'), t('terminalStatus')(terminalLabel(selectedTerminal)));
  setText(sections[1]?.querySelector('h2'), t('route'));
  setText(document.getElementById('saveRoute'), t('save'));
  setText(document.getElementById('saveKey'), t('saveKey'));
  setText(document.getElementById('verifySetupKey'), t('verifyKey'));
  setText(sections[2]?.querySelector('h2'), t('filePermission'));
  setText(sections[2]?.querySelector('.muted'), t('workspaceHint'));
  setText(document.getElementById('openFolderPicker'), t('choose'));
  setText(document.getElementById('saveWorkspace'), t('authorize'));
  setText(sections[3]?.querySelector('h2'), t('files'));
  setText(document.getElementById('upload'), t('upload'));
  setText(sections[4]?.querySelector('h2'), t('memories'));
  setText(sections[5]?.querySelector('h2'), t('logs'));
  setText(document.getElementById('refreshLogs'), t('refresh'));

  setText(document.querySelector('.wizard h1'), t('firstSetup'));
  setText(document.querySelector('.wizard header .muted'), t('setupDesc'));
  const choices = document.querySelectorAll('.choice');
  setText(choices[0]?.querySelector('strong'), t('ownApi'));
  setText(choices[0]?.querySelector('span'), t('ownApiDesc'));
  setText(choices[1]?.querySelector('strong'), t('hostedApi'));
  setText(choices[1]?.querySelector('span'), t('hostedApiDesc'));
  setText(choices[2]?.querySelector('strong'), t('localModel'));
  setText(choices[2]?.querySelector('span'), t('localModelDesc'));
  setText(document.querySelector('label[for="setupModel"]'), t('model'));
  setText(document.querySelector('label[for="setupLanguage"]'), t('systemLanguage'));
  setText(document.querySelector('label[for="setupHostedRegion"]'), t('hostedRegion'));
  setText(document.querySelector('label[for="setupProvider"]'), t('provider'));
  setText(document.querySelector('label[for="setupEmail"]'), t('loginEmail'));
  setText(document.querySelector('label[for="setupHostedPassword"]'), t('hostedPassword'));
  setText(document.getElementById('hostedLoginBtn'), t('hostedLoginBtn'));
  setText(document.querySelector('#hostedApiPanel .muted'), t('hostedHint'));
  setText(document.querySelector('#localModelPanel .muted'), t('localHint'));
  setText(document.querySelector('label[for="agentName"]'), t('agentName'));
  setText(document.querySelector('label[for="persona"]'), t('persona'));
  setText(document.querySelector('label[for="userIntro"]'), t('userIntro'));
  setText(document.querySelector('label[for="setupWorkspacePath"]'), t('setupWorkspace'));
  setText(document.getElementById('setupOpenFolderPicker'), t('choose'));
  setText(document.querySelector('#setupWorkspacePath')?.closest('.field')?.querySelector('.muted'), t('workspaceHelp'));
  setText(document.querySelector('label[for="setupPermissionScope"]'), t('permissionScope'));
  setText(document.querySelector('#setupPermissionScope')?.closest('.field')?.querySelector('.muted'), t('permissionHelp'));
  setText(document.querySelector('label[for="setupTerminalAccess"]'), t('terminalAccess'));
  setText(document.querySelector('#setupTerminalAccess')?.closest('.field')?.querySelector('.muted'), t('terminalHelp'));
  setText(document.getElementById('saveProfile')?.closest('label')?.querySelector('span'), t('saveProfile'));
  setText(document.querySelector('#onboardingForm button.primary'), t('finishSetup'));
  updatePersonaLabels();
  updateRegionLabels();
  updatePermissionLabels();
  updateTerminalLabels();

  setText(document.querySelector('.folder-head h1'), t('folderTitle'));
  document.getElementById('closeFolderPicker').title = t('close');
  document.getElementById('closeFolderPicker').setAttribute('aria-label', t('close'));
  setText(document.getElementById('folderUp'), t('folderUp'));
  setText(document.getElementById('chooseFolder'), t('chooseFolder'));
}
function openSettingsPanel() {
  settingsPanel.classList.add('open');
  settingsBackdrop.classList.add('open');
  loadAutoModel();
}
function closeSettingsPanel() {
  settingsPanel.classList.remove('open');
  settingsBackdrop.classList.remove('open');
}
async function openFolderPicker(targetId) {
  folderPickerTarget = targetId;
  folderOverlay.style.display = 'flex';
  await loadFolder('');
}
function closeFolderPicker() {
  folderOverlay.style.display = 'none';
}
async function loadFolder(path) {
  const url = path ? `/api/folders?path=${encodeURIComponent(path)}` : '/api/folders';
  const r = await fetch(url);
  const j = await r.json();
  if (!r.ok) {
    document.getElementById('folderPath').textContent = j.detail || JSON.stringify(j);
    document.getElementById('folderList').textContent = '';
    return;
  }
  currentFolderPath = j.path;
  document.getElementById('folderPath').textContent = j.path;
  const list = document.getElementById('folderList');
  list.textContent = '';
  (j.items || []).forEach(item => {
    const btn = document.createElement('button');
    btn.className = 'folder-row';
    btn.type = 'button';
    btn.innerHTML = `<span>📁 ${item.name}</span><span class="muted">${item.readable ? '' : t('unreadable')}</span>`;
    btn.onclick = () => loadFolder(item.path);
    list.appendChild(btn);
  });
  if (!j.items || !j.items.length) {
    const empty = document.createElement('div');
    empty.className = 'muted';
    empty.style.padding = '12px';
    empty.textContent = t('folderEmpty');
    list.appendChild(empty);
  }
}
function scrollChatToBottom() {
  requestAnimationFrame(() => {
    messages.scrollTop = messages.scrollHeight;
    setTimeout(() => { messages.scrollTop = messages.scrollHeight; }, 30);
  });
}
function addMessage(text, cls) {
  const el = document.createElement('div');
  el.className = `msg ${cls}`;
  el.textContent = text;
  messages.appendChild(el);
  scrollChatToBottom();
  return el;
}
function addPendingMessage() {
  const el = document.createElement('div');
  el.className = 'msg agent pending';
  const main = document.createElement('span');
  main.className = 'pending-main';
  const text = document.createElement('span');
  text.textContent = t('pending');
  const dots = document.createElement('span');
  dots.className = 'typing';
  dots.innerHTML = '<span></span><span></span><span></span>';
  main.append(text, dots);
  const detail = document.createElement('span');
  detail.className = 'pending-detail';
  const steps = t('progressSteps');
  const startedAt = Date.now();
  let stepIndex = 0;
  const updateDetail = () => {
    const elapsed = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
    detail.textContent = `${steps[Math.min(stepIndex, steps.length - 1)]} · ${elapsed}s`;
    if (stepIndex < steps.length - 1) stepIndex += 1;
    scrollChatToBottom();
  };
  updateDetail();
  el._progressTimer = setInterval(updateDetail, 3500);
  el.append(main, detail);
  messages.appendChild(el);
  scrollChatToBottom();
  return el;
}
function replaceMessage(el, text, cls='agent') {
  if (el._progressTimer) {
    clearInterval(el._progressTimer);
    el._progressTimer = null;
  }
  el.className = `msg ${cls}`;
  el.textContent = text;
  scrollChatToBottom();
}
function addFileLink(path) {
  const a = document.createElement('a');
  a.className = 'file';
  a.href = path;
  a.target = '_blank';
  a.textContent = path.split('/').pop();
  messages.appendChild(a);
  scrollChatToBottom();
}
async function sendChat(text, terminalPermission='') {
  const input = document.getElementById('message');
  const sendButton = document.querySelector('#chatForm button.primary');
  input.disabled = true;
  sendButton.disabled = true;
  const pending = addPendingMessage();
  try {
    const r = await fetch('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({session_id: sid, message: text, terminal_permission: terminalPermission})});
    const raw = await r.text();
    let j = {};
    try {
      j = raw ? JSON.parse(raw) : {};
    } catch {
      j = {detail: raw || r.statusText};
    }
    if (!r.ok) {
      replaceMessage(pending, j.detail || JSON.stringify(j));
      return;
    }
    if (j.permission_request && j.permission_request.type === 'terminal') {
      replaceMessage(pending, t('terminalRequest'));
      addTerminalPermissionButtons(text);
      return;
    }
    replaceMessage(pending, j.reply || JSON.stringify(j));
    (j.files || []).forEach(addFileLink);
    scrollChatToBottom();
    refreshLogs();
    refreshMemories();
  } catch (err) {
    replaceMessage(pending, `${t('requestFailed')}${err}`);
  } finally {
    input.disabled = false;
    sendButton.disabled = false;
    input.focus();
  }
}
document.getElementById('chatForm').onsubmit = async (e) => {
  e.preventDefault();
  const input = document.getElementById('message');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  addMessage(text, 'user');
  await sendChat(text);
};
function addTerminalPermissionButtons(text) {
  const el = document.createElement('div');
  el.className = 'msg agent';
  const row = document.createElement('div');
  row.className = 'row';
  const once = document.createElement('button');
  once.textContent = t('terminalOnce');
  once.onclick = async () => {
    el.remove();
    await sendChat(text, 'once');
  };
  const always = document.createElement('button');
  always.textContent = t('terminalAlways');
  always.onclick = async () => {
    await saveTerminalAccessValue('enabled', false);
    el.remove();
    await sendChat(text, 'always');
  };
  const no = document.createElement('button');
  no.textContent = t('terminalNo');
  no.onclick = () => el.remove();
  row.append(once, always, no);
  el.append(row);
  messages.appendChild(el);
  scrollChatToBottom();
}
document.getElementById('upload').onclick = async () => {
  const f = document.getElementById('file').files[0];
  if (!f) return;
  const body = await f.arrayBuffer();
  const r = await fetch(`/api/upload?filename=${encodeURIComponent(f.name)}`, {method:'POST', body});
  const j = await r.json();
  document.getElementById('uploadStatus').textContent = j.filename ? `${t('uploaded')}${j.filename}` : JSON.stringify(j);
};
async function refreshLogs() {
  const r = await fetch('/api/logs?tail=8');
  const j = await r.json();
  const box = document.getElementById('logs');
  box.textContent = '';
  (j.items || []).forEach(x => {
    const el = document.createElement('div');
    el.className = 'item';
    el.textContent = `${x.ts || ''} ${x.tool_name || ''} ${x.status || ''}`;
    box.appendChild(el);
  });
}
async function refreshMemories() {
  const r = await fetch('/api/memories?candidates=true');
  const j = await r.json();
  const box = document.getElementById('memories');
  box.textContent = '';
  if (!j.items || !j.items.length) {
    const empty = document.createElement('div');
    empty.className = 'muted';
    empty.textContent = t('noMemories');
    box.appendChild(empty);
    return;
  }
  j.items.forEach(m => {
    const el = document.createElement('div');
    el.className = 'item';
    const title = document.createElement('strong');
    title.textContent = `[${m.type}] ${m.title}`;
    const content = document.createElement('div');
    content.textContent = m.content;
    content.className = 'muted';
    const row = document.createElement('div');
    row.className = 'row';
    const ok = document.createElement('button');
    ok.textContent = t('confirm');
    ok.onclick = () => updateMemory(m.id, 'confirm');
    const no = document.createElement('button');
    no.textContent = t('reject');
    no.onclick = () => updateMemory(m.id, 'reject');
    row.append(ok, no);
    el.append(title, content, row);
    box.appendChild(el);
  });
}
async function updateMemory(id, action) {
  await fetch(`/api/memories/${id}/${action}`, {method:'POST'});
  refreshMemories();
}
async function loadModels() {
  const r = await fetch('/api/model');
  const j = await r.json();
  const sel = document.getElementById('model');
  const setupSel = document.getElementById('setupModel');
  sel.textContent = '';
  setupSel.textContent = '';
  (j.available || [j.model]).forEach(m => {
    const opt = document.createElement('option');
    opt.value = m; opt.textContent = m; opt.selected = m === j.model;
    sel.appendChild(opt);
    const setupOpt = opt.cloneNode(true);
    setupSel.appendChild(setupOpt);
  });
  syncProviderToModel();
}
document.getElementById('saveModel').onclick = async () => {
  const model = document.getElementById('model').value;
  await fetch('/api/model', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({model})});
  loadModels();
};
async function loadAutoModel() {
  const r = await fetch('/api/auto-model');
  const j = await r.json();
  document.getElementById('autoModelToggle').checked = j.enabled;
}
document.getElementById('autoModelToggle').onchange = async (e) => {
  await fetch('/api/auto-model', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({enabled: e.target.checked})});
};
document.getElementById('setupModel').onchange = syncProviderToModel;
document.getElementById('model').onchange = syncProviderToModel;
function syncProviderToModel() {
  const model = document.getElementById('setupModel')?.value || document.getElementById('model')?.value || '';
  const provider = model.includes('deepseek') ? 'deepseek' : (model.includes('gpt') ? 'openai' : (model.includes('claude') ? 'anthropic' : ''));
  if (!provider) return;
  ['provider', 'setupProvider'].forEach(id => {
    const el = document.getElementById(id);
    if (el && [...el.options].some(opt => opt.value === provider)) el.value = provider;
  });
}
async function loadLanguage() {
  const r = await fetch('/api/language');
  const j = await r.json();
  const language = j.language || 'zh';
  document.getElementById('systemLanguage').value = language;
  document.getElementById('setupLanguage').value = language;
  applyLanguage(language);
}
async function loadHostedRegion() {
  const r = await fetch('/api/hosted-region');
  const j = await r.json();
  const region = j.region || 'auto';
  document.getElementById('hostedRegion').value = region;
  document.getElementById('setupHostedRegion').value = region;
  document.getElementById('hostedRegionStatus').textContent = t('hostedRegionStatus')(regionLabel(region));
}
async function detectHostedRegion() {
  const current = document.getElementById('hostedRegion').value || 'auto';
  if (current !== 'auto') return;
  const payload = {
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || '',
    locale: navigator.language || '',
    languages: Array.from(navigator.languages || [])
  };
  const r = await fetch('/api/hosted-region/detect', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  });
  const j = await r.json();
  if (!r.ok || !j.region) return;
  document.getElementById('hostedRegion').value = j.region;
  document.getElementById('setupHostedRegion').value = j.region;
  document.getElementById('hostedRegionStatus').textContent = t('hostedRegionStatus')(regionLabel(j.region));
}
async function loadPermissionScope() {
  const r = await fetch('/api/permission-scope');
  const j = await r.json();
  const scope = j.scope || 'workspace';
  document.getElementById('permissionScope').value = scope;
  document.getElementById('setupPermissionScope').value = scope;
  document.getElementById('permissionScopeStatus').textContent = t('permissionStatus')(permissionLabel(scope));
}
async function loadTerminalAccess() {
  const r = await fetch('/api/terminal-access');
  const j = await r.json();
  const access = j.access || 'disabled';
  document.getElementById('terminalAccess').value = access;
  document.getElementById('setupTerminalAccess').value = access;
  document.getElementById('terminalAccessStatus').textContent = t('terminalStatus')(terminalLabel(access));
}
document.getElementById('saveLanguage').onclick = async () => {
  const language = document.getElementById('systemLanguage').value;
  const r = await fetch('/api/language', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({language})});
  const j = await r.json();
  if (!r.ok) {
    document.getElementById('languageStatus').textContent = j.detail || JSON.stringify(j);
    return;
  }
  document.getElementById('setupLanguage').value = j.language;
  applyLanguage(j.language);
  addMessage(t('languageSaved'), 'agent');
};
document.getElementById('saveHostedRegion').onclick = async () => {
  const region = document.getElementById('hostedRegion').value;
  const r = await fetch('/api/hosted-region', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({region})});
  const j = await r.json();
  if (!r.ok) {
    document.getElementById('hostedRegionStatus').textContent = j.detail || JSON.stringify(j);
    return;
  }
  document.getElementById('setupHostedRegion').value = j.region;
  document.getElementById('hostedRegionStatus').textContent = t('hostedRegionStatus')(regionLabel(j.region));
  addMessage(t('regionSaved')(regionLabel(j.region)), 'agent');
};
document.getElementById('savePermissionScope').onclick = async () => {
  const scope = document.getElementById('permissionScope').value;
  const r = await fetch('/api/permission-scope', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({scope})});
  const j = await r.json();
  if (!r.ok) {
    document.getElementById('permissionScopeStatus').textContent = j.detail || JSON.stringify(j);
    return;
  }
  document.getElementById('setupPermissionScope').value = j.scope;
  document.getElementById('permissionScopeStatus').textContent = t('permissionStatus')(permissionLabel(j.scope));
  addMessage(t('permissionSaved')(permissionLabel(j.scope)), 'agent');
};
async function saveTerminalAccessValue(access, announce=true) {
  const r = await fetch('/api/terminal-access', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({access})});
  const j = await r.json();
  if (!r.ok) {
    document.getElementById('terminalAccessStatus').textContent = j.detail || JSON.stringify(j);
    return false;
  }
  document.getElementById('terminalAccess').value = j.access;
  document.getElementById('setupTerminalAccess').value = j.access;
  document.getElementById('terminalAccessStatus').textContent = t('terminalStatus')(terminalLabel(j.access));
  if (announce) addMessage(t('terminalSaved')(terminalLabel(j.access)), 'agent');
  return true;
}
document.getElementById('saveTerminalAccess').onclick = async () => {
  await saveTerminalAccessValue(document.getElementById('terminalAccess').value);
};
async function loadRoute() {
  const r = await fetch('/api/route');
  const j = await r.json();
  const sel = document.getElementById('route');
  sel.textContent = '';
  (j.available || ['local','byo','proxy']).forEach(route => {
    const opt = document.createElement('option');
    opt.value = route; opt.textContent = route; opt.selected = route === j.route;
    sel.appendChild(opt);
  });
  const keys = (j.api_keys || []).map(k => `${k.provider}: ${k.key_hint}`).join('  ');
  document.getElementById('keyStatus').textContent = keys || t('noKey');
  loadProviders();
}
async function loadProviders() {
  const r = await fetch('/api/api-keys');
  const j = await r.json();
  const sel = document.getElementById('provider');
  const setupSel = document.getElementById('setupProvider');
  sel.textContent = '';
  setupSel.textContent = '';
  (j.providers || ['anthropic','openai','deepseek']).forEach(p => {
    const opt = document.createElement('option');
    opt.value = p; opt.textContent = p;
    sel.appendChild(opt);
    setupSel.appendChild(opt.cloneNode(true));
  });
  syncProviderToModel();
}
document.getElementById('saveRoute').onclick = async () => {
  const route = document.getElementById('route').value;
  await fetch('/api/route', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({route})});
  loadRoute();
};
document.getElementById('saveKey').onclick = async () => {
  const provider = document.getElementById('provider').value;
  const api_key = document.getElementById('apiKey').value.trim();
  if (!api_key) return;
  const valid = await validateApiKey({
    provider,
    api_key,
    model: document.getElementById('model').value,
    statusId: 'keyStatus',
    buttonId: 'saveKey'
  });
  if (!valid) return;
  const r = await fetch('/api/api-keys', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({provider, api_key})});
  const j = await r.json();
  if (!r.ok) {
    document.getElementById('keyStatus').textContent = j.detail || JSON.stringify(j);
    return;
  }
  await fetch('/api/route', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({route: 'byo'})});
  document.getElementById('apiKey').value = '';
  loadRoute();
  addMessage(t('keySaved'), 'agent');
};
async function validateApiKey({provider, api_key, model, statusId, buttonId}) {
  const status = document.getElementById(statusId);
  const button = document.getElementById(buttonId);
  if (!provider || !api_key || !model) return false;
  status.textContent = t('verifyingKey');
  if (button) button.disabled = true;
  try {
    const r = await fetch('/api/api-keys/validate', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({provider, api_key, model})
    });
    const j = await r.json();
    if (!r.ok || !j.ok) {
      status.textContent = `${t('keyInvalid')}${j.detail || j.error || JSON.stringify(j)}`;
      return false;
    }
    status.textContent = t('keyValid');
    return true;
  } catch (err) {
    status.textContent = `${t('keyInvalid')}${err}`;
    return false;
  } finally {
    if (button) button.disabled = false;
  }
}
async function loadWorkspace() {
  const r = await fetch('/api/workspace');
  const j = await r.json();
  document.getElementById('workspacePath').value = j.workspace || '';
  document.getElementById('setupWorkspacePath').value = j.workspace || '';
  const warning = j.uses_default ? t('workspaceWarning') : '';
  document.getElementById('workspaceStatus').textContent = `${t('workspaceCurrent')}${j.workspace || t('workspaceUnset')}${warning}`;
}
document.getElementById('saveWorkspace').onclick = async () => {
  const path = document.getElementById('workspacePath').value.trim();
  if (!path) return;
  const r = await fetch('/api/workspace', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({path})});
  const j = await r.json();
  if (!r.ok) {
    document.getElementById('workspaceStatus').textContent = j.detail || JSON.stringify(j);
    return;
  }
  document.getElementById('workspaceStatus').textContent = `${t('workspaceCurrent')}${j.workspace}`;
  addMessage(t('workspaceSaved'), 'agent');
};
document.getElementById('openFolderPicker').onclick = () => openFolderPicker('workspacePath');
document.getElementById('setupOpenFolderPicker').onclick = () => openFolderPicker('setupWorkspacePath');
document.getElementById('verifySetupKey').onclick = async () => validateApiKey({
  provider: document.getElementById('setupProvider').value,
  api_key: document.getElementById('setupApiKey').value.trim(),
  model: document.getElementById('setupModel').value,
  statusId: 'setupKeyStatus',
  buttonId: 'verifySetupKey'
});
document.getElementById('systemLanguage').onchange = (e) => applyLanguage(e.target.value);
document.getElementById('setupLanguage').onchange = (e) => applyLanguage(e.target.value);
document.getElementById('closeFolderPicker').onclick = closeFolderPicker;
document.getElementById('folderUp').onclick = () => loadFolder(`${currentFolderPath}/..`);
document.getElementById('chooseFolder').onclick = () => {
  document.getElementById(folderPickerTarget).value = currentFolderPath;
  closeFolderPicker();
};
folderOverlay.addEventListener('click', (e) => {
  if (e.target === folderOverlay) closeFolderPicker();
});
function updateSetupPanels() {
  const mode = document.querySelector('input[name="mode"]:checked').value;
  document.getElementById('ownApiPanel').classList.toggle('active', mode === 'own_api');
  document.getElementById('hostedApiPanel').classList.toggle('active', mode === 'hosted_api');
  document.getElementById('localModelPanel').classList.toggle('active', mode === 'local_model');
}
document.querySelectorAll('input[name="mode"]').forEach(x => x.onchange = updateSetupPanels);
document.getElementById('openSettings').onclick = openSettingsPanel;
document.getElementById('closeSettings').onclick = closeSettingsPanel;
settingsBackdrop.onclick = closeSettingsPanel;
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && folderOverlay.style.display === 'flex') {
    closeFolderPicker();
    return;
  }
  if (e.key === 'Escape') closeSettingsPanel();
});
document.getElementById('openSetup').onclick = () => {
  closeSettingsPanel();
  _hostedApiKey = null;
  document.getElementById('hostedLoginStatus').textContent = '';
  document.getElementById('hostedAccountInfo').style.display = 'none';
  overlay.style.display = 'flex';
  updateSetupPanels();
};
document.getElementById('hostedLoginBtn').addEventListener('click', async () => {
  const email = document.getElementById('setupEmail').value.trim();
  const password = document.getElementById('setupHostedPassword').value;
  const region = document.getElementById('setupHostedRegion').value;
  const status = document.getElementById('hostedLoginStatus');
  const info = document.getElementById('hostedAccountInfo');
  if (!email || !password) {
    status.textContent = '请填写邮箱和密码';
    status.style.color = '#9d1c1c';
    return;
  }
  status.textContent = '登录中…';
  status.style.color = '#888';
  info.style.display = 'none';
  _hostedApiKey = null;
  try {
    const r = await fetch('/api/hosted-login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({email, password, region, base_url: 'http://120.24.223.0'})
    });
    const j = await r.json();
    if (!r.ok) {
      status.textContent = j.detail || '登录失败，请检查邮箱和密码';
      status.style.color = '#9d1c1c';
      return;
    }
    _hostedApiKey = j.api_key;
    const billing = j.billing || {};
    const balance = billing.balance_cny != null ? `¥${Number(billing.balance_cny).toFixed(2)}` : (billing.balance != null ? `¥${Number(billing.balance).toFixed(2)}` : '—');
    status.textContent = '✓ 登录成功';
    status.style.color = '#276749';
    info.style.display = 'block';
    info.innerHTML = `<strong>${j.email}</strong>&emsp;账号余额：${balance}`;
  } catch (err) {
    status.textContent = '网络错误，请稍后重试';
    status.style.color = '#9d1c1c';
  }
});
async function loadOnboarding() {
  const r = await fetch('/api/onboarding');
  const j = await r.json();
  document.getElementById('persona').value = j.persona || 'professional';
  document.getElementById('systemLanguage').value = j.system_language || 'zh';
  document.getElementById('setupLanguage').value = j.system_language || 'zh';
  document.getElementById('hostedRegion').value = j.hosted_region || 'auto';
  document.getElementById('setupHostedRegion').value = j.hosted_region || 'auto';
  document.getElementById('permissionScope').value = j.permission_scope || 'workspace';
  document.getElementById('setupPermissionScope').value = j.permission_scope || 'workspace';
  document.getElementById('terminalAccess').value = j.terminal_access || 'disabled';
  document.getElementById('setupTerminalAccess').value = j.terminal_access || 'disabled';
  applyLanguage(j.system_language || 'zh');
  if (!j.completed) {
    overlay.style.display = 'flex';
  } else if (j.agent_name) {
    addMessage(t('helloAgent')(j.agent_name), 'agent');
  }
}
document.getElementById('onboardingForm').onsubmit = async (e) => {
  e.preventDefault();
  const mode = document.querySelector('input[name="mode"]:checked').value;
  const error = document.getElementById('setupError');
  error.textContent = '';
  if (mode === 'hosted_api' && !_hostedApiKey) {
    error.textContent = t('hostedLoginRequired');
    return;
  }
  const payload = {
    mode,
    model: document.getElementById('setupModel').value,
    system_language: document.getElementById('setupLanguage').value,
    hosted_region: document.getElementById('setupHostedRegion').value,
    hosted_base_url: 'http://120.24.223.0',
    permission_scope: document.getElementById('setupPermissionScope').value,
    terminal_access: document.getElementById('setupTerminalAccess').value,
    provider: document.getElementById('setupProvider').value,
    api_key: mode === 'hosted_api' ? _hostedApiKey : document.getElementById('setupApiKey').value.trim(),
    hosted_email: document.getElementById('setupEmail').value.trim(),
    agent_name: document.getElementById('agentName').value.trim(),
    persona: document.getElementById('persona').value,
    user_intro: document.getElementById('userIntro').value.trim(),
    workspace_path: document.getElementById('setupWorkspacePath').value.trim(),
    save_profile: document.getElementById('saveProfile').checked
  };
  if (mode === 'own_api' && payload.api_key) {
    const valid = await validateApiKey({
      provider: payload.provider,
      api_key: payload.api_key,
      model: payload.model,
      statusId: 'setupKeyStatus',
      buttonId: 'verifySetupKey'
    });
    if (!valid) return;
  }
  const r = await fetch('/api/onboarding', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  const j = await r.json();
  if (!r.ok) {
    error.textContent = j.detail || JSON.stringify(j);
    return;
  }
  overlay.style.display = 'none';
  _hostedApiKey = null;
  applyLanguage(j.system_language || payload.system_language || currentLanguage);
  addMessage(t('setupDone'), 'agent');
	  if (j.candidate_memory_ids && j.candidate_memory_ids.length) {
	    addMessage(t('profileSaved'), 'agent');
	  }
  loadModels();
  loadLanguage();
  loadRoute();
  refreshMemories();
};
document.getElementById('refreshLogs').onclick = refreshLogs;

// ── Communication provider switch ──────────────────────────────────────────
const COMM_PROVIDERS = [
  {id: 'telegram', panel: 'commPanelTelegram'},
  {id: 'feishu', panel: 'commPanelFeishu'},
  {id: 'lark', panel: 'commPanelLark'},
];
function showCommunicationProvider(provider) {
  const selected = COMM_PROVIDERS.some(p => p.id === provider) ? provider : 'telegram';
  COMM_PROVIDERS.forEach(p => {
    const panel = document.getElementById(p.panel);
    if (panel) panel.style.display = p.id === selected ? '' : 'none';
  });
}
const commProvider = document.getElementById('commProvider');
if (commProvider) {
  commProvider.addEventListener('change', () => showCommunicationProvider(commProvider.value));
  showCommunicationProvider(commProvider.value);
}

// ── Telegram settings ──────────────────────────────────────────────────────
async function loadTelegramConfig() {
  const r = await fetch('/api/telegram/config');
  if (!r.ok) return;
  const j = await r.json();
  const info = document.getElementById('tgConfiguredInfo');
  if (j.configured) {
    info.style.display = 'block';
    info.textContent = `✓ 已配置 Bot Token：${j.token_hint}  允许用户：${j.allowed_user_ids || '(未限制)'}`;
    document.getElementById('tgUserIds').value = j.allowed_user_ids || '';
  } else {
    info.style.display = 'none';
  }
}
document.getElementById('tgVerifyBtn').addEventListener('click', async () => {
  const token = document.getElementById('tgBotToken').value.trim();
  const status = document.getElementById('tgVerifyStatus');
  if (!token) { status.textContent = '请先填写 Bot Token'; return; }
  status.textContent = '验证中…';
  const r = await fetch('/api/telegram/verify-token', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({bot_token: token, allowed_user_ids: ''})});
  const j = await r.json();
  if (j.ok) {
    status.textContent = `✓ 验证成功：@${j.username}`;
    status.style.color = '#276749';
  } else {
    status.textContent = `✗ ${j.error || '验证失败'}`;
    status.style.color = '#9d1c1c';
  }
});
document.getElementById('tgFetchIdsBtn').addEventListener('click', async () => {
  const token = document.getElementById('tgBotToken').value.trim();
  const status = document.getElementById('tgFetchStatus');
  if (!token) { status.textContent = '请先填写 Bot Token'; return; }
  status.textContent = '获取中…';
  const r = await fetch('/api/telegram/fetch-updates', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({bot_token: token, allowed_user_ids: ''})});
  const j = await r.json();
  if (j.ok && j.user_ids && j.user_ids.length) {
    const ids = j.user_ids.map(u => u.id).join(',');
    document.getElementById('tgUserIds').value = ids;
    status.textContent = `✓ 找到 ${j.user_ids.length} 个用户：${j.user_ids.map(u => u.name).join('、')}`;
    status.style.color = '#276749';
  } else {
    status.textContent = j.ok ? '未找到用户，请先向 Bot 发送一条消息再重试' : `✗ ${j.error}`;
    status.style.color = j.ok ? '#888' : '#9d1c1c';
  }
});
document.getElementById('tgSaveBtn').addEventListener('click', async () => {
  const token = document.getElementById('tgBotToken').value.trim();
  const ids = document.getElementById('tgUserIds').value.trim();
  const status = document.getElementById('tgSaveStatus');
  if (!token) { status.textContent = '请先填写 Bot Token'; return; }
  status.textContent = '保存中…';
  const r = await fetch('/api/telegram/save', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({bot_token: token, allowed_user_ids: ids})});
  const j = await r.json();
  if (j.ok) {
    status.textContent = `✓ 已保存，Bot 用户名：@${j.username}`;
    status.style.color = '#276749';
    loadTelegramConfig();
  } else {
    status.textContent = `✗ ${j.detail || '保存失败'}`;
    status.style.color = '#9d1c1c';
  }
});
document.getElementById('tgTestBtn').addEventListener('click', async () => {
  const token = document.getElementById('tgBotToken').value.trim();
  const ids = document.getElementById('tgUserIds').value.trim();
  const status = document.getElementById('tgSaveStatus');
  status.textContent = '发送中…';
  const r = await fetch('/api/telegram/test-message', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({bot_token: token, allowed_user_ids: ids})});
  const j = await r.json();
  status.textContent = j.ok ? '✓ 测试消息已发送，请检查手机' : `✗ ${j.detail || '发送失败'}`;
  status.style.color = j.ok ? '#276749' : '#9d1c1c';
});

// ── Feishu settings ────────────────────────────────────────────────────────
async function loadFeishuConfig() {
  const r = await fetch('/api/feishu/config');
  if (!r.ok) return;
  const j = await r.json();
  const info = document.getElementById('feishuConfiguredInfo');
  if (j.configured) {
    info.style.display = 'block';
    info.textContent = `✓ 已配置飞书应用：${j.app_id}`;
    if ((j.domain || 'feishu') === 'feishu') document.getElementById('feishuAppId').value = j.app_id || '';
  } else {
    info.style.display = 'none';
  }
  const larkInfo = document.getElementById('larkConfiguredInfo');
  if (larkInfo) {
    if (j.configured && j.domain === 'lark') {
      larkInfo.style.display = 'block';
      larkInfo.textContent = `✓ 已配置 Lark 应用：${j.app_id}`;
      document.getElementById('larkAppId').value = j.app_id || '';
    } else {
      larkInfo.style.display = 'none';
    }
  }
  const wh = document.getElementById('feishuWebhookUrl');
  if (wh) wh.textContent = j.webhook_url || 'http://127.0.0.1:8000/webhook/feishu';
}
document.getElementById('feishuSaveBtn').addEventListener('click', async () => {
  const app_id = document.getElementById('feishuAppId').value.trim();
  const app_secret = document.getElementById('feishuAppSecret').value.trim();
  const verification_token = document.getElementById('feishuVerifyToken').value.trim();
  const status = document.getElementById('feishuSaveStatus');
  if (!app_id || !app_secret) { status.textContent = '请填写 App ID 和 App Secret'; return; }
  status.textContent = '验证中…';
  const r = await fetch('/api/feishu/save', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({app_id, app_secret, verification_token, receive_mode: 'websocket', domain: 'feishu'})});
  const j = await r.json();
  if (j.ok) {
    status.textContent = `✓ 飞书配置已保存，长连接已启动：${app_id}`;
    status.style.color = '#276749';
    loadFeishuConfig();
  } else {
    status.textContent = `✗ ${j.detail || '保存失败'}`;
    status.style.color = '#9d1c1c';
  }
});
document.getElementById('larkSaveBtn').addEventListener('click', async () => {
  const app_id = document.getElementById('larkAppId').value.trim();
  const app_secret = document.getElementById('larkAppSecret').value.trim();
  const status = document.getElementById('larkSaveStatus');
  if (!app_id || !app_secret) { status.textContent = '请填写 App ID 和 App Secret'; return; }
  status.textContent = '验证中…';
  const r = await fetch('/api/feishu/save', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({app_id, app_secret, verification_token: '', receive_mode: 'websocket', domain: 'lark'})});
  const j = await r.json();
  if (j.ok) {
    status.textContent = `✓ Lark 配置已保存，长连接已启动：${app_id}`;
    status.style.color = '#276749';
    document.getElementById('larkAppSecret').value = '';
    loadFeishuConfig();
  } else {
    status.textContent = `✗ ${j.detail || '保存失败'}`;
    status.style.color = '#9d1c1c';
  }
});

// ── Telegram inbox polling ─────────────────────────────────────────────────
let _tgInboxLastId = 0;
async function pollTelegramInbox() {
  try {
    const r = await fetch(`/api/telegram/inbox?since=${_tgInboxLastId}`);
    if (!r.ok) return;
    const j = await r.json();
    if (j.messages && j.messages.length) {
      j.messages.forEach(m => {
        if (m.id > _tgInboxLastId) _tgInboxLastId = m.id;
        const prefix = m.direction === 'in' ? `📱 ${m.from_user || 'Telegram'}：` : '🤖 Agent：';
        addMessage(prefix + m.text, m.direction === 'in' ? 'user' : 'agent');
      });
      const banner = document.getElementById('tgInboxBanner');
      const text = document.getElementById('tgInboxText');
      banner.style.display = 'block';
      text.textContent = `收到 ${j.messages.length} 条 Telegram 消息`;
      setTimeout(() => { banner.style.display = 'none'; }, 4000);
    }
  } catch (_) {}
}
setInterval(pollTelegramInbox, 5000);

// ── Mobile status indicator ──────────────────────────────────────────────────
const mobileIndicator = document.getElementById('mobileStatusIndicator');
const mobileSettingsStatus = document.getElementById('mobileSettingsStatus');

async function updateMobileStatus() {
  try {
    const r = await fetch('/api/mobile-status');
    if (!r.ok) return;
    const { configured, connected } = await r.json();

    // Header indicator
    if (!configured) {
      mobileIndicator.innerHTML = '<a href="/pair" target="_blank" style="display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border:1px solid #bbb;border-radius:14px;font-size:12px;color:#555;background:#f9f9f9;text-decoration:none;cursor:pointer">📱 连接手机</a>';
    } else if (connected) {
      mobileIndicator.innerHTML = '<span style="display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border:1px solid #b7e8b0;border-radius:14px;font-size:12px;color:#2c6e28;background:#f0faf0"><span style="width:7px;height:7px;border-radius:50%;background:#2e8b2e;flex-shrink:0"></span>手机已连接</span>';
    } else {
      mobileIndicator.innerHTML = '<a href="/pair" target="_blank" style="display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border:1px solid #f5c5a0;border-radius:14px;font-size:12px;color:#8b4500;background:#fff8f3;text-decoration:none;cursor:pointer"><span style="width:7px;height:7px;border-radius:50%;background:#e08020;flex-shrink:0"></span>手机未连接</a>';
    }

    // Settings panel status text
    if (mobileSettingsStatus) {
      if (!configured) {
        mobileSettingsStatus.textContent = '未配置：请先在 .env 中设置 MOBILE_RELAY_URL 和 MOBILE_RELAY_ADMIN_SECRET';
      } else if (connected) {
        mobileSettingsStatus.textContent = '✓ 已连接';
        mobileSettingsStatus.style.color = '#2c6e28';
      } else {
        mobileSettingsStatus.textContent = '已配置，等待手机连接…';
        mobileSettingsStatus.style.color = '#8b4500';
      }
    }
  } catch (_) {}
}

document.getElementById('openPairingPage').addEventListener('click', () => {
  window.open('/pair', '_blank');
});

updateMobileStatus();
setInterval(updateMobileStatus, 5000);

async function init() {
  await loadLanguage();
  await loadHostedRegion();
  await detectHostedRegion();
  await loadPermissionScope();
  await loadTerminalAccess();
  await loadModels();
  await loadRoute();
  await loadWorkspace();
  await refreshLogs();
  await refreshMemories();
  await loadOnboarding();
  await loadTelegramConfig();
  await loadFeishuConfig();
}
init();
</script>
</body>
</html>
"""


MARKETING_CSS = """
<style>
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#fbfbf8;color:#1f2933;line-height:1.6}
a{color:#0f5f8f;text-decoration:none}
.wrap{max-width:1120px;margin:0 auto;padding:0 22px}
nav{height:58px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #e4e0d6;background:#fff}
.brand{font-weight:700;letter-spacing:.01em}
.navlinks{display:flex;gap:18px;font-size:14px}
.hero{min-height:560px;display:grid;grid-template-columns:minmax(0,1fr) minmax(360px,520px);gap:44px;align-items:center}
h1{font-size:56px;line-height:1.02;margin:0 0 18px;letter-spacing:0}
.lead{font-size:19px;color:#52606d;max-width:640px;margin:0 0 28px}
.actions{display:flex;gap:12px;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;justify-content:center;border:1px solid #1f2933;border-radius:6px;padding:10px 14px;font-weight:600;background:#1f2933;color:#fff}
.btn.secondary{background:#fff;color:#1f2933}
.product{border:1px solid #d8d2c2;background:#fff;border-radius:8px;box-shadow:0 18px 50px rgba(31,41,51,.13);overflow:hidden}
.bar{height:38px;background:#f0ede5;border-bottom:1px solid #d8d2c2;display:flex;align-items:center;gap:7px;padding:0 12px}
.dot{width:10px;height:10px;border-radius:50%;background:#9aa5b1}
.screen{display:grid;grid-template-columns:1fr 170px;min-height:330px}
.chat{padding:18px;background:#fff}
.msg{border:1px solid #e4e0d6;border-radius:6px;padding:10px 12px;margin-bottom:10px;background:#fbfbf8;font-size:14px}
.msg.user{background:#e9f4fb;margin-left:36px}
.side{border-left:1px solid #e4e0d6;background:#f8f6ef;padding:14px}
.pill{border:1px solid #d8d2c2;background:#fff;border-radius:6px;padding:8px;margin-bottom:8px;font-size:13px}
.band{border-top:1px solid #e4e0d6;padding:54px 0}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}
.card{border:1px solid #e4e0d6;background:#fff;border-radius:8px;padding:18px}
h2{font-size:30px;margin:0 0 18px}
h3{margin:0 0 8px;font-size:17px}
.muted{color:#65737f}
.legal{max-width:860px;padding:34px 22px 64px}
.legal h1{font-size:38px}
.legal h2{font-size:22px;margin-top:30px}
footer{border-top:1px solid #e4e0d6;padding:24px 0;color:#65737f;font-size:14px}
@media(max-width:860px){.hero{grid-template-columns:1fr;min-height:auto;padding:44px 0}.screen{grid-template-columns:1fr}h1{font-size:40px}.grid{grid-template-columns:1fr}.navlinks{gap:10px}}
</style>
"""


LANDING_HTML = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Auctus Agent</title>{MARKETING_CSS}</head>
<body>
<nav><div class="wrap" style="display:flex;align-items:center;justify-content:space-between;width:100%">
<div class="brand">Auctus Agent</div><div class="navlinks"><a href="/">App</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a></div>
</div></nav>
<main>
<section class="wrap hero">
<div>
<h1>Auctus Agent</h1>
<p class="lead">本地优先的个人 AI Agent：处理文件、生成报告和表格、维护长期记忆，并从确认过的历史任务中学习你的工作方式。</p>
<div class="actions"><a class="btn" href="/">打开本地 App</a><a class="btn secondary" href="/files/2026-05-13_cost_report.md">查看成本日报</a></div>
</div>
<div class="product" aria-label="Auctus Agent product preview">
<div class="bar"><span class="dot"></span><span class="dot"></span><span class="dot"></span><span class="muted" style="font-size:13px">127.0.0.1:8000</span></div>
<div class="screen">
<div class="chat">
<div class="msg user">读取 PRD，生成总结、功能清单和测试用例。</div>
<div class="msg">已生成 Markdown 报告、Excel 表格和网页原型。输出路径已列出。</div>
<div class="msg user">以后这种任务按同样流程处理。</div>
<div class="msg">已提炼为候选学习项，确认后会进入运行时规则。</div>
</div>
<div class="side"><div class="pill">Memory candidates</div><div class="pill">Tool logs</div><div class="pill">Route: local / byo / proxy</div><div class="pill">Cost report ready</div></div>
</div></div>
</section>
<section class="band"><div class="wrap"><h2>核心能力</h2><div class="grid">
<div class="card"><h3>文件工作流</h3><p class="muted">读取输入文件，生成 Markdown、Excel、HTML 原型和结构化输出。</p></div>
<div class="card"><h3>长期记忆</h3><p class="muted">偏好、项目背景和规则进入候选记忆，经确认后才会生效。</p></div>
<div class="card"><h3>自我进化</h3><p class="muted">从历史对话和工具错误中提炼 workflow、prompt rule 和 retry hint。</p></div>
<div class="card"><h3>成本可见</h3><p class="muted">记录模型调用、token 和 cost，支持每日成本 Markdown 报告。</p></div>
<div class="card"><h3>本地优先</h3><p class="muted">SQLite、Chroma、日志和输出文件默认保存在本机目录。</p></div>
<div class="card"><h3>多模型路由</h3><p class="muted">支持 local、BYO key 和 proxy 路由，便于自用和商业化扩展。</p></div>
</div></div></section>
</main><footer><div class="wrap">Auctus Agent · Local-first personal AI agent</div></footer>
</body></html>"""


PRIVACY_HTML = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Privacy Policy - Auctus Agent</title>{MARKETING_CSS}</head><body>
<nav><div class="wrap" style="display:flex;align-items:center;justify-content:space-between;width:100%"><div class="brand">Auctus Agent</div><div class="navlinks"><a href="/landing">Landing</a><a href="/">App</a><a href="/terms">Terms</a></div></div></nav>
<main class="wrap legal">
<h1>隐私政策</h1>
<p class="muted">最后更新：2026-05-13</p>
<p>Auctus Agent 是本地优先的个人 AI Agent。默认情况下，对话历史、长期记忆、工具日志、成本记录和生成文件保存在你的本机工作目录中。</p>
<h2>我们处理哪些数据</h2>
<p>系统可能处理你输入的消息、上传文件、生成文件、工具调用日志、模型调用用量、API key 配置、候选记忆和已确认记忆。</p>
<h2>数据存放位置</h2>
<p>默认存放在本机的 <code>data/</code>、<code>outputs/</code>、<code>inputs/</code> 和 <code>logs/</code>。如果你启用 Cloud Relay、Proxy 或第三方模型 API，相关请求内容会按配置发送到对应服务。</p>
<h2>API Key</h2>
<p>BYO API key 会在本地加密保存。你也可以只通过环境变量提供 key。请不要把包含 key 的 <code>.env</code> 文件提交到公开仓库。</p>
<h2>模型服务</h2>
<p>当你使用 OpenAI、Anthropic、DeepSeek、DashScope、Ollama 或其他模型提供商时，模型请求会受对应提供商的隐私条款约束。</p>
<h2>用户控制</h2>
<p>你可以删除记忆、拒绝候选记忆、停用或回滚自我学习项，也可以直接删除本机数据目录中的运行数据。</p>
<h2>联系我们</h2>
<p>如果你在发布版本中使用 Auctus Agent，请在这里补充运营主体和联系邮箱。</p>
</main></body></html>"""


TERMS_HTML = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Terms of Use - Auctus Agent</title>{MARKETING_CSS}</head><body>
<nav><div class="wrap" style="display:flex;align-items:center;justify-content:space-between;width:100%"><div class="brand">Auctus Agent</div><div class="navlinks"><a href="/landing">Landing</a><a href="/">App</a><a href="/privacy">Privacy</a></div></div></nav>
<main class="wrap legal">
<h1>用户协议</h1>
<p class="muted">最后更新：2026-05-13</p>
<h2>服务说明</h2>
<p>Auctus Agent 提供本地 AI Agent、文件处理、记忆管理、模型路由、成本统计和自动学习辅助功能。具体能力取决于你的本地环境、模型配置和启用的第三方服务。</p>
<h2>用户责任</h2>
<p>你应确保输入、上传和生成内容拥有合法使用权限，并自行负责 API key、访问令牌和本地数据的安全。</p>
<h2>AI 输出</h2>
<p>AI 输出可能不准确或不完整。你应在用于法律、医疗、财务、商业发布或其他高风险场景前自行审核。</p>
<h2>禁止事项</h2>
<p>不得使用 Auctus Agent 进行违法、侵权、绕过访问控制、泄露敏感凭据、攻击系统或违反第三方服务条款的活动。</p>
<h2>第三方服务</h2>
<p>当你配置模型提供商、Telegram、Cloud Relay 或其他服务时，你同时受对应第三方服务条款约束。</p>
<h2>免责声明</h2>
<p>Auctus Agent 按现状提供。除适用法律另有要求外，不对服务连续性、输出准确性、数据丢失或间接损失作保证。</p>
<h2>变更</h2>
<p>发布版本的条款可能随功能、收费方式和运营主体变化而更新。</p>
</main></body></html>"""


class ChatIn(BaseModel):
    session_id: Optional[str] = None
    message: str
    terminal_permission: Optional[str] = None
    calendar_permission: Optional[str] = None
    file_permission: Optional[str] = None
    task_mode: Optional[bool] = False


class ChatOut(BaseModel):
    session_id: str
    reply: str
    files: list[str] = []
    permission_request: Optional[dict] = None
    task: Optional[dict] = None


class ModelIn(BaseModel):
    model: str


class AutoModelIn(BaseModel):
    enabled: bool


class ProactiveSuggestionsIn(BaseModel):
    enabled: bool


class RouteIn(BaseModel):
    route: str


class LanguageIn(BaseModel):
    language: str


class HostedRegionIn(BaseModel):
    region: str


class HostedRegionDetectIn(BaseModel):
    timezone: Optional[str] = None
    locale: Optional[str] = None
    languages: list[str] = []


class HostedLoginIn(BaseModel):
    base_url: str = "http://120.24.223.0"
    email: str
    password: str
    region: str = "auto"


class HostedBillingIn(BaseModel):
    base_url: str
    token: str


def _fetch_hosted_billing_summary(client: httpx.Client, base_url: str, auth_headers: dict, region: str = "global") -> dict:
    billing: dict = {}
    status_res = client.get(f"{base_url}/subscriptions/billing-status?region={region}", headers=auth_headers)
    if status_res.is_success:
        billing = status_res.json()

    balances_res = client.get(f"{base_url}/billing/balance", headers=auth_headers)
    if balances_res.is_success:
        balances = balances_res.json()
        billing["balances"] = balances
        primary = next((b for b in balances if b.get("region") == region), None)
        if primary is None:
            primary = next((b for b in balances if b.get("region") == "global"), None)
        if primary is not None:
            billing["balance"] = {"amount": primary.get("balance", 0.0), "region": primary.get("region")}

    subs_res = client.get(f"{base_url}/subscriptions/my", headers=auth_headers)
    if subs_res.is_success:
        subs = subs_res.json()
        billing["subscriptions"] = subs
        usable = [
            s for s in subs
            if s.get("status") in {"active", "cancelled"} and int(s.get("days_remaining") or 0) >= 0
        ]
        if usable:
            billing["subscription"] = usable[0]

    return billing


class PermissionScopeIn(BaseModel):
    scope: str


class TerminalAccessIn(BaseModel):
    access: str


class TerminalSessionStartIn(BaseModel):
    command: str
    working_directory: Optional[str] = None
    label: str = ""


class TerminalSessionSendIn(BaseModel):
    text: str
    append_newline: bool = True


class TerminalSessionStopIn(BaseModel):
    force: bool = False


class CalendarAccessIn(BaseModel):
    access: str


class ApiKeyIn(BaseModel):
    provider: str
    api_key: str
    model: Optional[str] = None


class ApiKeyValidationIn(BaseModel):
    provider: str
    api_key: str
    model: str


class EmailAccountIn(BaseModel):
    email_address: str
    username: str = ""
    password: str
    imap_host: str
    imap_port: int = 993
    imap_ssl: bool = True
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_ssl: bool = True
    label: str = ""


class WorkspaceIn(BaseModel):
    path: str


class OnboardingIn(BaseModel):
    mode: str
    model: Optional[str] = None
    provider: Optional[str] = None
    api_key: Optional[str] = None
    hosted_email: Optional[str] = None
    hosted_region: Optional[str] = None
    hosted_base_url: Optional[str] = None
    agent_name: Optional[str] = None
    persona: Optional[str] = None
    system_language: Optional[str] = None
    user_intro: Optional[str] = None
    workspace_path: Optional[str] = None
    permission_scope: Optional[str] = None
    terminal_access: Optional[str] = None
    calendar_access: Optional[str] = None
    save_profile: bool = False


class MemoryListOut(BaseModel):
    items: list[dict]


@app.post("/api/shutdown")
def shutdown():
    import threading, os, signal
    threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    return {"ok": True}


@app.get("/api/runtime-info")
def runtime_info() -> dict:
    import os
    app_dir = Path.cwd().resolve()
    return {
        "pid": os.getpid(),
        "app_dir": str(app_dir),
        "data_dir": str(settings.data_dir.resolve()),
        "logs_dir": str(settings.logs_dir.resolve()),
        "version": APP_VERSION,
    }


@app.get("/api/runtime-dir")
def runtime_dir() -> Response:
    return Response(str(Path.cwd().resolve()), media_type="text/plain")


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(
        WEB_UI,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/landing", response_class=HTMLResponse)
def landing():
    return LANDING_HTML


@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    return PRIVACY_HTML


@app.get("/terms", response_class=HTMLResponse)
def terms():
    return TERMS_HTML


@app.get("/billing", response_class=HTMLResponse)
def billing():
    account = accounting.hosted_account_summary()
    email = account["email"] or "未登录"
    balance = account["balance_cents"] / 100
    region = _hosted_region_label(account.get("region", "auto"))
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Auctus Billing</title>{MARKETING_CSS}</head><body>
    <nav><div class="wrap" style="display:flex;align-items:center;justify-content:space-between;width:100%"><div class="brand">Auctus Billing</div><div class="navlinks"><a href="/">App</a><a href="/landing">Landing</a></div></div></nav>
    <main class="wrap legal"><h1>充值与计费</h1>
    <p class="muted">当前账号：{email}</p>
    <p class="muted">服务区域：{region}</p>
    <p>余额：${balance:.2f} · 免费额度：{account["free_tokens"]} tokens</p>
    <h2>当前状态</h2>
    <p>这是托管 API 计费页面的本地占位版。真实支付接入后，这里会显示套餐、订单、支付状态和账单流水。</p>
    <h2>下一步</h2>
    <p>接入支付回调、订单表、余额扣减和管理后台后，Auctus 托管 API 才能用于生产计费。</p>
    </main></body></html>"""


@app.get("/api/onboarding")
def onboarding_state() -> dict:
    state = accounting.get_setup_state()
    _restore_workspace_from_state(state)
    if state.get("model"):
        settings.model = state["model"]
    if state.get("onboarding_mode"):
        _restore_route_from_onboarding_mode(state["onboarding_mode"])
    return {
        "completed": state.get("onboarding_completed") == "1",
        "mode": state.get("onboarding_mode", ""),
        "agent_name": state.get("agent_name", ""),
        "persona": state.get("persona", "professional"),
        "system_language": state.get("system_language", "zh"),
        "system_language_configured": "system_language" in state,
        "hosted_region": _normalize_hosted_region(state.get("hosted_region")),
        "hosted_base_url": state.get("hosted_base_url", "http://120.24.223.0"),
        "permission_scope": _normalize_permission_scope(state.get("permission_scope")),
        "terminal_access": _normalize_terminal_access(state.get("terminal_access")),
        "calendar_access": _normalize_calendar_access(state.get("calendar_access")),
        "hosted_account": accounting.hosted_account_summary(state),
        "route": accounting.current_route(),
        "model": settings.model,
    }


@app.post("/api/onboarding")
def save_onboarding(body: OnboardingIn) -> dict:
    mode = body.mode.strip().lower()
    if mode not in {"own_api", "hosted_api", "local_model"}:
        raise HTTPException(400, "unsupported onboarding mode")

    model = (body.model or "").strip()
    if model:
        settings.model = model

    hosted_account = accounting.hosted_account_summary()
    if mode == "own_api":
        selected_provider = (body.provider or "").strip().lower()
        model_provider = (accounting.provider_for_model(settings.model) or "").strip().lower()
        if body.api_key:
            provider = selected_provider or model_provider
            if not provider:
                raise HTTPException(400, "provider is required")
            accounting.set_api_key(provider, body.api_key)
            accounting.set_route("byo")
        else:
            provider = model_provider or selected_provider
            if not _settings_api_key_for_provider(provider):
                raise HTTPException(400, "api_key is required")
            accounting.set_route("local")
    elif mode == "hosted_api":
        if not body.hosted_email:
            raise HTTPException(400, "hosted_email is required")
        region = _normalize_hosted_region(body.hosted_region)
        base_url = (body.hosted_base_url or "http://120.24.223.0").rstrip("/")
        
        # If api_key provided (new flow with login), save it and use proxy route
        if body.api_key:
            hosted_account = accounting.save_hosted_account(
                body.hosted_email,
                region=region,
                api_key=body.api_key,
                base_url=base_url
            )
            accounting.set_api_key("auctus_hosted", body.api_key, base_url=base_url)
            accounting.set_route("proxy")
        else:
            # Backward compatibility: old flow without api_key
            hosted_account = accounting.save_hosted_account(
                body.hosted_email,
                region=region,
                base_url=base_url
            )
            # Use proxy if configured, otherwise local preview
            if settings.proxy_base_url:
                accounting.set_route("proxy")
            else:
                accounting.set_route("local")
    else:
        accounting.set_route("local")

    workspace_dir = _set_workspace_path(body.workspace_path) if body.workspace_path else settings.workspace_dir.resolve()
    candidate_ids = _store_onboarding_memory_candidates(body)
    current_state = accounting.get_setup_state()
    state = accounting.set_setup_state(
        {
            "onboarding_completed": "1",
            "onboarding_mode": mode,
            "agent_name": (body.agent_name or "").strip(),
            "persona": _normalize_persona(body.persona),
            "system_language": _normalize_language(body.system_language),
            "hosted_region": _normalize_hosted_region(body.hosted_region),
            "hosted_base_url": (body.hosted_base_url or "http://120.24.223.0").rstrip("/"),
            "permission_scope": _normalize_permission_scope(body.permission_scope),
            "terminal_access": _normalize_terminal_access(body.terminal_access),
            "calendar_access": _normalize_calendar_access(body.calendar_access)
            if body.calendar_access is not None
            else _normalize_calendar_access(current_state.get("calendar_access")),
            "model": settings.model,
            "workspace_dir": str(workspace_dir),
        }
    )
    return {
        "completed": True,
        "mode": mode,
        "route": accounting.current_route(),
        "model": settings.model,
        "persona": _normalize_persona(body.persona),
        "system_language": _normalize_language(body.system_language),
        "hosted_region": _normalize_hosted_region(body.hosted_region),
        "permission_scope": _normalize_permission_scope(body.permission_scope),
        "terminal_access": _normalize_terminal_access(body.terminal_access),
        "calendar_access": _normalize_calendar_access(state.get("calendar_access")),
        "workspace": str(workspace_dir),
        "hosted_account": hosted_account,
        "candidate_memory_ids": candidate_ids,
        "state": state,
    }


@app.post("/api/chat", response_model=ChatOut)
def chat(body: ChatIn) -> ChatOut:
    if not body.message.strip():
        raise HTTPException(400, "empty message")
    state = accounting.get_setup_state()
    if state.get("onboarding_mode"):
        _restore_route_from_onboarding_mode(state["onboarding_mode"])
    sid = body.session_id or f"web-{uuid.uuid4().hex[:8]}"
    file_permission = _normalize_file_permission(body.file_permission)
    calendar_permission = _normalize_calendar_permission(body.calendar_permission)
    terminal_permission = _normalize_terminal_permission(body.terminal_permission)
    active_task = task_mode.create_or_resume(sid, body.message) if body.task_mode else None
    plan_only_task_turn = bool(
        active_task
        and int(active_task.get("current_step") or 0) == 0
        and len(active_task.get("activity") or []) <= 2
    )
    if _should_request_file_permission(body.message, file_permission):
        runtime_state.set_state(sid, "waiting_for_user", "waiting_for_file_permission", permission="files")
        return ChatOut(
            session_id=sid,
            reply="",
            task=active_task,
            permission_request={
                "type": "files",
                "message": "这个任务需要访问你电脑上的文件（可能超出当前授权 workspace）。是否临时授权本次访问？",
                "options": ["once", "always", "no"],
            },
        )
    if _should_request_calendar_permission(body.message, calendar_permission):
        runtime_state.set_state(sid, "waiting_for_user", "waiting_for_calendar_permission", permission="calendar")
        return ChatOut(
            session_id=sid,
            reply="",
            task=active_task,
            permission_request={
                "type": "calendar",
                "message": "这个任务需要访问系统日历/提醒事项。是否授权？（如果你不希望我直接写入日历，我也可以生成 .ics 文件供你导入）",
                "options": ["once", "always", "no"],
            },
        )
    if _should_request_terminal_permission(body.message, terminal_permission):
        runtime_state.set_state(sid, "waiting_for_user", "waiting_for_terminal_permission", permission="terminal")
        return ChatOut(
            session_id=sid,
            reply="",
            task=active_task,
            permission_request={
                "type": "terminal",
                "message": "这个任务需要执行终端命令。是否授权？",
                "options": ["once", "always", "no"],
            },
        )
    if calendar_permission == "no":
        runtime_state.set_state(sid, "blocked", "calendar_permission_denied")
        return ChatOut(
            session_id=sid,
            reply="好的，这次我不访问系统日历/提醒事项。如果你愿意，我可以帮你生成一个 .ics 文件，你导入到日历即可。",
            task=active_task,
        )
    if terminal_permission == "no":
        runtime_state.set_state(sid, "blocked", "terminal_permission_denied")
        return ChatOut(session_id=sid, reply="好的，这次不执行终端命令。", task=active_task)
    if file_permission == "no":
        runtime_state.set_state(sid, "blocked", "file_permission_denied")
        return ChatOut(
            session_id=sid,
            task=active_task,
            reply=(
                "好的，这次我不访问 workspace 之外的文件。\n"
                "你可以：\n"
                "- 把目标文件复制/移动到当前 workspace 后再让我处理；或\n"
                "- 在设置里把“文件权限范围”改为“整台电脑”，再重试。"
            ),
        )
    # If a previous task for this session is still running, signal it to stop.
    # The agent loop picks this up at the next iteration boundary (usually within
    # seconds). We then wait for the lock to be released before starting fresh.
    session_control.request_stop(sid)
    lock = session_control.get_lock(sid)
    acquired = lock.acquire(timeout=60)
    if not acquired:
        raise HTTPException(
            503,
            "上一条任务仍在执行中，60 秒内未能停止。请稍后重试，或重启 Auctus Agent。",
        )
    try:
        runtime_state.set_state(sid, "running", "chat_started")
        session_control.reset_stop(sid)
        message = body.message
        file_scope_override = None
        if file_permission in {"once", "always"}:
            if file_permission == "always":
                accounting.set_setup_state({"permission_scope": "full_computer"})
            else:
                file_scope_override = "full_computer"
            message = (
                f"【已授权：文件访问={file_permission}】\n"
                "如果需要访问 workspace 外的文件，现在可以读取/写入支持的文本文件；高风险操作仍需终端权限。\n\n"
                + message
            )
        if calendar_permission in {"once", "always"}:
            if calendar_permission == "always":
                accounting.set_setup_state({"calendar_access": "enabled"})
            # 给 LLM 明确“已授权”的信号，并提供当前可落地的降级方案（生成 .ics）
            message = (
                f"【已授权：日历访问={calendar_permission}】\n"
                "如果你无法直接写入系统日历，请生成可导入的 .ics 文件（周五早上“加油”提醒），并告诉用户如何导入。\n\n"
                + message
            )
        # Auto model routing: pick the best model based on message content (proxy mode only).
        if accounting.get_setup_state().get("auto_model") and accounting.current_route() == "proxy":
            settings.model = _auto_pick_model(message)

        task_context = None
        quick_without_tools = False
        if active_task:
            task_mode.mark_working(sid, "Agent 正在按任务清单推进")
            task_context = task_mode.prompt_context(active_task)
            if plan_only_task_turn:
                task_context += (
                    "\n[本轮执行限制]\n"
                    "这是新任务的第一轮。不要调用工具，不要搜索网页。"
                    "只输出任务清单、当前步骤、已知信息、最多 3 个关键问题和下一步。"
                )
        else:
            task_context = _quick_answer_context()
            quick_without_tools = _should_quick_answer_without_tools(message)

        if terminal_permission in {"once", "always"}:
            if terminal_permission == "always":
                accounting.set_setup_state({"terminal_access": "enabled"})
            # 给 LLM 明确“已授权”的信号，减少“需要你在当前消息明确授权”的二次卡顿
            message = (
                f"【已授权：终端命令={terminal_permission}】\n"
                "你可以调用 run_terminal_command 执行短命令，或调用 terminal_session_start/send/stop 管理长期终端任务；高风险终端工具参数里务必包含 confirmed:true。\n"
                "删除类操作默认必须“移到废纸篓/回收站（可恢复）”，不要直接 rm；只有用户明确要求“永久/彻底删除”时才允许 rm。\n\n"
                + message
            )
            with tools.terminal_access_override("enabled"):
                if file_scope_override:
                    with tools.permission_scope_override(file_scope_override):
                        result = agent.chat(sid, message, extra_system_context=task_context, allow_tools=(not plan_only_task_turn and not quick_without_tools))
                else:
                    result = agent.chat(sid, message, extra_system_context=task_context, allow_tools=(not plan_only_task_turn and not quick_without_tools))
        else:
            if file_scope_override:
                with tools.permission_scope_override(file_scope_override):
                    result = agent.chat(sid, message, extra_system_context=task_context, allow_tools=(not plan_only_task_turn and not quick_without_tools))
            else:
                result = agent.chat(sid, message, extra_system_context=task_context, allow_tools=(not plan_only_task_turn and not quick_without_tools))
    except HTTPException:
        raise
    except Exception as e:
        runtime_state.set_state(sid, "blocked", "runtime_error")
        raise HTTPException(503, _friendly_runtime_error(str(e)))
    finally:
        lock.release()
    files = [_file_url(p) for p in result.get("files", [])]
    reply = result.get("reply", "")
    if active_task:
        active_task = task_mode.mark_after_reply(sid, reply) or active_task
        reply = task_mode.strip_update_markers(reply)
    runtime_state.set_state(sid, "idle", "chat_completed")
    _schedule_post_turn_review(sid, body.message, reply, files)
    return ChatOut(session_id=sid, reply=reply, files=files, task=active_task)


def _sse_event(event_type: str, data: dict) -> str:
    """Format a single Server-Sent Event."""
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event_type}\ndata: {payload}\n\n"


def _pin_generator_to_context(gen_factory):
    """把 sync 生成器整体绑死到一个固定 Context 里运行。

    Starlette/FastAPI 的 StreamingResponse 会用 iterate_in_threadpool +
    anyio.to_thread.run_sync 来迭代 sync 生成器；后者对每次 next() 都做
    contextvars.copy_context()，导致 generator 内部的 `with` 块（基于 ContextVar.set
    + reset(token)）跨 next() 调用时 token 不属于当前 context → 抛
    "Token was created in a different Context"。

    本工具把生成器丢到一个专用 daemon 线程里，使用一份 copy_context() 跑到结束，
    主线程通过 queue 取值并 re-yield。这样 ContextVar 的 set/reset 全部发生在
    同一个 Context 里。
    """
    import contextvars
    import queue
    import threading

    sentinel = object()

    class _Err:
        __slots__ = ("exc",)

        def __init__(self, exc: BaseException) -> None:
            self.exc = exc

    q: "queue.Queue[object]" = queue.Queue(maxsize=64)
    cancel_event = threading.Event()

    def _producer() -> None:
        gen = None
        try:
            gen = gen_factory()
            for item in gen:
                # Put with timeout so we can periodically check cancel_event.
                # If consumer disappeared (client disconnect), the queue fills
                # and we bail out instead of holding the LLM connection forever.
                while True:
                    if cancel_event.is_set():
                        return
                    try:
                        q.put(item, timeout=0.5)
                        break
                    except queue.Full:
                        continue
        except BaseException as exc:  # noqa: BLE001
            try:
                q.put(_Err(exc), timeout=0.5)
            except queue.Full:
                pass
        finally:
            if gen is not None and cancel_event.is_set():
                # Let the inner generator unwind its `with` blocks cleanly
                # (reset ContextVars, close LLM stream).
                try:
                    gen.close()
                except Exception:
                    pass
            try:
                q.put(sentinel, timeout=0.5)
            except queue.Full:
                pass

    ctx = contextvars.copy_context()
    threading.Thread(
        target=ctx.run, args=(_producer,), daemon=True, name="sse-producer"
    ).start()

    def _consumer():
        try:
            while True:
                item = q.get()
                if item is sentinel:
                    return
                if isinstance(item, _Err):
                    raise item.exc
                yield item
        finally:
            # Fires when starlette closes the streaming response (client
            # disconnect / abort) — signal the producer to unwind cleanly.
            cancel_event.set()

    return _consumer()


@app.post("/api/chat/stream")
def chat_stream_endpoint(body: ChatIn):
    """Streaming version of /api/chat. Returns SSE events:
      event: meta       — {session_id, task}
      event: text       — {delta}
      event: tool_start — {name, activity}
      event: tool_end   — {name, ok}
      event: end        — {session_id, reply, files, task}
      event: error      — {message}
      event: permission_request — {type, message, options}  (if pre-check requires it)
    """
    from contextlib import ExitStack

    if not body.message.strip():
        raise HTTPException(400, "empty message")
    state = accounting.get_setup_state()
    if state.get("onboarding_mode"):
        _restore_route_from_onboarding_mode(state["onboarding_mode"])
    sid = body.session_id or f"web-{uuid.uuid4().hex[:8]}"
    file_permission = _normalize_file_permission(body.file_permission)
    calendar_permission = _normalize_calendar_permission(body.calendar_permission)
    terminal_permission = _normalize_terminal_permission(body.terminal_permission)
    active_task = task_mode.create_or_resume(sid, body.message) if body.task_mode else None
    plan_only_task_turn = bool(
        active_task
        and int(active_task.get("current_step") or 0) == 0
        and len(active_task.get("activity") or []) <= 2
    )

    # Permission gates: serialize as a single-shot SSE stream so the UI can use the same reader.
    def _single_event_stream(event_type: str, data: dict):
        yield _sse_event("meta", {"session_id": sid, "task": active_task})
        yield _sse_event(event_type, data)
        yield _sse_event("end", {
            "session_id": sid,
            "reply": data.get("reply", ""),
            "files": [],
            "task": active_task,
        })

    if _should_request_file_permission(body.message, file_permission):
        runtime_state.set_state(sid, "waiting_for_user", "waiting_for_file_permission", permission="files")
        return StreamingResponse(
            _single_event_stream("permission_request", {
                "type": "files",
                "message": "这个任务需要访问你电脑上的文件（可能超出当前授权 workspace）。是否临时授权本次访问？",
                "options": ["once", "always", "no"],
            }),
            media_type="text/event-stream",
        )
    if _should_request_calendar_permission(body.message, calendar_permission):
        runtime_state.set_state(sid, "waiting_for_user", "waiting_for_calendar_permission", permission="calendar")
        return StreamingResponse(
            _single_event_stream("permission_request", {
                "type": "calendar",
                "message": "这个任务需要访问系统日历/提醒事项。是否授权？（如果你不希望我直接写入日历，我也可以生成 .ics 文件供你导入）",
                "options": ["once", "always", "no"],
            }),
            media_type="text/event-stream",
        )
    if _should_request_terminal_permission(body.message, terminal_permission):
        runtime_state.set_state(sid, "waiting_for_user", "waiting_for_terminal_permission", permission="terminal")
        return StreamingResponse(
            _single_event_stream("permission_request", {
                "type": "terminal",
                "message": "这个任务需要执行终端命令。是否授权？",
                "options": ["once", "always", "no"],
            }),
            media_type="text/event-stream",
        )
    if calendar_permission == "no":
        runtime_state.set_state(sid, "blocked", "calendar_permission_denied")
        return StreamingResponse(
            _single_event_stream("text", {
                "delta": "好的，这次我不访问系统日历/提醒事项。如果你愿意，我可以帮你生成一个 .ics 文件，你导入到日历即可。",
                "reply": "好的，这次我不访问系统日历/提醒事项。如果你愿意，我可以帮你生成一个 .ics 文件，你导入到日历即可。",
            }),
            media_type="text/event-stream",
        )
    if terminal_permission == "no":
        runtime_state.set_state(sid, "blocked", "terminal_permission_denied")
        return StreamingResponse(
            _single_event_stream("text", {"delta": "好的，这次不执行终端命令。", "reply": "好的，这次不执行终端命令。"}),
            media_type="text/event-stream",
        )
    if file_permission == "no":
        runtime_state.set_state(sid, "blocked", "file_permission_denied")
        denial = (
            "好的，这次我不访问 workspace 之外的文件。\n"
            "你可以：\n"
            "- 把目标文件复制/移动到当前 workspace 后再让我处理；或\n"
            "- 在设置里把“文件权限范围”改为“整台电脑”，再重试。"
        )
        return StreamingResponse(
            _single_event_stream("text", {"delta": denial, "reply": denial}),
            media_type="text/event-stream",
        )

    session_control.request_stop(sid)
    lock = session_control.get_lock(sid)
    acquired = lock.acquire(timeout=60)
    if not acquired:
        raise HTTPException(503, "上一条任务仍在执行中，60 秒内未能停止。请稍后重试，或重启 Auctus Agent。")

    runtime_state.set_state(sid, "running", "chat_started")
    session_control.reset_stop(sid)
    message = body.message
    file_scope_override = None
    if file_permission in {"once", "always"}:
        if file_permission == "always":
            accounting.set_setup_state({"permission_scope": "full_computer"})
        else:
            file_scope_override = "full_computer"
        message = (
            f"【已授权：文件访问={file_permission}】\n"
            "如果需要访问 workspace 外的文件，现在可以读取/写入支持的文本文件；高风险操作仍需终端权限。\n\n"
            + message
        )
    if calendar_permission in {"once", "always"}:
        if calendar_permission == "always":
            accounting.set_setup_state({"calendar_access": "enabled"})
        message = (
            f"【已授权：日历访问={calendar_permission}】\n"
            "如果你无法直接写入系统日历，请生成可导入的 .ics 文件（周五早上“加油”提醒），并告诉用户如何导入。\n\n"
            + message
        )
    if accounting.get_setup_state().get("auto_model") and accounting.current_route() == "proxy":
        settings.model = _auto_pick_model(message)

    task_context = None
    quick_without_tools = False
    if active_task:
        task_mode.mark_working(sid, "Agent 正在按任务清单推进")
        task_context = task_mode.prompt_context(active_task)
        if plan_only_task_turn:
            task_context += (
                "\n[本轮执行限制]\n"
                "这是新任务的第一轮。不要调用工具，不要搜索网页。"
                "只输出任务清单、当前步骤、已知信息、最多 3 个关键问题和下一步。"
            )
    else:
        task_context = _quick_answer_context()
        quick_without_tools = _should_quick_answer_without_tools(message)

    if terminal_permission in {"once", "always"}:
        if terminal_permission == "always":
            accounting.set_setup_state({"terminal_access": "enabled"})
        message = (
            f"【已授权：终端命令={terminal_permission}】\n"
            "你可以调用 run_terminal_command 执行短命令，或调用 terminal_session_start/send/stop 管理长期终端任务；高风险终端工具参数里务必包含 confirmed:true。\n"
            "删除类操作默认必须“移到废纸篓/回收站（可恢复）”，不要直接 rm；只有用户明确要求“永久/彻底删除”时才允许 rm。\n\n"
            + message
        )

    allow_tools_flag = (not plan_only_task_turn and not quick_without_tools)

    def event_generator():
        accumulated_reply: list[str] = []
        files_produced: list[str] = []
        stopped = False
        try:
            yield _sse_event("meta", {"session_id": sid, "task": active_task})

            with ExitStack() as stack:
                if terminal_permission in {"once", "always"}:
                    stack.enter_context(tools.terminal_access_override("enabled"))
                if file_scope_override:
                    stack.enter_context(tools.permission_scope_override(file_scope_override))
                stream = agent.chat_stream(
                    sid, message,
                    extra_system_context=task_context,
                    allow_tools=allow_tools_flag,
                )
                for event in stream:
                    etype = event.get("type")
                    data = {k: v for k, v in event.items() if k != "type"}
                    if etype == "done":
                        accumulated_reply.append(event.get("reply", ""))
                        files_produced.extend(event.get("files", []))
                    elif etype == "stopped":
                        stopped = True
                        accumulated_reply.append(event.get("reply", ""))
                        files_produced.extend(event.get("files", []))
                    yield _sse_event(etype or "text", data)

            reply = accumulated_reply[0] if accumulated_reply else ""
            updated_task = active_task
            if active_task:
                updated_task = task_mode.mark_after_reply(sid, reply) or active_task
                reply = task_mode.strip_update_markers(reply)
            files = [_file_url(p) for p in files_produced]
            runtime_state.set_state(sid, "idle" if not stopped else "blocked", "chat_completed" if not stopped else "stopped")
            _schedule_post_turn_review(sid, body.message, reply, files)
            yield _sse_event("end", {
                "session_id": sid,
                "reply": reply,
                "files": files,
                "task": updated_task,
                "stopped": stopped,
            })
        except Exception as exc:
            runtime_state.set_state(sid, "blocked", "runtime_error")
            yield _sse_event("error", {"message": _friendly_runtime_error(str(exc))})
        finally:
            lock.release()

    return StreamingResponse(
        _pin_generator_to_context(event_generator),
        media_type="text/event-stream",
    )


@app.get("/api/task/{session_id}")
def task_state(session_id: str) -> dict:
    task = task_mode.get(session_id)
    return {"task": task}


@app.get("/api/tasks")
def task_list(limit: int = 20) -> dict:
    return {"items": task_mode.list_tasks(limit=limit)}


@app.get("/api/evidence/{session_id}")
def evidence_list(session_id: str, limit: int = 20) -> dict:
    return {"items": evidence.list_evidence(session_id, limit=limit)}


@app.post("/api/task/{session_id}/end")
def task_end(session_id: str) -> dict:
    return {"task": task_mode.end(session_id)}


def _quick_answer_context() -> str:
    return (
        "[普通快速回答模式]\n"
        "用户没有开启 Task Mode。不要使用任务清单、当前步骤、长问卷或逐步执行格式，"
        "也不要因为历史里曾经出现任务模式就继续沿用任务模式。\n"
        "先直接给一个有用的简短答案或初步建议；如果缺少信息，只在结尾问最多 1-2 个最关键问题。\n"
        "涉及保险、金融、法律、医疗等高影响事项时，可以说明需要用户自行核对官方报价和条款，"
        "但仍要先给用户一个快速方向。"
    )


def _should_quick_answer_without_tools(message: str) -> bool:
    text = (message or "").lower()
    shopping_or_quote = any(
        signal in text
        for signal in (
            "buy", "purchase", "shopping", "deal", "quote", "insurance", "laptop", "notebook",
            "买", "购买", "划算", "性价比", "报价", "保险", "笔记本", "电脑", "游戏本",
        )
    )
    explicit_live_lookup = any(
        signal in text
        for signal in (
            "search", "browse", "open website", "real-time", "latest price", "check stock",
            "搜索", "上网", "打开网页", "查官网", "实时", "最新价格", "查库存", "现在价格",
        )
    )
    return shopping_or_quote and not explicit_live_lookup


@app.post("/api/chat/stop")
def chat_stop(body: dict) -> dict:
    """Signal the in-flight agent loop for a session to bail out at its next iteration."""
    sid = (body.get("session_id") or "").strip() if isinstance(body, dict) else ""
    if not sid:
        raise HTTPException(400, "session_id required")
    session_control.request_stop(sid)
    return {"ok": True, "session_id": sid}


def _schedule_post_turn_review(session_id: str, user_text: str, reply: str, files: list[str]) -> None:
    """Write a cheap post-turn review in the background.

    This deliberately avoids LLM/RAG/tool calls so it cannot slow the response.
    """
    import time as _time

    def _run() -> None:
        try:
            item = intent.review_summary(session_id, user_text, reply, files)
            preference_candidate = preferences.maybe_store_preference_candidate(user_text, reply)
            profile = preferences.refresh_summary()
            item["ts"] = _time.time()
            item["reply_chars"] = len(reply or "")
            item["preference_candidate"] = preference_candidate
            item["preference_summary_items"] = profile.get("items", 0)
            path = settings.logs_dir / "turn_reviews.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name="turn-review").start()


@app.get("/api/runtime-state/{session_id}")
def get_runtime_state(session_id: str) -> dict:
    return runtime_state.get_state(session_id)


@app.get("/api/runtime-states")
def list_runtime_states(limit: int = Query(50, ge=1, le=200)) -> dict:
    return {"items": runtime_state.list_states(limit=limit)}


@app.get("/api/preferences/summary")
def get_preference_summary() -> dict:
    state = accounting.get_setup_state()
    return {
        "summary": preferences.get_summary(max_chars=1200),
        "updated_at": float(state.get(preferences.UPDATED_KEY) or 0),
    }


@app.post("/api/preferences/summary/refresh")
def refresh_preference_summary() -> dict:
    return preferences.refresh_summary()


@app.post("/api/upload")
async def upload_file(
    request: Request,
    filename: str = Query(..., min_length=1, max_length=180),
) -> dict:
    safe_name = _safe_input_filename(filename)
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty file")
    target = (settings.workspace_dir / safe_name).resolve()
    workspace = settings.workspace_dir.resolve()
    try:
        target.relative_to(workspace)
    except ValueError:
        raise HTTPException(400, "invalid filename")
    target.write_bytes(body)
    return {"filename": safe_name, "size": len(body), "path": str(target)}


@app.get("/api/logs")
def logs(
    tail: int = Query(50, ge=1, le=500),
    tool: Optional[str] = Query(None),
    risk: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
) -> dict:
    log_path = settings.logs_dir / "tool_calls.jsonl"
    if not log_path.exists():
        return {"items": [], "total": 0}
    raw_lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    all_items: list[dict] = []
    for raw in reversed(raw_lines):
        try:
            all_items.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    filtered: list[dict] = []
    q_lower = (q or "").lower()
    for item in all_items:
        if tool and item.get("tool_name") != tool:
            continue
        if risk and item.get("risk_level") != risk:
            continue
        if q_lower and q_lower not in item.get("tool_name", "").lower() and q_lower not in json.dumps(item.get("tool_input", {})).lower():
            continue
        filtered.append(item)
        if len(filtered) >= limit:
            break
    return {"items": filtered[:tail], "total": len(raw_lines)}


@app.get("/api/memories", response_model=MemoryListOut)
def memories(candidates: bool = False, type: Optional[str] = None) -> MemoryListOut:
    items = memory.list_memories(type=type, confirmed=0 if candidates else 1)
    return MemoryListOut(items=items)


@app.post("/api/memories/{memory_id}/confirm")
def confirm_memory(memory_id: str) -> dict:
    result = memory.confirm_memory(memory_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "memory not found"))
    return result


@app.post("/api/memories/{memory_id}/reject")
def reject_memory(memory_id: str) -> dict:
    result = memory.reject_memory(memory_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "memory not found"))
    return result


@app.get("/api/model")
def get_model() -> dict:
    return {"model": settings.model, "available": _available_models()}


@app.post("/api/model")
def set_model(body: ModelIn) -> dict:
    model = body.model.strip()
    if not model:
        raise HTTPException(400, "empty model")
    settings.model = model
    accounting.set_setup_state({"model": model})
    return {"model": settings.model, "available": _available_models()}


@app.get("/api/auto-model")
def get_auto_model() -> dict:
    enabled = bool(accounting.get_setup_state().get("auto_model", False))
    return {"enabled": enabled}


@app.post("/api/auto-model")
def set_auto_model(body: AutoModelIn) -> dict:
    accounting.set_setup_state({"auto_model": body.enabled})
    return {"enabled": body.enabled}


@app.get("/api/proactive-suggestions")
def get_proactive_suggestions() -> dict:
    enabled = accounting.get_setup_state().get("proactive_suggestions", "1") == "1"
    return {"enabled": enabled}


@app.post("/api/proactive-suggestions")
def set_proactive_suggestions(body: ProactiveSuggestionsIn) -> dict:
    accounting.set_setup_state({"proactive_suggestions": "1" if body.enabled else "0"})
    return {"enabled": body.enabled}


@app.get("/api/language")
def get_language() -> dict:
    state = accounting.get_setup_state()
    configured = "system_language" in state
    return {"language": _normalize_language(state.get("system_language")), "configured": configured}


@app.post("/api/language")
def set_language(body: LanguageIn) -> dict:
    language = _normalize_language(body.language)
    accounting.set_setup_state({"system_language": language})
    return {"language": language}


@app.get("/api/hosted-region")
def get_hosted_region() -> dict:
    state = accounting.get_setup_state()
    region = _normalize_hosted_region(state.get("hosted_region"))
    return {
        "region": region,
        "label": _hosted_region_label(region),
        "available": _available_hosted_regions(),
    }


@app.post("/api/hosted-region")
def set_hosted_region(body: HostedRegionIn) -> dict:
    region = _normalize_hosted_region(body.region)
    state = accounting.set_setup_state({"hosted_region": region})
    return {
        "region": region,
        "label": _hosted_region_label(region),
        "available": _available_hosted_regions(),
        "state": state,
    }


@app.post("/api/hosted-region/detect")
def detect_hosted_region(body: HostedRegionDetectIn) -> dict:
    region = _detect_region_from_client(body)
    state = accounting.set_setup_state({"hosted_region": region})
    return {
        "region": region,
        "label": _hosted_region_label(region),
        "state": state,
    }


@app.post("/api/hosted-login")
def hosted_login(body: HostedLoginIn) -> dict:
    _ensure_runtime_dirs()
    base_url = (body.base_url or "http://120.24.223.0").strip().rstrip("/")
    if not base_url.startswith(("https://", "http://")):
        raise HTTPException(400, "invalid hosted API URL")

    email = body.email.strip()
    password = body.password
    if not email or not password:
        raise HTTPException(400, "email and password are required")

    try:
        with httpx.Client(timeout=20.0, trust_env=False) as client:
            login_res = client.post(
                f"{base_url}/auth/login",
                data={"username": email, "password": password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if login_res.status_code >= 400:
                status_code = login_res.status_code if login_res.status_code in {400, 401, 403} else 502
                raise HTTPException(status_code, _hosted_error(login_res, "登录失败，请检查邮箱和密码。"))

            token = login_res.json().get("access_token")
            if not token:
                raise HTTPException(502, "登录成功但后台没有返回 access token")

            auth_headers = {"Authorization": f"Bearer {token}"}
            region = _normalize_hosted_region(body.region)
            billing = _fetch_hosted_billing_summary(
                client,
                base_url,
                auth_headers,
                "cn" if region == "cn" else "global",
            )

            key_res = client.post(
                f"{base_url}/api-keys/",
                headers={**auth_headers, "Content-Type": "application/json"},
                json={"name": "Auctus Agent"},
            )
            if key_res.status_code >= 400:
                raise HTTPException(502, _hosted_error(key_res, "无法创建 API Key"))
            api_key = key_res.json().get("key")
            if not api_key:
                raise HTTPException(502, "API Key 创建成功但后台没有返回 key")

            try:
                accounting.set_setup_state(
                    {
                        "hosted_email": email,
                        "hosted_region": region,
                        "hosted_base_url": base_url,
                    }
                )
            except FileNotFoundError as e:
                missing = getattr(e, "filename", "") or str(e)
                print(f"[hosted-login] local setup state save failed: {missing}")
                raise HTTPException(500, f"登录已通过，但本地保存登录状态失败：{missing}") from e
            return {
                "token": token,
                "email": email,
                "api_key": api_key,
                "base_url": base_url,
                "region": region,
                "billing": billing,
            }
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        raise HTTPException(502, f"无法连接 Auctus API：{e}") from e
    except FileNotFoundError as e:
        import traceback as _tb
        missing = getattr(e, "filename", "") or str(e)
        print(f"[hosted-login] local file error: {missing}\n{_tb.format_exc()}")
        raise HTTPException(500, f"本地文件或目录不存在，无法完成登录：{missing}") from e


@app.post("/api/hosted-billing")
def hosted_billing(body: HostedBillingIn) -> dict:
    base_url = body.base_url.strip().rstrip("/")
    if not base_url.startswith(("https://", "http://")):
        raise HTTPException(400, "invalid hosted API URL")
    if not body.token:
        raise HTTPException(400, "token is required")
    try:
        with httpx.Client(timeout=10.0, trust_env=False) as client:
            headers = {"Authorization": f"Bearer {body.token}"}
            res = client.get(f"{base_url}/subscriptions/billing-status", headers=headers)
            if res.status_code == 401:
                raise HTTPException(401, "session expired")
            if not res.is_success:
                raise HTTPException(502, "billing fetch failed")
            return _fetch_hosted_billing_summary(client, base_url, headers)
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        raise HTTPException(502, f"无法连接 Auctus API：{e}") from e


@app.get("/api/permission-scope")
def get_permission_scope() -> dict:
    state = accounting.get_setup_state()
    scope = _normalize_permission_scope(state.get("permission_scope"))
    return {
        "scope": scope,
        "label": _permission_scope_label(scope),
        "available": _available_permission_scopes(),
    }


@app.post("/api/permission-scope")
def set_permission_scope(body: PermissionScopeIn) -> dict:
    scope = _normalize_permission_scope(body.scope)
    state = accounting.set_setup_state({"permission_scope": scope})
    return {
        "scope": scope,
        "label": _permission_scope_label(scope),
        "available": _available_permission_scopes(),
        "state": state,
    }


@app.get("/api/terminal-access")
def get_terminal_access() -> dict:
    state = accounting.get_setup_state()
    access = _normalize_terminal_access(state.get("terminal_access"))
    return {
        "access": access,
        "label": _terminal_access_label(access),
        "available": _available_terminal_access(),
    }


@app.post("/api/terminal-access")
def set_terminal_access(body: TerminalAccessIn) -> dict:
    access = _normalize_terminal_access(body.access)
    state = accounting.set_setup_state({"terminal_access": access})
    return {
        "access": access,
        "label": _terminal_access_label(access),
        "available": _available_terminal_access(),
        "state": state,
    }


@app.get("/api/terminal-sessions")
def api_terminal_sessions() -> dict:
    return tools.terminal_session_list()


@app.post("/api/terminal-sessions")
def api_terminal_session_start(body: TerminalSessionStartIn) -> dict:
    result = tools.terminal_session_start(
        command=body.command,
        working_directory=body.working_directory,
        label=body.label,
    )
    if result.get("error"):
        raise HTTPException(400, result["error"])
    return result


@app.get("/api/terminal-sessions/{session_id}")
def api_terminal_session_tail(
    session_id: str,
    lines: int = Query(80, ge=1, le=500),
) -> dict:
    result = tools.terminal_session_tail(session_id=session_id, lines=lines)
    if result.get("error"):
        raise HTTPException(404, result["error"])
    return result


@app.post("/api/terminal-sessions/{session_id}/send")
def api_terminal_session_send(session_id: str, body: TerminalSessionSendIn) -> dict:
    result = tools.terminal_session_send(
        session_id=session_id,
        text=body.text,
        append_newline=body.append_newline,
    )
    if result.get("error"):
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/terminal-sessions/{session_id}/stop")
def api_terminal_session_stop(session_id: str, body: TerminalSessionStopIn) -> dict:
    result = tools.terminal_session_stop(session_id=session_id, force=body.force)
    if result.get("error"):
        raise HTTPException(400, result["error"])
    return result


@app.get("/api/calendar-access")
def get_calendar_access() -> dict:
    state = accounting.get_setup_state()
    access = _normalize_calendar_access(state.get("calendar_access"))
    return {
        "access": access,
        "label": _calendar_access_label(access),
        "available": _available_calendar_access(),
    }


@app.post("/api/calendar-access")
def set_calendar_access(body: CalendarAccessIn) -> dict:
    access = _normalize_calendar_access(body.access)
    state = accounting.set_setup_state({"calendar_access": access})
    return {
        "access": access,
        "label": _calendar_access_label(access),
        "available": _available_calendar_access(),
        "state": state,
    }


@app.get("/api/route")
def get_route() -> dict:
    state = accounting.get_setup_state()
    if state.get("onboarding_mode"):
        _restore_route_from_onboarding_mode(state["onboarding_mode"])
    return {
        "route": accounting.current_route(),
        "hosted_region": _normalize_hosted_region(state.get("hosted_region")),
        "available": sorted(accounting.ROUTES),
        "api_keys": accounting.list_api_keys(),
    }


@app.post("/api/route")
def set_route(body: RouteIn) -> dict:
    try:
        route = accounting.set_route(body.route)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"route": route, "available": sorted(accounting.ROUTES), "api_keys": accounting.list_api_keys()}


@app.post("/api/api-keys")
def save_api_key(body: ApiKeyIn) -> dict:
    provider = body.provider.strip().lower()
    model = (body.model or "").strip()
    if model:
        expected_provider = accounting.provider_for_model(model)
        if expected_provider and expected_provider != provider:
            raise HTTPException(400, f"selected model uses {expected_provider}, not {provider}")
    try:
        item = accounting.set_api_key(provider, body.api_key)
        if model:
            settings.model = model
        accounting.set_route("byo")
        accounting.set_setup_state(
            {
                "onboarding_completed": "1",
                "onboarding_mode": "own_api",
                "model": settings.model,
            }
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {**item, "route": accounting.current_route()}


@app.post("/api/api-keys/validate")
def validate_api_key(body: ApiKeyValidationIn) -> dict:
    provider = body.provider.strip().lower()
    model = body.model.strip()
    api_key = body.api_key.strip()
    if provider not in accounting.PROVIDERS:
        raise HTTPException(400, f"unsupported provider: {provider}")
    if not api_key:
        raise HTTPException(400, "empty api key")
    expected_provider = accounting.provider_for_model(model)
    if expected_provider and expected_provider != provider:
        raise HTTPException(400, f"selected model uses {expected_provider}, not {provider}")
    try:
        _validate_api_key_live(model=model, api_key=api_key)
    except Exception as e:
        raise HTTPException(400, _friendly_runtime_error(str(e)))
    return {"ok": True, "provider": provider, "model": model}


@app.get("/api/api-keys")
def api_keys() -> dict:
    return {"items": accounting.list_api_keys(), "providers": sorted(accounting.PROVIDERS)}


@app.delete("/api/api-keys/{provider}")
def delete_api_key(provider: str) -> dict:
    return accounting.delete_api_key(provider)


@app.get("/api/email-accounts")
def list_email_accounts_endpoint() -> dict:
    return {"items": accounting.list_email_accounts(), "providers": accounting.EMAIL_PROVIDERS}


@app.post("/api/email-accounts")
def add_email_account(body: EmailAccountIn) -> dict:
    email_address = body.email_address.strip()
    if not email_address or "@" not in email_address:
        raise HTTPException(400, "invalid email address")
    if not body.password.strip():
        raise HTTPException(400, "password is required")
    if not body.imap_host.strip():
        raise HTTPException(400, "imap_host is required")
    try:
        item = accounting.save_email_account(
            email_address=email_address,
            username=body.username.strip() or email_address,
            password=body.password.strip(),
            imap_host=body.imap_host.strip(),
            imap_port=body.imap_port,
            imap_ssl=body.imap_ssl,
            smtp_host=body.smtp_host.strip(),
            smtp_port=body.smtp_port,
            smtp_ssl=body.smtp_ssl,
            label=body.label.strip(),
        )
    except Exception as e:
        raise HTTPException(400, str(e))
    return item


@app.delete("/api/email-accounts/{account_id}")
def remove_email_account(account_id: str) -> dict:
    return accounting.delete_email_account(account_id)


@app.post("/api/email-accounts/{account_id}/test")
def test_email_account(account_id: str) -> dict:
    from .email_client import test_connection
    account = accounting.get_email_account(account_id)
    if not account:
        raise HTTPException(404, "email account not found")
    return test_connection(account)


# ── Telegram config ──────────────────────────────────────────────────────────

class TelegramConfigIn(BaseModel):
    bot_token: str
    allowed_user_ids: str = ""


def _tg_api(token: str, method: str, params: Optional[dict] = None) -> dict:
    import urllib.request
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        import urllib.parse
        url += "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"ok": False, "description": str(e)}


@app.get("/api/telegram/config")
def get_telegram_config() -> dict:
    token = settings.telegram_bot_token or ""
    ids = settings.telegram_allowed_user_ids
    masked = f"...{token[-6:]}" if len(token) > 6 else ("(未配置)" if not token else token)
    return {
        "configured": bool(token),
        "token_hint": masked,
        "allowed_user_ids": ids,
    }


@app.post("/api/telegram/verify-token")
def verify_telegram_token(body: TelegramConfigIn) -> dict:
    token = body.bot_token.strip()
    if not token:
        raise HTTPException(400, "bot_token is required")
    result = _tg_api(token, "getMe")
    if not result.get("ok"):
        return {"ok": False, "error": result.get("description", "Token 无效")}
    bot = result.get("result", {})
    return {"ok": True, "username": bot.get("username", ""), "name": bot.get("first_name", "")}


@app.post("/api/telegram/fetch-updates")
def fetch_telegram_updates(body: TelegramConfigIn) -> dict:
    """Return the most recent sender user IDs from getUpdates (for first-time ID lookup)."""
    token = body.bot_token.strip()
    if not token:
        raise HTTPException(400, "bot_token is required")
    result = _tg_api(token, "getUpdates", {"limit": 10, "timeout": 0})
    if not result.get("ok"):
        return {"ok": False, "error": result.get("description", "获取失败"), "user_ids": []}
    updates = result.get("result", [])
    seen: dict[int, str] = {}
    for upd in updates:
        msg = upd.get("message") or upd.get("callback_query", {}).get("message")
        sender = (upd.get("message") or {}).get("from") or {}
        uid = sender.get("id")
        if uid and uid not in seen:
            name = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")])) or str(uid)
            seen[uid] = name
    return {"ok": True, "user_ids": [{"id": k, "name": v} for k, v in seen.items()]}


@app.post("/api/telegram/save")
def save_telegram_config(body: TelegramConfigIn) -> dict:
    token = body.bot_token.strip()
    ids = body.allowed_user_ids.strip()
    if not token:
        raise HTTPException(400, "bot_token is required")
    verify = _tg_api(token, "getMe")
    if not verify.get("ok"):
        raise HTTPException(400, verify.get("description", "Token 验证失败"))
    env_path = Path(".env")
    try:
        from dotenv import set_key as _set_key
        env_path.touch()
        _set_key(str(env_path), "TELEGRAM_BOT_TOKEN", token)
        _set_key(str(env_path), "TELEGRAM_ALLOWED_USER_IDS", ids)
    except Exception as e:
        raise HTTPException(500, f"写入 .env 失败：{e}")
    settings.telegram_bot_token = token
    settings.telegram_allowed_user_ids = ids
    try:
        from . import telegram_bot as _tg_bot
        _tg_bot.run_in_thread()
    except Exception as exc:
        raise HTTPException(500, f"Telegram 配置已保存，但启动 Bot 失败：{type(exc).__name__}: {exc}")
    bot = verify.get("result", {})
    return {"ok": True, "username": bot.get("username", ""), "name": bot.get("first_name", "")}


@app.post("/api/telegram/test-message")
def send_telegram_test(body: TelegramConfigIn) -> dict:
    token = body.bot_token.strip() or (settings.telegram_bot_token or "")
    ids_str = body.allowed_user_ids.strip() or settings.telegram_allowed_user_ids
    if not token:
        raise HTTPException(400, "未配置 bot_token")
    user_ids = [x.strip() for x in ids_str.split(",") if x.strip()]
    if not user_ids:
        raise HTTPException(400, "请先填写允许的 Telegram 用户 ID")
    results = []
    for uid in user_ids[:3]:
        r = _tg_api(token, "sendMessage", {"chat_id": uid, "text": "✅ Auctus Agent 已成功连接 Telegram！"})
        results.append({"user_id": uid, "ok": r.get("ok"), "error": r.get("description", "")})
    return {"ok": all(r["ok"] for r in results), "results": results}


@app.get("/api/telegram/inbox")
def telegram_inbox(since: int = Query(0)) -> dict:
    msgs = accounting.get_telegram_inbox_messages(since_id=since)
    return {"messages": msgs}


# ── Discord config ─────────────────────────────────────────────────────────

class DiscordConfigIn(BaseModel):
    bot_token: str
    allowed_user_ids: str = ""


def _discord_api(token: str, path: str, method: str = "GET", body: Optional[dict] = None) -> dict:
    import urllib.request
    url = f"https://discord.com/api/v10{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "AuctusAgent/1 (https://github.com/auctus)",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return {"ok": True, **json.loads(resp.read())}
    except Exception as e:
        return {"ok": False, "description": str(e)}


@app.get("/api/discord/config")
def get_discord_config() -> dict:
    token = settings.discord_bot_token or ""
    ids = settings.discord_allowed_user_ids
    masked = f"...{token[-6:]}" if len(token) > 6 else ("(未配置)" if not token else token)
    return {"configured": bool(token), "token_hint": masked, "allowed_user_ids": ids}


@app.post("/api/discord/verify-token")
def verify_discord_token(body: DiscordConfigIn) -> dict:
    token = body.bot_token.strip()
    if not token:
        raise HTTPException(400, "bot_token is required")
    result = _discord_api(token, "/users/@me")
    if not result.get("ok"):
        return {"ok": False, "error": result.get("description", "Token 无效")}
    return {"ok": True, "username": result.get("username", ""), "id": result.get("id", "")}


@app.post("/api/discord/save")
def save_discord_config(body: DiscordConfigIn) -> dict:
    token = body.bot_token.strip()
    ids = body.allowed_user_ids.strip()
    if not token:
        raise HTTPException(400, "bot_token is required")
    verify = _discord_api(token, "/users/@me")
    if not verify.get("ok"):
        raise HTTPException(400, verify.get("description", "Token 验证失败"))
    env_path = Path(".env")
    try:
        from dotenv import set_key as _set_key
        env_path.touch()
        _set_key(str(env_path), "DISCORD_BOT_TOKEN", token)
        _set_key(str(env_path), "DISCORD_ALLOWED_USER_IDS", ids)
    except Exception as e:
        raise HTTPException(500, f"写入 .env 失败：{e}")
    settings.discord_bot_token = token
    settings.discord_allowed_user_ids = ids
    try:
        from . import discord_bot as _dc_bot
        _dc_bot.run_in_thread()
    except Exception as exc:
        raise HTTPException(500, f"Discord 配置已保存，但启动 Bot 失败：{type(exc).__name__}: {exc}")
    return {"ok": True, "username": verify.get("username", ""), "id": verify.get("id", "")}


@app.post("/api/discord/test-message")
def send_discord_test(body: DiscordConfigIn) -> dict:
    token = body.bot_token.strip() or (settings.discord_bot_token or "")
    ids_str = body.allowed_user_ids.strip() or settings.discord_allowed_user_ids
    if not token:
        raise HTTPException(400, "未配置 bot_token")
    user_ids = [x.strip() for x in ids_str.split(",") if x.strip()]
    if not user_ids:
        raise HTTPException(400, "请先填写允许的 Discord 用户 ID")
    results = []
    for uid in user_ids[:3]:
        # Create DM channel
        dm = _discord_api(token, "/users/@me/channels", "POST", {"recipient_id": uid})
        if not dm.get("ok"):
            results.append({"user_id": uid, "ok": False, "error": dm.get("description", "创建 DM 失败")})
            continue
        channel_id = dm.get("id", "")
        msg = _discord_api(token, f"/channels/{channel_id}/messages", "POST",
                           {"content": "✅ Auctus Agent 已成功连接 Discord！"})
        results.append({"user_id": uid, "ok": msg.get("ok", False), "error": msg.get("description", "")})
    return {"ok": all(r["ok"] for r in results), "results": results}


# ── Feishu config ──────────────────────────────────────────────────────────

class FeishuConfigIn(BaseModel):
    app_id: str
    app_secret: str
    verification_token: str = ""
    receive_mode: str = "websocket"
    domain: str = "feishu"


@app.get("/api/feishu/config")
def get_feishu_config(request: Request) -> dict:
    state = accounting.get_setup_state()
    app_id = state.get("feishu_app_id", "")
    mode = state.get("feishu_receive_mode", "websocket")
    domain = state.get("feishu_domain", "feishu")
    base = str(request.base_url).rstrip("/")
    return {
        "configured": bool(app_id),
        "app_id": app_id,
        "receive_mode": mode,
        "domain": domain,
        "connection_hint": "长连接 WebSocket 模式：Auctus Agent 会主动连接飞书开放平台，不需要公网地址。",
        "webhook_url": f"{base}/webhook/feishu",
    }


@app.post("/api/feishu/save")
def save_feishu_config(body: FeishuConfigIn) -> dict:
    import urllib.request as _ur, json as _json
    app_id = body.app_id.strip()
    app_secret = body.app_secret.strip()
    receive_mode = (body.receive_mode or "websocket").strip().lower()
    domain = (body.domain or "feishu").strip().lower()
    if not app_id or not app_secret:
        raise HTTPException(400, "app_id 和 app_secret 是必填项")
    if receive_mode not in {"websocket", "webhook"}:
        raise HTTPException(400, "receive_mode 必须是 websocket 或 webhook")
    if domain not in {"feishu", "lark"}:
        raise HTTPException(400, "domain 必须是 feishu 或 lark")
    api_host = "open.larksuite.com" if domain == "lark" else "open.feishu.cn"
    url = f"https://{api_host}/open-apis/auth/v3/tenant_access_token/internal"
    data = _json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = _ur.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with _ur.urlopen(req, timeout=8) as resp:
            result = _json.loads(resp.read())
    except Exception as e:
        raise HTTPException(502, f"无法连接飞书服务：{e}")
    if result.get("code") != 0:
        raise HTTPException(400, f"飞书验证失败：{result.get('msg', '未知错误')}")
    accounting.set_setup_state({
        "feishu_app_id": app_id,
        "feishu_app_secret": app_secret,
        "feishu_verification_token": body.verification_token.strip(),
        "feishu_receive_mode": receive_mode,
        "feishu_domain": domain,
    })
    if receive_mode == "websocket":
        try:
            from . import feishu_bot as _feishu_bot
            _feishu_bot.run_in_thread()
        except Exception as exc:
            raise HTTPException(500, f"飞书配置已保存，但启动长连接失败：{type(exc).__name__}: {exc}")
    return {
        "ok": True,
        "app_id": app_id,
        "receive_mode": receive_mode,
        "domain": domain,
        "message": "已保存。WebSocket 长连接模式不需要公网地址；请在飞书开放平台选择长连接接收事件并订阅 im.message.receive_v1。",
    }


@app.post("/webhook/feishu")
async def feishu_webhook(request: Request) -> dict:
    """Receive Feishu event subscription messages."""
    import json as _json
    body_bytes = await request.body()
    try:
        payload = _json.loads(body_bytes)
    except Exception:
        raise HTTPException(400, "invalid JSON")

    state = accounting.get_setup_state()
    verify_token = state.get("feishu_verification_token", "")

    if verify_token and payload.get("token") != verify_token:
        raise HTTPException(401, "verification token mismatch")

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    event = payload.get("event", {})
    msg = event.get("message", {})
    sender = event.get("sender", {})
    text_content = ""
    try:
        content = _json.loads(msg.get("content", "{}"))
        text_content = content.get("text", "").strip()
    except Exception:
        pass

    if not text_content:
        return {"ok": True}

    sender_id = (sender.get("sender_id") or {}).get("open_id") or (sender.get("sender_id") or {}).get("user_id") or "feishu_user"
    session_id = f"feishu-{sender_id}"

    accounting.add_telegram_inbox_message("in", text_content, from_user=f"飞书:{sender_id}")

    import threading
    def _reply():
        try:
            result = agent.chat(session_id, text_content)
            reply = result.get("reply", "")
            if not reply:
                return
            accounting.add_telegram_inbox_message("out", reply, from_user="agent")
            app_id = state.get("feishu_app_id", "")
            app_secret = state.get("feishu_app_secret", "")
            if not app_id or not app_secret:
                return
            import urllib.request as _ur2
            token_url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
            token_data = _json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
            token_req = _ur2.Request(token_url, data=token_data, headers={"Content-Type": "application/json"})
            with _ur2.urlopen(token_req, timeout=8) as r:
                token_resp = _json.loads(r.read())
            access_token = token_resp.get("tenant_access_token", "")
            if not access_token:
                return
            chat_id = msg.get("chat_id", "")
            if not chat_id:
                return
            send_url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
            send_data = _json.dumps({
                "receive_id": chat_id,
                "msg_type": "text",
                "content": _json.dumps({"text": reply}),
            }).encode()
            send_req = _ur2.Request(send_url, data=send_data, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {access_token}",
            })
            _ur2.urlopen(send_req, timeout=10)
        except Exception as e:
            import logging
            logging.getLogger("feishu").exception("Feishu reply failed: %s", e)

    threading.Thread(target=_reply, daemon=True).start()
    return {"ok": True}


@app.get("/api/workspace")
def get_workspace() -> dict:
    _restore_workspace_from_state()
    workspace = settings.workspace_dir.resolve()
    return {"workspace": str(workspace), "uses_default": _is_default_workspace(workspace)}


@app.get("/api/folders")
def list_folders(path: Optional[str] = None) -> dict:
    target = Path(path).expanduser().resolve() if path else _default_folder_picker_path()
    if not target.exists() or not target.is_dir():
        raise HTTPException(400, "folder path must be an existing folder")

    items: list[dict] = []
    try:
        children = sorted(target.iterdir(), key=lambda p: p.name.lower())
    except OSError as e:
        raise HTTPException(400, f"folder is not readable: {e}")

    for child in children:
        if child.name.startswith(".") or not child.is_dir():
            continue
        readable = True
        try:
            next(child.iterdir(), None)
        except OSError:
            readable = False
        items.append({"name": child.name, "path": str(child.resolve()), "readable": readable})
        if len(items) >= 300:
            break

    parent = target.parent if target.parent != target else None
    return {
        "path": str(target),
        "parent": str(parent) if parent else None,
        "items": items,
        "truncated": len(items) >= 300,
    }


def _default_folder_picker_path() -> Path:
    desktop = Path.home() / "Desktop"
    return desktop.resolve() if desktop.exists() and desktop.is_dir() else Path.home().resolve()


@app.post("/api/workspace")
def set_workspace(body: WorkspaceIn) -> dict:
    target = _set_workspace_path(body.path)
    accounting.set_setup_state({"workspace_dir": str(target)})
    return {"workspace": str(target)}


@app.get("/api/billing")
def billing_state() -> dict:
    summary = accounting.usage_summary()
    account = accounting.hosted_account_summary()
    return {
        "hosted_account": account,
        "hosted_region": account.get("region", "auto"),
        "usage": summary,
        "route": accounting.current_route(),
    }


@app.get("/api/pinned-context")
def get_pinned_context():
    state = accounting.get_setup_state()
    return {"pinned_context": state.get("pinned_context", "")}


@app.post("/api/pinned-context")
async def save_pinned_context(request: Request):
    body = await request.json()
    accounting.set_setup_state({"pinned_context": str(body.get("pinned_context") or "")})
    return {"ok": True}


@app.get("/api/playbooks")
def list_playbooks():
    from . import playbooks as pb_mod
    return {"playbooks": pb_mod.list_playbooks()}


class PlaybookBody(BaseModel):
    name: str
    trigger: str
    content: str


@app.post("/api/playbooks")
def create_playbook(body: PlaybookBody):
    from . import playbooks as pb_mod
    pb = pb_mod.add_playbook(body.name, body.trigger, body.content)
    return {"ok": True, "playbook": pb}


class PlaybookUpdate(BaseModel):
    name: Optional[str] = None
    trigger: Optional[str] = None
    content: Optional[str] = None
    enabled: Optional[bool] = None


@app.put("/api/playbooks/{pb_id}")
def update_playbook(pb_id: str, body: PlaybookUpdate):
    from . import playbooks as pb_mod
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    updated = pb_mod.update_playbook(pb_id, **fields)
    return {"ok": True, "playbook": updated}


@app.delete("/api/playbooks/{pb_id}")
def delete_playbook(pb_id: str):
    from . import playbooks as pb_mod
    removed = pb_mod.remove_playbook(pb_id)
    return {"ok": removed}


@app.get("/api/skills")
def list_skills_api():
    from . import skills_manager
    return {"skills": skills_manager.list_skills()}


class SkillToggleBody(BaseModel):
    skill_id: str


@app.post("/api/skills/{skill_id}/toggle")
def toggle_skill_api(skill_id: str):
    from . import skills_manager
    updated = skills_manager.toggle_skill(skill_id)
    return {"ok": True, "skill": updated}


@app.delete("/api/skills/{skill_id}")
def delete_skill_api(skill_id: str):
    from . import skills_manager
    removed = skills_manager.delete_skill(skill_id)
    return {"ok": removed}


@app.get("/api/daily-brief/config")
def get_daily_brief_config():
    from . import cronjobs
    return cronjobs.get_daily_brief_config()


class DailyBriefBody(BaseModel):
    enabled: bool
    hour: int = 8
    target: str = "telegram"


@app.post("/api/daily-brief/config")
def set_daily_brief_config(body: DailyBriefBody):
    from . import cronjobs
    return cronjobs.set_daily_brief(body.enabled, body.hour, body.target)


@app.get("/api/stats")
def get_stats():
    import time as _time
    from datetime import date, timedelta
    now = _time.time()
    week_ago = now - 7 * 86400

    daily = []
    for i in range(6, -1, -1):
        day = date.today() - timedelta(days=i)
        report = accounting.daily_cost_report(day=day)
        daily.append({
            "date": report["date"],
            "calls": report["calls"],
            "cost": round(report["cost"], 4),
        })

    week_summary = accounting.usage_summary(since=week_ago)
    sessions = memory.list_sessions(limit=1000)
    try:
        output_count = sum(1 for p in settings.output_dir.rglob("*") if p.is_file())
    except Exception:
        output_count = 0

    by_tool = week_summary.get("by_tool") or {}
    top_tools = sorted(by_tool.items(), key=lambda x: x[1].get("calls", 0), reverse=True)[:5]

    return {
        "daily": daily,
        "week": {
            "calls": week_summary["calls"],
            "cost": round(week_summary["cost"], 4),
            "total_tokens": week_summary["total_tokens"],
        },
        "sessions_total": len(sessions),
        "files_total": output_count,
        "top_tools": [{"name": k, "calls": v.get("calls", 0)} for k, v in top_tools],
    }


class ModelRoutingBody(BaseModel):
    enabled: bool
    fast: str = ""
    standard: str = ""
    deep: str = ""


@app.get("/api/model-routing")
def get_model_routing():
    cfg = routing.get_config()
    cfg["available"] = _available_models()
    return cfg


@app.post("/api/model-routing")
def save_model_routing(body: ModelRoutingBody):
    return routing.set_config(
        enabled=body.enabled,
        fast=body.fast,
        standard=body.standard,
        deep=body.deep,
    )


class RagIndexBody(BaseModel):
    folder: str


@app.post("/api/rag/index")
async def rag_index(body: RagIndexBody):
    import asyncio
    from . import rag
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, rag.index_folder, body.folder)
    return result


@app.get("/api/rag/folders")
def rag_folders():
    from . import rag
    return {"folders": rag.get_indexed_folders()}


@app.delete("/api/rag/folder")
async def rag_remove_folder(folder: str = Query(...)):
    from . import rag
    return rag.remove_folder(folder)


@app.get("/api/outputs")
def list_outputs():
    """List generated files in outputs/ directory, newest first."""
    files = _list_files_under(settings.output_dir, source="outputs", url_prefix="/files")
    files.sort(key=lambda f: f["modified"], reverse=True)
    return {"files": files}


@app.get("/api/files")
def list_all_files():
    """List user-visible files from both generated outputs/ and workspace inputs/."""
    files = _list_files_under(settings.output_dir, source="outputs", url_prefix="/files")
    files.extend(_list_files_under(settings.workspace_dir, source="inputs", url_prefix="/workspace-files"))
    files.sort(key=lambda f: f["modified"], reverse=True)
    return {
        "files": files,
        "roots": {
            "outputs": str(settings.output_dir.resolve()),
            "inputs": str(settings.workspace_dir.resolve()),
        },
    }


@app.get("/workspace-files/{path:path}")
def serve_workspace_file(path: str):
    root = settings.workspace_dir.resolve()
    target = (root / path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(404, "file not found")
    if not target.is_file():
        raise HTTPException(404, "file not found")
    media_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type, filename=target.name)


def _list_files_under(root_dir: Path, *, source: str, url_prefix: str) -> list[dict]:
    import os
    base = root_dir.resolve()
    files: list[dict] = []
    try:
        for root, dirs, filenames in os.walk(base):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for fname in sorted(filenames):
                if fname.startswith("."):
                    continue
                fpath = Path(root) / fname
                try:
                    stat = fpath.stat()
                    rel = fpath.relative_to(base)
                    rel_url = str(rel).replace(chr(92), "/")
                    ext = fpath.suffix.lower()
                    files.append({
                        "name": fname,
                        "path": str(rel),
                        "source": source,
                        "url": f"{url_prefix}/{rel_url}",
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                        "type": _output_file_type(ext),
                    })
                except OSError:
                    pass
    except OSError:
        pass
    return files


def _output_file_type(ext: str) -> str:
    if ext in {".md", ".txt"}: return "text"
    if ext in {".html", ".htm"}: return "html"
    if ext in {".xlsx", ".xls", ".csv"}: return "spreadsheet"
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif"}: return "image"
    if ext == ".pdf": return "pdf"
    return "other"


@app.get("/api/history/sessions")
def list_history_sessions(limit: int = Query(50, ge=1, le=200)):
    """List recent conversation sessions with previews."""
    return {"sessions": memory.list_sessions(limit=limit)}


@app.get("/api/history/search")
def search_history(
    q: str = Query(""),
    session_id: str = Query(""),
    limit: int = Query(40, ge=1, le=200),
):
    """Search conversation messages by keyword."""
    return {"messages": memory.search_messages(
        q=q,
        session_id=session_id or None,
        limit=limit,
    )}


@app.get("/healthz")
def health():
    return {"ok": True, "model": settings.model, "route": accounting.current_route()}


@app.get("/api/mobile-status")
def mobile_status():
    """Returns current mobile relay connection state."""
    from .relay_client import is_relay_connected, is_relay_configured
    return {
        "configured": is_relay_configured(),
        "connected": is_relay_connected(),
    }


@app.get("/api/pairing-info")
def pairing_info():
    """Returns the data needed to generate a mobile pairing QR code."""
    relay_url = settings.mobile_relay_url or ""
    device_token = settings.mobile_relay_device_token or ""
    configured = bool(relay_url and device_token)
    return {
        "configured": configured,
        "relay_url": relay_url,
        "device_token": device_token,
    }


@app.get("/api/pairing-qr")
def pairing_qr():
    """Returns the pairing QR code as an SVG image (no CDN needed)."""
    import io
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        raise HTTPException(503, "qrcode package not installed")

    relay_url = settings.mobile_relay_url or ""
    device_token = settings.mobile_relay_device_token or ""
    if not relay_url or not device_token:
        raise HTTPException(404, "Relay not configured")

    import json as _json
    payload = _json.dumps({"relay": relay_url, "token": device_token})
    factory = qrcode.image.svg.SvgImage
    img = qrcode.make(payload, image_factory=factory, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


@app.get("/pair", response_class=HTMLResponse)
def pair_page(lang: str = "zh"):
    relay_url = settings.mobile_relay_url or ""
    device_token = settings.mobile_relay_device_token or ""
    configured = bool(relay_url and device_token)
    is_en = lang.startswith("en")

    if not configured:
        if is_en:
            qr_section = """<div style="color:#f85149;padding:20px;font-size:14px">
                Relay not configured<br>
                <small style="color:#8b949e">Set MOBILE_RELAY_URL and MOBILE_RELAY_ADMIN_SECRET in .env</small>
            </div>"""
            token_text = "Not configured"
        else:
            qr_section = """<div style="color:#f85149;padding:20px;font-size:14px">
                中继服务未配置<br>
                <small style="color:#8b949e">请在 .env 中设置 MOBILE_RELAY_URL 和 MOBILE_RELAY_ADMIN_SECRET</small>
            </div>"""
            token_text = "未配置"
    else:
        qr_section = '<img src="/api/pairing-qr" width="240" height="240" alt="QR Code" style="display:block">'
        token_text = device_token

    if is_en:
        title = "Mobile Pairing"
        subtitle = "Scan the QR code below with the Auctus Agent mobile app<br>Pair once, valid forever"
        steps_title = "Pairing steps:"
        steps = "1. Download the Auctus Agent mobile app<br>2. Open App → tap \"Scan to Pair\"<br>3. Scan the QR code above<br>4. Done! No need to scan again next time"
        back_btn = "Back"
        html_lang = "en"
        page_title = "Auctus Agent — Mobile Pairing"
    else:
        title = "手机配对"
        subtitle = "用 Auctus Agent 手机 App 扫描下方二维码<br>一次配对，永久有效"
        steps_title = "配对步骤："
        steps = "1. 下载 Auctus Agent 手机 App<br>2. 打开 App → 点击「扫码配对」<br>3. 扫描上方二维码<br>4. 完成！之后打开 App 无需重新扫码"
        back_btn = "返回"
        html_lang = "zh"
        page_title = "Auctus Agent — 手机配对"

    return HTMLResponse(content=f"""<!DOCTYPE html>
<html lang="{html_lang}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{page_title}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0d1117; color: #e6edf3; min-height: 100vh;
         display: flex; align-items: center; justify-content: center; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 16px;
           padding: 40px; text-align: center; max-width: 420px; width: 90%; }}
  h1 {{ font-size: 20px; font-weight: 600; margin-bottom: 8px; }}
  .sub {{ color: #8b949e; font-size: 14px; margin-bottom: 32px; line-height: 1.5; }}
  .qr-wrap {{ background: #fff; border-radius: 12px; padding: 16px;
              display: inline-block; margin-bottom: 24px; }}
  .token-box {{ background: #0d1117; border: 1px solid #30363d; border-radius: 8px;
                padding: 10px 14px; font-family: monospace; font-size: 11px;
                color: #8b949e; word-break: break-all; margin-bottom: 24px; }}
  .steps {{ text-align: left; background: #0d1117; border-radius: 8px;
            padding: 16px; font-size: 13px; color: #8b949e; line-height: 2; }}
  .steps b {{ color: #e6edf3; }}
  .btn {{ display: inline-block; margin-top: 20px; padding: 10px 24px;
          background: #238636; color: #fff; border-radius: 8px; text-decoration: none;
          font-size: 14px; }}
</style>
</head>
<body>
<div class="card">
  <h1>{title}</h1>
  <p class="sub">{subtitle}</p>
  <div class="qr-wrap">{qr_section}</div>
  <div class="token-box">Token: {token_text}</div>
  <div class="steps">
    <b>{steps_title}</b><br>
    {steps}
  </div>
  <a href="/" class="btn">{back_btn}</a>
</div>
</body>
</html>""")


def _safe_input_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in name).strip()
    if not safe or safe in {".", ".."}:
        raise HTTPException(400, "invalid filename")
    return safe[:180]


def _auto_pick_model(message: str) -> str:
    """Pick the best relay model based on message keywords. Only used in proxy mode."""
    msg = message.lower()
    code_signals = ["def ", "class ", "import ", "```", "write code", "debug", "fix bug",
                    "function", "script", "程序", "代码", "函数", "bug", "报错", "错误"]
    reasoning_signals = ["analyze", "compare", "research", "explain why", "pros and cons",
                         "分析", "对比", "研究", "推理", "论证", "方案", "架构"]
    if any(s in msg for s in code_signals):
        return "deepseek-coder"
    if any(s in msg for s in reasoning_signals):
        return "gpt-5.4"
    return "deepseek-chat"


def _available_models() -> list[str]:
    route = accounting.current_route()
    if route == "proxy":
        # Models supported by the Auctus relay server, cheapest first.
        relay_models = [
            "deepseek-chat",
            "deepseek-coder",
            "gpt-5.4-nano",
            "gpt-4o-mini",
            "gpt-5.4-mini",
            "gpt-4o",
            "gpt-5.4",
            "gpt-5.5",
            "o3-mini",
            "o3",
            "claude-haiku",
            "claude-sonnet",
        ]
        # Put the current model first if it's not already in the list.
        out = [settings.model] if settings.model not in relay_models else []
        return out + relay_models
    # local / byo: full provider-prefixed list
    models = [
        settings.model,
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
        "gpt-4o",
        "gpt-4o-mini",
        "deepseek/deepseek-chat",
        "ollama/llama3.1",
    ]
    out = []
    for m in models:
        if m and m not in out:
            out.append(m)
    return out


def _store_onboarding_memory_candidates(body: OnboardingIn) -> list[str]:
    if not body.save_profile:
        return []
    ids: list[str] = []
    agent_name = (body.agent_name or "").strip()
    persona = _normalize_persona(body.persona)
    user_intro = (body.user_intro or "").strip()
    if agent_name:
        ids.append(
            memory.remember(
                key="Agent name",
                value=f"用户希望把这个 Agent 叫做：{agent_name}",
                tags=["onboarding", "agent_name"],
                type="preference",
                importance=3,
            )
        )
    if persona != "professional":
        ids.append(
            memory.remember(
                key="Agent persona",
                value=f"用户希望 Agent 使用的交流风格：{_persona_label(persona)}",
                tags=["onboarding", "persona"],
                type="preference",
                importance=3,
            )
        )
    if user_intro:
        ids.append(
            memory.remember(
                key="User self introduction",
                value=user_intro,
                tags=["onboarding", "user_profile"],
                type="preference",
                importance=4,
            )
        )
    return ids


def _normalize_persona(persona: Optional[str]) -> str:
    value = (persona or "professional").strip()
    return value if value in _PERSONA_LABELS else "professional"


def _normalize_language(language: Optional[str]) -> str:
    value = (language or "en").strip().lower()
    return value if value in {"zh", "en"} else "en"


def _normalize_hosted_region(region: Optional[str]) -> str:
    value = (region or "auto").strip().lower()
    return value if value in {"auto", "global", "cn"} else "auto"


def _hosted_error(response: httpx.Response, fallback: str) -> str:
    try:
        data = response.json()
    except ValueError:
        return fallback
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    return fallback


def _detect_region_from_client(body: HostedRegionDetectIn) -> str:
    timezone = (body.timezone or "").lower()
    locale = (body.locale or "").lower()
    languages = " ".join((body.languages or [])).lower()
    text = f"{timezone} {locale} {languages}"
    if "shanghai" in timezone or "chongqing" in timezone or "urumqi" in timezone:
        return "cn"
    if "zh-cn" in text or "hans-cn" in text:
        return "cn"
    return "global"


def _available_hosted_regions() -> list[dict[str, str]]:
    return [
        {"region": "auto", "label": "自动选择区域"},
        {"region": "global", "label": "海外 Vercel"},
        {"region": "cn", "label": "国内阿里云"},
    ]


def _hosted_region_label(region: Optional[str]) -> str:
    labels = {
        "auto": "自动选择区域",
        "global": "海外 Vercel",
        "cn": "国内阿里云",
    }
    return labels.get(_normalize_hosted_region(region), labels["auto"])


def _normalize_permission_scope(scope: Optional[str]) -> str:
    value = (scope or DEFAULT_PERMISSION_SCOPE).strip().lower()
    return value if value in {"workspace", "full_computer"} else DEFAULT_PERMISSION_SCOPE


def _normalize_terminal_access(access: Optional[str]) -> str:
    value = (access or DEFAULT_TERMINAL_ACCESS).strip().lower()
    return value if value in {"disabled", "enabled"} else DEFAULT_TERMINAL_ACCESS


def _normalize_calendar_access(access: Optional[str]) -> str:
    value = (access or "disabled").strip().lower()
    return value if value in {"disabled", "enabled"} else "disabled"


def _normalize_terminal_permission(permission: Optional[str]) -> str:
    value = (permission or "").strip().lower()
    return value if value in {"once", "always", "no"} else ""


def _normalize_calendar_permission(permission: Optional[str]) -> str:
    value = (permission or "").strip().lower()
    return value if value in {"once", "always", "no"} else ""


def _normalize_file_permission(permission: Optional[str]) -> str:
    value = (permission or "").strip().lower()
    return value if value in {"once", "always", "no"} else ""


def _available_permission_scopes() -> list[dict[str, str]]:
    return [
        {"scope": "workspace", "label": "仅授权文件夹"},
        {"scope": "full_computer", "label": "整台电脑"},
    ]


def _available_terminal_access() -> list[dict[str, str]]:
    return [
        {"access": "disabled", "label": "关闭终端命令"},
        {"access": "enabled", "label": "允许终端命令"},
    ]


def _available_calendar_access() -> list[dict[str, str]]:
    return [
        {"access": "disabled", "label": "不允许访问日历"},
        {"access": "enabled", "label": "允许访问日历"},
    ]


def _permission_scope_label(scope: Optional[str]) -> str:
    labels = {
        "workspace": "仅授权文件夹",
        "full_computer": "整台电脑",
    }
    return labels.get(_normalize_permission_scope(scope), labels[DEFAULT_PERMISSION_SCOPE])


def _terminal_access_label(access: Optional[str]) -> str:
    labels = {
        "disabled": "关闭终端命令",
        "enabled": "允许终端命令",
    }
    return labels.get(_normalize_terminal_access(access), labels[DEFAULT_TERMINAL_ACCESS])


def _calendar_access_label(access: Optional[str]) -> str:
    labels = {
        "disabled": "不允许访问日历",
        "enabled": "允许访问日历",
    }
    return labels.get(_normalize_calendar_access(access), labels["disabled"])


_TERMINAL_INTENT_KEYWORDS = (
    "终端",
    "命令",
    "shell",
    "terminal",
    "command",
    "执行",
    "运行",
    "打开",
    "启动",
    "跑一下",
    "跑测试",
    "打开网页",
    "打开html",
    # 文件/系统操作（例如删除文件）也需要弹权限
    "删除",
    "删掉",
    "移到废纸篓",
    "废纸篓",
    "回收站",
    "trash",
    "rm ",
    "mv ",
)
_COMMAND_LIKE_RE = re.compile(
    r"(^|\s)(open|npm|pnpm|yarn|pip|pytest|python3?|node|git|ls|pwd|cat|mkdir|touch|curl|brew|uvicorn|docker)\b",
    re.IGNORECASE,
)


def _should_request_terminal_permission(message: str, terminal_permission: str) -> bool:
    if terminal_permission in {"once", "always", "no"}:
        return False
    state = accounting.get_setup_state()
    if _normalize_terminal_access(state.get("terminal_access")) == "enabled":
        return False
    text = message.strip().lower()
    if not text:
        return False
    return any(keyword in text for keyword in _TERMINAL_INTENT_KEYWORDS) or bool(_COMMAND_LIKE_RE.search(text))


_CALENDAR_INTENT_KEYWORDS = (
    "日历",
    "行程",
    "提醒",
    "提醒事项",
    "calendar",
    "reminder",
    "提醒我",
)


def _should_request_calendar_permission(message: str, calendar_permission: str) -> bool:
    if calendar_permission in {"once", "always", "no"}:
        return False
    state = accounting.get_setup_state()
    if _normalize_calendar_access(state.get("calendar_access")) == "enabled":
        return False
    text = message.strip().lower()
    if not text:
        return False
    return any(keyword in text for keyword in _CALENDAR_INTENT_KEYWORDS)


_FILE_INTENT_KEYWORDS = (
    "桌面",
    "desktop",
    "下载",
    "downloads",
    "文档",
    "documents",
    "图片",
    "pictures",
    "音乐",
    "music",
    "视频",
    "movies",
    "/users/",
    "c:\\",
    "d:\\",
)


def _should_request_file_permission(message: str, file_permission: str) -> bool:
    if file_permission in {"once", "always", "no"}:
        return False
    state = accounting.get_setup_state()
    if _normalize_permission_scope(state.get("permission_scope")) == "full_computer":
        return False
    text = message.strip().lower()
    if not text:
        return False
    # 只要用户明显在谈“系统路径/常见目录”，就先问一次权限，让用户选择
    return any(keyword in text for keyword in _FILE_INTENT_KEYWORDS)


_PERSONA_LABELS = {
    "professional": "专业简洁",
    "cool_sister": "高冷御姐",
    "warm_uncle": "知心大叔",
    "reliable_bro": "可靠小哥",
    "cheerful_girl": "元气萌妹",
}


def _persona_label(persona: str) -> str:
    return _PERSONA_LABELS.get(persona, _PERSONA_LABELS["professional"])


def _restore_route_from_onboarding_mode(mode: str) -> None:
    normalized = (mode or "").strip().lower()
    if normalized == "own_api":
        provider = accounting.provider_for_model(settings.model) or ""
        if _settings_api_key_for_provider(provider) and not accounting.get_api_key(provider):
            accounting.set_route("local")
            return
    route_by_mode = {
        "own_api": "byo",
        "hosted_api": _hosted_api_route(),
        "local_model": "local",
    }
    route = route_by_mode.get(normalized)
    if route:
        accounting.set_route(route)


def _restore_workspace_from_state(state: Optional[dict[str, str]] = None) -> None:
    data = state or accounting.get_setup_state()
    path = data.get("workspace_dir")
    if not path:
        return
    target = Path(path).expanduser().resolve()
    if target.exists() and target.is_dir():
        settings.workspace_dir = target


def _set_workspace_path(path: Optional[str]) -> Path:
    raw = (path or "").strip()
    if not raw:
        raise HTTPException(400, "empty workspace path")
    target = Path(raw).expanduser().resolve()
    if not target.exists() or not target.is_dir():
        raise HTTPException(400, "workspace path must be an existing folder")
    settings.workspace_dir = target
    return target


def _is_default_workspace(path: Path) -> bool:
    default_workspace = (Path(__file__).resolve().parents[1] / "inputs").resolve()
    return path.resolve() == default_workspace


def _version_parts(version: str) -> tuple[int, ...]:
    raw = (version or "").strip().lstrip("v")
    parts: list[int] = []
    for piece in raw.split("."):
        match = re.match(r"(\d+)", piece)
        if not match:
            break
        parts.append(int(match.group(1)))
    return tuple(parts) if parts else (0,)


def _is_newer_version(candidate: str, current: str) -> bool:
    candidate_parts = _version_parts(candidate)
    current_parts = _version_parts(current)
    length = max(len(candidate_parts), len(current_parts))
    candidate_parts = candidate_parts + (0,) * (length - len(candidate_parts))
    current_parts = current_parts + (0,) * (length - len(current_parts))
    return candidate_parts > current_parts


def _hosted_api_route() -> str:
    if settings.proxy_base_url:
        return "proxy"
    if accounting.get_api_key("auctus_hosted"):
        return "proxy"
    return "local"


def _settings_api_key_for_provider(provider: str) -> Optional[str]:
    provider = (provider or "").strip().lower()
    if provider == "anthropic":
        return settings.anthropic_api_key
    if provider == "openai":
        return settings.openai_api_key
    if provider == "deepseek":
        return settings.deepseek_api_key
    if provider == "dashscope":
        return settings.dashscope_api_key
    return None


def _friendly_runtime_error(message: str) -> str:
    language = _normalize_language(accounting.get_setup_state().get("system_language"))
    lowered = message.lower()
    if "Authentication" in message or "401" in message or "Unauthorized" in message or "invalid api key" in lowered:
        if language == "en":
            return "Model API authentication failed. Check the API key for the current model, or switch to Own API and save a valid key."
        return "模型 API 认证失败。请检查当前模型对应的 API key，或切换到“自己的 API”后重新保存一个有效 key。"
    if (
        "deepseekexception" in lowered
        or "nodename nor servname" in lowered
        or "failed to connect" in lowered
        or "connection error" in lowered
        or "connectionerror" in lowered
        or "timeout" in lowered
    ):
        if language == "en":
            return "Cannot connect to DeepSeek right now. Check internet access, VPN/proxy, or try again in a minute."
        return "当前无法连接 DeepSeek。请检查网络、代理/VPN，或稍后再试。"
    if "PROXY_BASE_URL" in message:
        if language == "en":
            return (
                "Auctus Hosted API cloud Relay is not configured yet. "
                "For local preview, configure the platform model API key in .env, or switch to Own API."
            )
        return (
            "Auctus 托管 API 的云端 Relay 还没有配置。"
            "本地预览请先在 .env 配置平台模型 API key，或切换到“自己的 API”。"
        )
    if "requires a saved" in message:
        if language == "en":
            return "The current route is BYO Key, but no API key was found for this model provider. Save a key in Settings, or run Setup again."
        return "当前是 BYO Key 路由，但没有找到对应模型服务商的 API key。请在右侧保存 key，或重新运行首次设置。"
    return message
def _validate_api_key_live(*, model: str, api_key: str) -> None:
    litellm.completion(
        model=model,
        api_key=api_key,
        messages=[{"role": "user", "content": "Reply with ok."}],
        max_tokens=3,
        temperature=0,
    )


def _file_url(path: str) -> str:
    p = Path(path).resolve()
    root = settings.output_dir.resolve()
    try:
        rel = p.relative_to(root)
    except ValueError:
        rel = Path(p.name)
    return "/files/" + "/".join(rel.parts)
