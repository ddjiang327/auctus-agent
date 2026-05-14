"""Cloud Relay foundation: auth, quota, LLM proxy, Telegram webhook, tunnel hub."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from . import accounting, llm
from .config import settings


router = APIRouter(prefix="/relay", tags=["relay"])


class AuthVerifyIn(BaseModel):
    token: Optional[str] = None


class LlmChatIn(BaseModel):
    messages: list[dict[str, Any]]
    model: Optional[str] = None
    temperature: float = 0.2


class TunnelCommandIn(BaseModel):
    type: str
    payload: dict[str, Any] = {}


class TunnelHub:
    def __init__(self) -> None:
        self._clients: dict[str, set[WebSocket]] = {}
        self._queued: dict[str, list[dict[str, Any]]] = {}

    async def connect(self, desktop_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.setdefault(desktop_id, set()).add(websocket)
        for item in self._queued.pop(desktop_id, []):
            await websocket.send_json(item)

    def disconnect(self, desktop_id: str, websocket: WebSocket) -> None:
        clients = self._clients.get(desktop_id)
        if not clients:
            return
        clients.discard(websocket)
        if not clients:
            self._clients.pop(desktop_id, None)

    async def send_command(self, desktop_id: str, command: dict[str, Any]) -> dict:
        clients = list(self._clients.get(desktop_id, set()))
        if not clients:
            self._queued.setdefault(desktop_id, []).append(command)
            return {"queued": True, "delivered": 0, "command_id": command["id"]}
        delivered = 0
        for ws in clients:
            await ws.send_json(command)
            delivered += 1
        return {"queued": False, "delivered": delivered, "command_id": command["id"]}

    def status(self) -> dict:
        return {
            "connected": {desktop_id: len(clients) for desktop_id, clients in self._clients.items()},
            "queued": {desktop_id: len(items) for desktop_id, items in self._queued.items()},
        }

    def reset(self) -> None:
        self._clients.clear()
        self._queued.clear()


hub = TunnelHub()
_risk_alerts: list[dict[str, Any]] = []


@router.post("/auth/verify")
def auth_verify(body: AuthVerifyIn, authorization: Optional[str] = Header(default=None)) -> dict:
    token = body.token or _bearer_token(authorization)
    return {"ok": _token_is_valid(token)}


@router.get("/quota")
def quota(authorization: Optional[str] = Header(default=None)) -> dict:
    _require_auth(authorization)
    return quota_state()


@router.get("/risk")
def risk(authorization: Optional[str] = Header(default=None)) -> dict:
    _require_auth(authorization)
    return {"quota": quota_state(), "alerts": list(_risk_alerts[-20:])}


def quota_state() -> dict:
    monthly = accounting.usage_summary(user_id=accounting.LOCAL_USER_ID, since=_month_start_ts())
    per_minute = accounting.usage_summary(user_id=accounting.LOCAL_USER_ID, since=time.time() - 60)
    daily = accounting.usage_summary(user_id=accounting.LOCAL_USER_ID, since=_day_start_ts())

    used = int(monthly["total_tokens"])
    limit = int(settings.relay_monthly_token_limit)
    minute_tokens = int(per_minute["total_tokens"])
    minute_limit = int(settings.relay_rate_limit_per_minute)
    daily_cost = float(daily["cost"])
    daily_cap = float(settings.relay_daily_cost_cap)
    spike_threshold = int(settings.relay_spike_token_threshold)

    reasons: list[str] = []
    if used >= limit:
        reasons.append("monthly_quota_exceeded")
    if minute_tokens >= minute_limit:
        reasons.append("rate_limit_exceeded")
    if daily_cost >= daily_cap:
        reasons.append("daily_cost_cap_exceeded")
    if minute_tokens >= spike_threshold:
        reasons.append("spike_detected")
        _record_alert("spike_detected", {"minute_tokens": minute_tokens, "threshold": spike_threshold})

    return {
        "user_id": accounting.LOCAL_USER_ID,
        "limit_tokens": limit,
        "used_tokens": used,
        "remaining_tokens": max(limit - used, 0),
        "rate_limit_per_minute": minute_limit,
        "minute_tokens": minute_tokens,
        "daily_cost_cap": daily_cap,
        "daily_cost": daily_cost,
        "spike_token_threshold": spike_threshold,
        "allowed": not reasons,
        "reasons": reasons,
        "byo_guidance": _byo_guidance() if reasons else "",
    }


@router.post("/llm/chat")
def llm_chat(body: LlmChatIn, authorization: Optional[str] = Header(default=None)) -> dict:
    _require_auth(authorization)
    state = quota_state()
    if not state["allowed"]:
        raise HTTPException(429, {"error": "quota or risk limit exceeded", **state})
    old_model = settings.model
    if body.model:
        settings.model = body.model
    try:
        with accounting.usage_context(
            user_id=accounting.LOCAL_USER_ID,
            session_id=f"relay-{uuid.uuid4().hex[:8]}",
            route="relay_proxy",
        ):
            return llm.chat_completion(messages=body.messages, temperature=body.temperature)
    finally:
        settings.model = old_model


@router.post("/telegram/webhook")
async def telegram_webhook(update: dict[str, Any], authorization: Optional[str] = Header(default=None)) -> dict:
    _require_auth(authorization)
    command = {
        "id": f"telegram-{uuid.uuid4().hex[:12]}",
        "type": "telegram_update",
        "payload": update,
    }
    result = await hub.send_command(settings.relay_desktop_id, command)
    return {"ok": True, **result}


@router.websocket("/tunnel/{desktop_id}")
async def tunnel(websocket: WebSocket, desktop_id: str, token: Optional[str] = None) -> None:
    if not _token_is_valid(token):
        await websocket.close(code=1008)
        return
    await hub.connect(desktop_id, websocket)
    try:
        while True:
            message = await websocket.receive_json()
            if message.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            else:
                await websocket.send_json({"type": "ack", "received": message.get("id") or message.get("type")})
    except WebSocketDisconnect:
        hub.disconnect(desktop_id, websocket)


@router.post("/tunnel/{desktop_id}/commands")
async def send_tunnel_command(
    desktop_id: str,
    body: TunnelCommandIn,
    authorization: Optional[str] = Header(default=None),
) -> dict:
    _require_auth(authorization)
    command = {"id": f"cmd-{uuid.uuid4().hex[:12]}", "type": body.type, "payload": body.payload}
    return await hub.send_command(desktop_id, command)


@router.get("/tunnel/status")
def tunnel_status(authorization: Optional[str] = Header(default=None)) -> dict:
    _require_auth(authorization)
    return hub.status()


def auth_headers() -> dict[str, str]:
    if not settings.relay_shared_token:
        return {}
    return {"Authorization": f"Bearer {settings.relay_shared_token}"}


def _require_auth(authorization: Optional[str]) -> None:
    token = _bearer_token(authorization)
    if not _token_is_valid(token):
        raise HTTPException(401, "invalid relay token")


def _token_is_valid(token: Optional[str]) -> bool:
    expected = settings.relay_shared_token
    if not expected:
        return True
    return token == expected


def _bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    prefix = "Bearer "
    if authorization.startswith(prefix):
        return authorization[len(prefix):]
    return authorization


def _month_start_ts() -> float:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc).timestamp()


def _day_start_ts() -> float:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp()


def _record_alert(kind: str, detail: dict[str, Any]) -> None:
    now = time.time()
    if _risk_alerts and _risk_alerts[-1]["kind"] == kind and now - _risk_alerts[-1]["ts"] < 60:
        return
    _risk_alerts.append({"kind": kind, "detail": detail, "ts": now})
    del _risk_alerts[:-50]


def _byo_guidance() -> str:
    return (
        "Relay quota is currently limited. Switch to BYO route and add your own provider key "
        "with `agent.py route byo` and `agent.py api-key set --provider <provider>`."
    )


async def connect_desktop(url: str, token: Optional[str] = None) -> None:
    """Connect this desktop agent to a Relay WebSocket and ack commands."""
    import websockets

    full_url = _with_token(url, token)
    async with websockets.connect(full_url) as websocket:
        await websocket.send('{"type":"ping"}')
        async for raw in websocket:
            await websocket.send(json.dumps({"type": "ack", "received": raw}))


def _with_token(url: str, token: Optional[str]) -> str:
    if not token:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}token={token}"
