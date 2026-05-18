"""Telegram bot 入口 —— 把手机变成 Agent 的远程终端。

启动：python -m app.telegram_bot
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters,
)

from . import agent, server, accounting
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tg")
_PENDING_PERMISSIONS: dict[int, dict] = {}
_BOT_THREAD: Optional["threading.Thread"] = None


def _auto_bind_first_telegram_user(user_id: int) -> bool:
    """Bind the first /start sender when no allowlist exists yet."""
    if settings.telegram_allowed_user_ids.strip():
        return False

    value = str(user_id)
    env_path = Path(".env")
    try:
        from dotenv import set_key as _set_key
        env_path.touch()
        _set_key(str(env_path), "TELEGRAM_ALLOWED_USER_IDS", value)
    except Exception:
        log.exception("Failed to auto-bind Telegram user id")
        return False

    settings.telegram_allowed_user_ids = value
    log.info("Telegram user %s auto-bound as first allowed user", value)
    return True


def _auth_ok(user_id: Optional[int]) -> bool:
    allow = settings.allowed_telegram_ids()
    if not allow:
        # 没配白名单时也别全开 —— 提示用户限定
        log.warning("TELEGRAM_ALLOWED_USER_IDS 未设置，所有人都能用 bot！")
        return True
    return user_id in allow


def _safe_input_filename(filename: str) -> str:
    name = Path(filename or "upload.bin").name.strip()
    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in name).strip()
    return (safe[:180] or "upload.bin")


def _task_for_uploaded_file(filename: str, caption: str) -> str:
    task = caption.strip() or "请读取这个文件，概括主要内容，并告诉我可以生成哪些交付物。"
    return f"请读取文件 `{filename}`（在 inputs/ 目录下），然后完成以下任务：\n\n{task}"


async def _reply_agent_result(message, result: dict) -> None:
    reply = result.get("reply") or "(无回复)"
    await message.reply_text(reply)

    for path in result.get("files", []):
        p = _telegram_file_path(path)
        if p.exists():
            with p.open("rb") as fh:
                await message.reply_document(document=fh, filename=p.name)


def _telegram_file_path(path: str) -> Path:
    if path.startswith("/files/"):
        rel = path.removeprefix("/files/").lstrip("/")
        return (settings.output_dir / rel).resolve()
    return Path(path)


def _permission_choice(text: str) -> Optional[str]:
    value = (text or "").strip().lower()
    if value in {"允许一次", "本次允许", "一次", "once", "/once"}:
        return "once"
    if value in {"始终允许", "总是允许", "以后都允许", "always", "/always"}:
        return "always"
    if value in {"拒绝", "不允许", "取消", "no", "deny", "/no"}:
        return "no"
    return None


def _permission_reply_text(request: dict) -> str:
    kind = request.get("type", "permission")
    label = {
        "terminal": "终端命令权限",
        "files": "文件访问权限",
        "calendar": "日历/提醒事项权限",
    }.get(kind, "权限")
    return (
        f"需要{label}：{request.get('message', '')}\n\n"
        "请直接回复：\n"
        "- 允许一次\n"
        "- 始终允许\n"
        "- 拒绝"
    )


def _run_chat_for_telegram(uid: int, text: str, choice: Optional[str] = None) -> dict:
    session_id = f"tg-{uid}"
    pending = _PENDING_PERMISSIONS.get(uid)
    payload = {"session_id": session_id, "message": text}
    if pending and choice:
        payload["message"] = pending["message"]
        if pending["type"] == "terminal":
            payload["terminal_permission"] = choice
        elif pending["type"] == "files":
            payload["file_permission"] = choice
        elif pending["type"] == "calendar":
            payload["calendar_permission"] = choice
        _PENDING_PERMISSIONS.pop(uid, None)

    accounting.add_telegram_inbox_message("in", text, from_user=str(uid))

    out = server.chat(server.ChatIn(**payload))
    data = out.model_dump() if hasattr(out, "model_dump") else out.dict()
    if data.get("permission_request"):
        req = data["permission_request"]
        _PENDING_PERMISSIONS[uid] = {"message": text, "type": req.get("type", "")}
        reply = _permission_reply_text(req)
        accounting.add_telegram_inbox_message("out", reply, from_user="agent")
        return {"reply": reply, "files": [], "permission_request": req}

    reply = data.get("reply", "")
    if reply:
        accounting.add_telegram_inbox_message("out", reply, from_user="agent")
    return data


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if _auto_bind_first_telegram_user(uid):
        await update.message.reply_text(
            f"你好！我是你的秘书 Agent。\n已自动绑定你的 Telegram ID：{uid}\n"
            "现在可以直接发任务给我。"
        )
        return

    if _auth_ok(uid):
        await update.message.reply_text(
            f"你好！我是你的秘书 Agent。\n你的 Telegram ID 是 {uid}\n"
            "你已经可以直接发任务给我。"
        )
    else:
        await update.message.reply_text(
            f"你好！我是你的秘书 Agent。\n你的 Telegram ID 是 {uid}\n"
            "当前 Bot 已绑定其他允许用户，请在 Auctus 设置里添加这个 ID 后再使用。"
        )


async def handle_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _auth_ok(uid):
        await update.message.reply_text("未授权。")
        return

    text = update.message.text or ""
    session_id = f"tg-{uid}"
    await update.message.chat.send_action("typing")

    try:
        choice = _permission_choice(text)
        if _PENDING_PERMISSIONS.get(uid) and choice is None:
            await update.message.reply_text("上一条任务正在等权限确认。请回复：允许一次 / 始终允许 / 拒绝。")
            return
        result = await asyncio.to_thread(_run_chat_for_telegram, uid, text, choice)
        await _reply_agent_result(update.message, result)
    except Exception as e:
        log.exception("Telegram text task failed")
        await update.message.reply_text(f"执行失败：{type(e).__name__}: {e}")


async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _auth_ok(uid):
        await update.message.reply_text("未授权。")
        return

    document = update.message.document
    if document is None:
        await update.message.reply_text("没有收到文件。")
        return

    filename = _safe_input_filename(document.file_name or f"telegram_{document.file_unique_id}")
    target = (settings.workspace_dir / filename).resolve()
    workspace = settings.workspace_dir.resolve()
    try:
        target.relative_to(workspace)
    except ValueError:
        await update.message.reply_text("文件名无效。")
        return

    await update.message.chat.send_action("upload_document")
    try:
        tg_file = await document.get_file()
        await tg_file.download_to_drive(custom_path=str(target))
        await update.message.reply_text(f"已保存文件：{filename}")

        caption = update.message.caption or ""
        if caption.strip():
            await update.message.chat.send_action("typing")
            prompt = _task_for_uploaded_file(filename, caption)
            result = await asyncio.to_thread(agent.chat, f"tg-{uid}", prompt)
            await _reply_agent_result(update.message, result)
    except Exception as e:
        log.exception("Telegram file task failed")
        await update.message.reply_text(f"文件处理失败：{type(e).__name__}: {e}")


def main(*, stop_signals=None) -> None:
    if not settings.telegram_bot_token:
        raise SystemExit("请在 .env 设置 TELEGRAM_BOT_TOKEN")
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    log.info("Telegram bot 启动中…")
    app.run_polling(stop_signals=stop_signals)


def run_in_thread() -> None:
    """在守护线程中启动 Telegram bot（由 FastAPI lifespan 调用）。"""
    import threading
    global _BOT_THREAD
    if not settings.telegram_bot_token:
        log.info("未配置 TELEGRAM_BOT_TOKEN，跳过 Telegram bot 启动")
        return
    if _BOT_THREAD and _BOT_THREAD.is_alive():
        log.info("Telegram bot 线程已在运行")
        return

    def _thread_main() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            main(stop_signals=None)
        except Exception:
            log.exception("Telegram bot 线程异常退出")
        finally:
            try:
                loop.close()
            except Exception:
                pass

    t = threading.Thread(target=_thread_main, daemon=True, name="telegram-bot")
    t.start()
    _BOT_THREAD = t
    log.info("Telegram bot 线程已启动")


if __name__ == "__main__":
    main()
