"""
Relay client — connects desktop agent to the cloud relay server.
Reads MOBILE_RELAY_URL and MOBILE_RELAY_ADMIN_SECRET from config. If not set, does nothing.

Message protocol (JSON):
  Mobile → Desktop: {"type": "message", "session_id": "...", "content": "..."}
  Desktop → Mobile: {"type": "reply",   "session_id": "...", "content": "..."}
                  | {"type": "error",   "session_id": "...", "content": "..."}
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import urllib.request
from urllib.parse import urlparse, urlunparse
from typing import Callable, Optional

log = logging.getLogger(__name__)

RECONNECT_DELAY = 5  # seconds between reconnect attempts
HEALTH_CHECK_INTERVAL = 30  # seconds between relay registry checks
HEALTH_CHECK_TIMEOUT = 8  # seconds for /relay/health
DESKTOP_HEARTBEAT_INTERVAL = 20  # seconds between app-level relay pings


def _health_url_for(relay_url: str) -> str:
    parsed = urlparse(relay_url.rstrip("/"))
    scheme = "https" if parsed.scheme == "wss" else "http"
    base = urlunparse((scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))
    return f"{base}/relay/health"


class RelayClient:
    """
    Runs an async WebSocket loop in a dedicated background thread.
    Incoming messages from mobile are dispatched to `on_message`.
    """

    def __init__(
        self,
        relay_url: str,
        admin_secret: str,
        device_token: str,
        on_message: Callable[[str, str], str],
    ) -> None:
        """
        relay_url    : base WebSocket URL, e.g. "ws://120.24.223.0"
        admin_secret : server-side secret, baked into app bundle
        device_token : this desktop's unique token, shown as QR code
        on_message   : sync callable(session_id, content) → reply string
        """
        self._ws_url = (
            f"{relay_url.rstrip('/')}/relay/ws/desktop"
            f"?admin_secret={admin_secret}&device_token={device_token}"
        )
        self._health_url = _health_url_for(relay_url)
        self._device_token_prefix = device_token[:8]
        self._on_message = on_message
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws = None
        self._running = False

    # ── public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the relay loop in a daemon thread."""
        self._running = True
        t = threading.Thread(target=self._run_loop, daemon=True, name="relay-client")
        t.start()
        log.info("[Relay] Client started, connecting to %s", self._ws_url)

    def stop(self) -> None:
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    # ── internal ──────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_loop())
        finally:
            self._loop.close()

    async def _connect_loop(self) -> None:
        try:
            import websockets
        except ImportError:
            log.error("[Relay] 'websockets' package not installed. Run: pip install websockets")
            return

        while self._running:
            try:
                async with websockets.connect(
                    self._ws_url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    log.info("[Relay] Connected to relay server")
                    await self._serve_connection(ws)
            except Exception as e:
                log.warning("[Relay] Disconnected: %s — retrying in %ds", e, RECONNECT_DELAY)
                self._ws = None
                await asyncio.sleep(RECONNECT_DELAY)

    async def _serve_connection(self, ws) -> None:
        recv_task = asyncio.create_task(self._recv_loop(ws))
        health_task = asyncio.create_task(self._health_loop(ws))
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))
        tasks = {recv_task, health_task, heartbeat_task}
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                exc = task.exception()
                if exc:
                    raise exc
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            self._ws = None

    async def _health_loop(self, ws) -> None:
        while self._running:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)
            if not await asyncio.to_thread(self._is_registered_online):
                log.warning("[Relay] Health check says desktop is offline; reconnecting")
                await ws.close()
                raise ConnectionError("relay registry lost desktop connection")

    async def _heartbeat_loop(self, ws) -> None:
        while self._running:
            await ws.send(json.dumps({"type": "desktop_ping"}))
            await asyncio.sleep(DESKTOP_HEARTBEAT_INTERVAL)

    def _is_registered_online(self) -> bool:
        try:
            req = urllib.request.Request(self._health_url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=HEALTH_CHECK_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            log.warning("[Relay] Health check failed: %s", e)
            return False

        device = (data.get("devices") or {}).get(self._device_token_prefix)
        return bool(device and device.get("desktop_online"))

    async def _recv_loop(self, ws) -> None:
        async for raw in ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if data.get("type") != "message":
                continue

            session_id = data.get("session_id", "mobile")
            content = data.get("content", "").strip()
            if not content:
                continue

            log.info("[Relay] Message from mobile (session=%s): %s", session_id, content[:80])

            # Call the agent's chat handler in a thread (it's synchronous/blocking)
            try:
                reply = await asyncio.get_event_loop().run_in_executor(
                    None, self._on_message, session_id, content
                )
                await ws.send(json.dumps({
                    "type": "reply",
                    "session_id": session_id,
                    "content": reply,
                }))
            except Exception as e:
                log.error("[Relay] Error processing message: %s", e)
                await ws.send(json.dumps({
                    "type": "error",
                    "session_id": session_id,
                    "content": f"Agent error: {e}",
                }))


# ── module-level singleton ────────────────────────────────────────────────────

_client: Optional[RelayClient] = None


def start_relay_client(on_message: Callable[[str, str], str]) -> None:
    """
    Call once at server startup. Reads mobile relay settings.
    Does nothing if either is not configured.
    """
    global _client

    from .config import settings

    relay_url = settings.mobile_relay_url or ""
    admin_secret = settings.mobile_relay_admin_secret or ""
    device_token = settings.mobile_relay_device_token or ""

    if not relay_url or not admin_secret or not device_token:
        log.info("[Relay] Mobile relay not fully configured — relay disabled")
        return

    _client = RelayClient(relay_url, admin_secret, device_token, on_message)
    _client.start()


def stop_relay_client() -> None:
    if _client:
        _client.stop()


def is_relay_connected() -> bool:
    """Return True if the relay WebSocket is currently open."""
    return _client is not None and _client._ws is not None


def is_relay_configured() -> bool:
    """Return True if relay credentials are present in settings."""
    from .config import settings
    return bool(
        settings.mobile_relay_url
        and settings.mobile_relay_admin_secret
        and settings.mobile_relay_device_token
    )
