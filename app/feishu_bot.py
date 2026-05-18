"""Feishu/Lark long-connection bot integration."""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Optional

from . import accounting, agent

log = logging.getLogger("feishu")
_BOT_THREAD: Optional[threading.Thread] = None
_BOT_CONFIG_KEY: str = ""


def _text_from_message_content(content: str) -> str:
    try:
        data = json.loads(content or "{}")
    except Exception:
        return ""
    text = data.get("text") or ""
    return str(text).strip()


def _send_reply(app_id: str, app_secret: str, chat_id: str, reply: str, domain: str) -> None:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

    builder = lark.Client.builder().app_id(app_id).app_secret(app_secret)
    if domain == "lark" and hasattr(lark, "LARK_DOMAIN"):
        builder = builder.domain(lark.LARK_DOMAIN)
    elif hasattr(lark, "FEISHU_DOMAIN"):
        builder = builder.domain(lark.FEISHU_DOMAIN)
    client = builder.build()

    request = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": reply}, ensure_ascii=False))
            .build()
        )
        .build()
    )
    response = client.im.v1.message.create(request)
    if not response.success():
        log.error("Feishu reply failed: code=%s msg=%s", response.code, response.msg)


def _handle_message_event(data: Any, app_id: str, app_secret: str, domain: str) -> None:
    event = getattr(data, "event", None)
    message = getattr(event, "message", None)
    sender = getattr(event, "sender", None)
    if not message:
        return

    text = _text_from_message_content(getattr(message, "content", ""))
    if not text:
        return

    sender_id_obj = getattr(sender, "sender_id", None)
    sender_id = (
        getattr(sender_id_obj, "open_id", None)
        or getattr(sender_id_obj, "user_id", None)
        or "feishu_user"
    )
    chat_id = getattr(message, "chat_id", "")
    session_id = f"{domain}-{sender_id}"
    label = "Lark" if domain == "lark" else "飞书"

    accounting.add_telegram_inbox_message("in", text, from_user=f"{label}:{sender_id}")

    def _reply() -> None:
        try:
            result = agent.chat(session_id, text)
            reply = result.get("reply", "")
            if not reply:
                return
            accounting.add_telegram_inbox_message("out", reply, from_user="agent")
            if chat_id:
                _send_reply(app_id, app_secret, chat_id, reply, domain)
        except Exception:
            log.exception("%s message task failed", label)

    threading.Thread(target=_reply, daemon=True, name=f"{domain}-reply").start()


def run_in_thread() -> None:
    """Start the Feishu/Lark WebSocket long-connection listener."""
    global _BOT_THREAD, _BOT_CONFIG_KEY

    state = accounting.get_setup_state()
    app_id = (state.get("feishu_app_id") or "").strip()
    app_secret = (state.get("feishu_app_secret") or "").strip()
    domain = (state.get("feishu_domain") or "feishu").strip().lower()
    mode = (state.get("feishu_receive_mode") or "websocket").strip().lower()
    if not app_id or not app_secret:
        log.info("未配置飞书 App ID/App Secret，跳过飞书长连接启动")
        return
    if mode != "websocket":
        log.info("飞书接收模式为 %s，跳过飞书长连接启动", mode)
        return

    config_key = f"{domain}:{app_id}"
    if _BOT_THREAD and _BOT_THREAD.is_alive():
        if _BOT_CONFIG_KEY == config_key:
            log.info("飞书/Lark 长连接线程已在运行")
        else:
            log.info("飞书/Lark 配置已更新，请重启 Auctus Agent 以切换长连接配置")
        return

    def _thread_main() -> None:
        try:
            import lark_oapi as lark

            event_handler = (
                lark.EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(
                    lambda data: _handle_message_event(data, app_id, app_secret, domain)
                )
                .build()
            )
            kwargs = {
                "app_id": app_id,
                "app_secret": app_secret,
                "event_handler": event_handler,
                "log_level": lark.LogLevel.INFO,
                "auto_reconnect": True,
            }
            if domain == "lark" and hasattr(lark, "LARK_DOMAIN"):
                kwargs["domain"] = lark.LARK_DOMAIN
            elif hasattr(lark, "FEISHU_DOMAIN"):
                kwargs["domain"] = lark.FEISHU_DOMAIN
            ws_client = lark.ws.Client(**kwargs)
            log.info("%s 长连接启动中", "Lark" if domain == "lark" else "飞书")
            ws_client.start()
        except ModuleNotFoundError:
            log.exception("缺少 lark-oapi 依赖，无法启动飞书/Lark 长连接")
        except Exception:
            log.exception("飞书/Lark 长连接线程异常退出")

    _BOT_CONFIG_KEY = config_key
    _BOT_THREAD = threading.Thread(target=_thread_main, daemon=True, name="feishu-bot")
    _BOT_THREAD.start()
    log.info("飞书/Lark 长连接线程已启动")
