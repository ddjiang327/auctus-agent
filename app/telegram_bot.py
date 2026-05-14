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

from . import agent
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tg")


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
        p = Path(path)
        if p.exists():
            with p.open("rb") as fh:
                await message.reply_document(document=fh, filename=p.name)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(
        f"你好！我是你的秘书 Agent。\n你的 Telegram ID 是 {uid}\n"
        "把它填到 .env 的 TELEGRAM_ALLOWED_USER_IDS 里限定只有你能用。"
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
        # agent.chat 是同步的，放线程池里跑避免阻塞 event loop
        result = await asyncio.to_thread(agent.chat, session_id, text)
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


def main() -> None:
    if not settings.telegram_bot_token:
        raise SystemExit("请在 .env 设置 TELEGRAM_BOT_TOKEN")
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    log.info("Telegram bot 启动中…")
    app.run_polling()


if __name__ == "__main__":
    main()
