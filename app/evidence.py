"""Per-session evidence store for research and comparison tasks."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from .config import settings


def record_tool_result(session_id: str, tool_name: str, result: Any) -> list[dict[str, Any]]:
    """Extract source-like records from a tool result and persist them."""
    if not session_id or not isinstance(result, dict):
        return []
    items = _items_from_result(tool_name, result)
    if not items:
        return []
    _ensure_store()
    saved: list[dict[str, Any]] = []
    with sqlite3.connect(_db_path()) as conn:
        for item in items:
            record = _normalize_record(session_id, tool_name, item)
            conn.execute(
                """
                INSERT INTO evidence (
                    id, session_id, tool_name, title, url, snippet, quote,
                    source_domain, retrieved_at, created_ts
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    snippet = excluded.snippet,
                    quote = excluded.quote,
                    retrieved_at = excluded.retrieved_at
                """,
                (
                    record["id"],
                    record["session_id"],
                    record["tool_name"],
                    record["title"],
                    record["url"],
                    record["snippet"],
                    record["quote"],
                    record["source_domain"],
                    record["retrieved_at"],
                    record["created_ts"],
                ),
            )
            saved.append(record)
    return saved


def list_evidence(session_id: str, limit: int = 20) -> list[dict[str, Any]]:
    _ensure_store()
    limit = max(1, min(int(limit or 20), 100))
    with sqlite3.connect(_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, session_id, tool_name, title, url, snippet, quote,
                   source_domain, retrieved_at, created_ts
            FROM evidence
            WHERE session_id = ?
            ORDER BY created_ts DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def prompt_context(session_id: str, limit: int = 8) -> str:
    items = list_evidence(session_id, limit=limit)
    if not items:
        return ""
    lines = []
    for idx, item in enumerate(reversed(items), start=1):
        label = item.get("title") or item.get("source_domain") or item.get("url") or "Source"
        snippet = item.get("quote") or item.get("snippet") or ""
        lines.append(f"[{idx}] {label} — {item.get('url') or 'no url'}")
        if snippet:
            lines.append(f"    {snippet[:260]}")
    return (
        "[已保存证据来源]\n"
        + "\n".join(lines)
        + "\n最终回答涉及事实、价格、条款、新闻或网页信息时，必须引用这些来源编号或说明仍待核验。"
    )


def _items_from_result(tool_name: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    if tool_name == "search_web":
        results = result.get("results")
        return results if isinstance(results, list) else []
    if tool_name in {"fetch_webpage", "browser_open", "browser_read"}:
        url = str(result.get("url") or result.get("final_url") or "")
        content = str(result.get("content") or result.get("text") or result.get("markdown") or "")
        title = str(result.get("title") or _domain(url) or tool_name)
        if url or content:
            return [{"title": title, "url": url, "snippet": content[:500], "quote": _first_quote(content)}]
    return []


def _normalize_record(session_id: str, tool_name: str, item: dict[str, Any]) -> dict[str, Any]:
    url = str(item.get("url") or item.get("href") or "").strip()
    title = str(item.get("title") or _domain(url) or "Untitled source").strip()[:240]
    snippet = str(item.get("snippet") or item.get("description") or item.get("content") or "").strip()[:1000]
    quote = str(item.get("quote") or _first_quote(snippet)).strip()[:600]
    record_id = hashlib.sha1(f"{session_id}\n{url or title}".encode("utf-8")).hexdigest()[:20]
    return {
        "id": record_id,
        "session_id": session_id,
        "tool_name": tool_name,
        "title": title,
        "url": url,
        "snippet": snippet,
        "quote": quote,
        "source_domain": _domain(url),
        "retrieved_at": datetime.now().isoformat(timespec="seconds"),
        "created_ts": time.time(),
    }


def _first_quote(text: str) -> str:
    cleaned = " ".join((text or "").split())
    return cleaned[:360]


def _domain(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().removeprefix("www.")
    except Exception:
        return ""


def _ensure_store() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS evidence (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                title TEXT,
                url TEXT,
                snippet TEXT,
                quote TEXT,
                source_domain TEXT,
                retrieved_at TEXT,
                created_ts REAL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_session ON evidence(session_id, created_ts)")


def _db_path():
    return settings.data_dir / "secretary.db"
