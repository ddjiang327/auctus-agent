"""完成通知分发：应用内通知 + Telegram + (mobile relay 预留 seam)。

- 应用内通知：存 SQLite `notifications` 表（与 task_mode 同一个 secretary.db），Web UI 轮询展示。
- Telegram：走 settings 里的 bot token + allowed ids，确定性 POST sendMessage。
- mobile relay：反向推送需要 relay-server + mobile-app 配合（当前 relay 是 mobile→desktop
  请求/应答模型，没有主动下行通道），这里留 best-effort seam，配齐后再接。

设计上把「渠道」做成可插拔：dispatch_* 永远先写应用内通知（最可靠），再尽力发外部渠道，
任一外部渠道失败都不影响任务结果，只记日志。
"""
from __future__ import annotations

import logging
import sqlite3
import time
import uuid

from .config import settings

log = logging.getLogger(__name__)

KIND_TASK_DONE = "task_done"
KIND_TASK_FAILED = "task_failed"

_MAX_BODY = 500


def _db_path():
    return settings.data_dir / "secretary.db"


def _ensure_store() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                kind TEXT,
                title TEXT,
                body TEXT,
                ref_session_id TEXT,
                created_at REAL,
                read_at REAL
            )
            """
        )


def _row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "kind": row[1],
        "title": row[2],
        "body": row[3],
        "ref_session_id": row[4],
        "created_at": row[5],
        "read_at": row[6],
        "read": row[6] is not None,
    }


# ── in-app notification store ───────────────────────────────────────────────

def add_notification(kind: str, title: str, body: str = "", ref_session_id: str = "") -> dict:
    _ensure_store()
    item_id = uuid.uuid4().hex[:12]
    created_at = time.time()
    clipped = (body or "")[:_MAX_BODY]
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            "INSERT INTO notifications (id, kind, title, body, ref_session_id, created_at, read_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item_id, kind, title, clipped, ref_session_id, created_at, None),
        )
    return {
        "id": item_id,
        "kind": kind,
        "title": title,
        "body": clipped,
        "ref_session_id": ref_session_id,
        "created_at": created_at,
        "read_at": None,
        "read": False,
    }


def list_notifications(limit: int = 30, unread_only: bool = False) -> list[dict]:
    _ensure_store()
    limit = max(1, min(int(limit or 30), 100))
    query = "SELECT id, kind, title, body, ref_session_id, created_at, read_at FROM notifications"
    if unread_only:
        query += " WHERE read_at IS NULL"
    query += " ORDER BY created_at DESC LIMIT ?"
    with sqlite3.connect(_db_path()) as conn:
        rows = conn.execute(query, (limit,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def unread_count() -> int:
    _ensure_store()
    with sqlite3.connect(_db_path()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE read_at IS NULL"
        ).fetchone()
    return int(row[0]) if row else 0


def mark_read(notification_id: str) -> bool:
    _ensure_store()
    with sqlite3.connect(_db_path()) as conn:
        cur = conn.execute(
            "UPDATE notifications SET read_at = ? WHERE id = ? AND read_at IS NULL",
            (time.time(), notification_id),
        )
        return cur.rowcount > 0


def mark_all_read() -> int:
    _ensure_store()
    with sqlite3.connect(_db_path()) as conn:
        cur = conn.execute(
            "UPDATE notifications SET read_at = ? WHERE read_at IS NULL",
            (time.time(),),
        )
        return cur.rowcount


# ── external channels ───────────────────────────────────────────────────────

def _send_telegram(text: str) -> bool:
    """Best-effort proactive Telegram push to all allowed user ids."""
    token = settings.telegram_bot_token
    chat_ids = settings.allowed_telegram_ids()
    if not token or not chat_ids:
        return False
    try:
        import httpx
    except ImportError:
        log.warning("httpx not available — telegram push skipped")
        return False
    ok = False
    for chat_id in chat_ids:
        try:
            resp = httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text[:3900]},
                timeout=10,
            )
            ok = ok or resp.status_code == 200
            if resp.status_code != 200:
                log.warning("telegram push non-200 for %s: %s", chat_id, resp.status_code)
        except Exception as exc:
            log.warning("telegram push failed for %s: %s", chat_id, exc)
    return ok


def _push_relay(text: str, session_id: str) -> bool:
    """Mobile relay reverse-push seam.

    The current relay is a mobile→desktop request/response tunnel with no
    desktop-initiated downstream frame, and the mobile app only renders replies
    to messages it sent. True proactive mobile push needs relay-server +
    mobile-app changes (separate repos), so this is intentionally a no-op until
    those land. Kept as a named seam so wiring it later is a one-function change.
    """
    return False


# ── public dispatch ──────────────────────────────────────────────────────────

def dispatch_task_done(session_id: str, goal: str, summary: str) -> dict:
    """后台任务成功完成：先写应用内通知，再尽力推送外部渠道。

    title 只存裸目标（goal）；「后台任务完成」这类前缀由前端按 kind + UI 语言渲染，
    避免把语言写死进存储。Telegram 推送用 emoji 前缀（无 UI 语言上下文，保持中性）。
    """
    goal_text = (goal or "").strip()[:80]
    body = (summary or "").strip()
    note = add_notification(KIND_TASK_DONE, goal_text, body, session_id)
    text = f"✅ {goal_text}\n\n{body[:_MAX_BODY]}"
    _send_telegram(text)
    _push_relay(text, session_id)
    return note


def dispatch_task_failed(session_id: str, goal: str, error: str) -> dict:
    """后台任务失败：写应用内通知 + 外部渠道告警。title 同样只存裸目标。"""
    goal_text = (goal or "").strip()[:80]
    body = (error or "").strip()
    note = add_notification(KIND_TASK_FAILED, goal_text, body, session_id)
    text = f"⚠️ {goal_text}\n\n{body[:_MAX_BODY]}"
    _send_telegram(text)
    _push_relay(text, session_id)
    return note
