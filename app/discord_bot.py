"""Discord bot entry — turns Discord DMs and @mentions into agent tasks.

Mirrors the structure of telegram_bot.py:
- One bot per .env DISCORD_BOT_TOKEN
- DISCORD_ALLOWED_USER_IDS gates who can use it (comma-separated user IDs)
- Each user gets a stable session_id 'dc-<uid>'
- Runs in a daemon thread off the FastAPI lifespan
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

from . import accounting, agent
from .config import settings


log = logging.getLogger("dc")
_BOT_THREAD: Optional[threading.Thread] = None
_CLIENT = None  # type: ignore[assignment]


def _allowed_ids() -> set[int]:
    raw = (getattr(settings, "discord_allowed_user_ids", "") or "").strip()
    if not raw:
        return set()
    out: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.add(int(chunk))
        except ValueError:
            pass
    return out


def _auth_ok(user_id: int) -> bool:
    allow = _allowed_ids()
    if not allow:
        log.warning("DISCORD_ALLOWED_USER_IDS unset — bot is open to anyone who DMs it")
        return True
    return user_id in allow


def _run_agent(uid: int, text: str) -> dict:
    """Synchronously call agent.chat. Run via to_thread from the discord async loop."""
    session_id = f"dc-{uid}"
    try:
        result = agent.chat(session_id, text)
        return result if isinstance(result, dict) else {"reply": str(result)}
    except Exception as exc:
        log.exception("Discord agent call failed")
        return {"reply": f"执行失败：{type(exc).__name__}: {exc}", "files": []}


async def _send_reply(channel, result: dict) -> None:
    """Send the agent reply text + any attached output files."""
    text = (result.get("reply") or "(no reply)").strip()
    for i in range(0, max(1, len(text)), 1900):
        chunk = text[i:i + 1900]
        if chunk:
            await channel.send(chunk)

    import discord
    from pathlib import Path
    for path_str in result.get("files", []) or []:
        if path_str.startswith("/files/"):
            rel = path_str.removeprefix("/files/").lstrip("/")
            local = (settings.output_dir / rel).resolve()
        else:
            local = Path(path_str)
        if local.exists() and local.is_file() and local.stat().st_size < 25 * 1024 * 1024:
            try:
                await channel.send(file=discord.File(str(local), filename=local.name))
            except Exception as exc:
                log.warning("Failed to attach %s: %s", local, exc)


def main() -> None:
    """Run the Discord client (blocking)."""
    if not getattr(settings, "discord_bot_token", None):
        raise SystemExit("请在 .env 设置 DISCORD_BOT_TOKEN")

    try:
        import discord
    except ImportError as exc:
        raise SystemExit(f"discord.py not installed: {exc}")

    intents = discord.Intents.default()
    intents.message_content = True
    intents.dm_messages = True
    client = discord.Client(intents=intents)

    global _CLIENT
    _CLIENT = client

    @client.event
    async def on_ready():
        log.info("Discord bot ready as %s (id=%s)", client.user, client.user.id if client.user else "?")

    @client.event
    async def on_message(message):
        if message.author == client.user:
            return

        is_dm = (message.guild is None)
        is_mention = client.user is not None and client.user.mentioned_in(message)
        if not (is_dm or is_mention):
            return

        uid = message.author.id
        if not _auth_ok(uid):
            await message.reply("Unauthorized.")
            return

        text = (message.content or "").strip()
        if client.user is not None:
            text = text.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "").strip()
        if not text:
            return

        accounting.add_telegram_inbox_message("in", text, from_user=f"dc:{uid}")
        try:
            async with message.channel.typing():
                result = await asyncio.to_thread(_run_agent, uid, text)
            reply_text = (result.get("reply") or "").strip()
            if reply_text:
                accounting.add_telegram_inbox_message("out", reply_text, from_user="agent")
            await _send_reply(message.channel, result)
        except Exception as exc:
            log.exception("Discord message handler failed")
            await message.reply(f"执行失败：{type(exc).__name__}: {exc}")

    log.info("Discord bot starting…")
    client.run(settings.discord_bot_token, log_handler=None)


def run_in_thread() -> None:
    """Start the Discord bot in a daemon thread (called from FastAPI lifespan)."""
    global _BOT_THREAD
    if not getattr(settings, "discord_bot_token", None):
        log.info("未配置 DISCORD_BOT_TOKEN，跳过 Discord bot 启动")
        return
    if _BOT_THREAD and _BOT_THREAD.is_alive():
        log.info("Discord bot 线程已在运行")
        return

    def _thread_main() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            main()
        except Exception:
            log.exception("Discord bot thread exited with error")
        finally:
            try:
                loop.close()
            except Exception:
                pass

    t = threading.Thread(target=_thread_main, daemon=True, name="discord-bot")
    t.start()
    _BOT_THREAD = t
    log.info("Discord bot 线程已启动")


if __name__ == "__main__":
    main()
