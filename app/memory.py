"""三层记忆：
1. 会话历史 (SQLite messages 表)
2. 长期事实 (SQLite facts 表 + Chroma 向量索引)
3. 滚动摘要 (SQLite summaries 表，定期由 LLM 压缩)

Phase 2 增强：
- 记忆分类：preference / project / rule / temporary
- 候选记忆确认机制
- 记忆删除
- Sleep-time 对话压缩
"""
from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from typing import Iterable, Optional

try:
    import chromadb as _chromadb_mod
    _CHROMADB_AVAILABLE = True
except Exception:
    _chromadb_mod = None  # type: ignore
    _CHROMADB_AVAILABLE = False

from sqlalchemy import (
    Column, Integer, String, Text, Float, create_engine, select, inspect, text,
)
from sqlalchemy.orm import DeclarativeBase, Session

from .config import settings
from . import llm


# ---------- SQLite ----------

class Base(DeclarativeBase):
    pass


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, index=True)
    role = Column(String)            # system / user / assistant / tool
    content = Column(Text)           # 文本内容；list content 走 JSON dump
    tool_calls = Column(Text)        # JSON dump
    tool_call_id = Column(String)
    name = Column(String)            # 工具名
    reasoning_content = Column(Text, nullable=True)  # Provider thinking/reasoning content that must be replayed for tool calls.
    ts = Column(Float)


class Summary(Base):
    __tablename__ = "summaries"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, index=True)
    content = Column(Text)
    covers_until_msg_id = Column(Integer)
    ts = Column(Float)


class Fact(Base):
    __tablename__ = "facts"
    id = Column(String, primary_key=True)      # uuid
    key = Column(String, index=True)           # 标题
    value = Column(Text)                       # 内容
    type = Column(String, default="project")   # preference / project / rule / temporary
    source = Column(String, default="chat")    # chat / file / explicit
    importance = Column(Integer, default=3)    # 1-5
    tags = Column(String)                      # 逗号分隔
    ts = Column(Float)                         # created_at
    expires_at = Column(Float, nullable=True)  # 过期时间戳，null=永不过期
    confirmed = Column(Integer, default=1)     # 0=候选 1=已确认


_engine = create_engine(f"sqlite:///{settings.data_dir}/secretary.db", future=True)


def _migrate():
    """为旧版 facts 表添加 Phase 2 新列（SQLite ALTER TABLE ADD COLUMN）。"""
    inspector = inspect(_engine)
    if "facts" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("facts")}
    with _engine.begin() as conn:
        if "type" not in columns:
            conn.execute(text("ALTER TABLE facts ADD COLUMN type VARCHAR DEFAULT 'project'"))
        if "source" not in columns:
            conn.execute(text("ALTER TABLE facts ADD COLUMN source VARCHAR DEFAULT 'chat'"))
        if "importance" not in columns:
            conn.execute(text("ALTER TABLE facts ADD COLUMN importance INTEGER DEFAULT 3"))
        if "expires_at" not in columns:
            conn.execute(text("ALTER TABLE facts ADD COLUMN expires_at FLOAT"))
        if "confirmed" not in columns:
            conn.execute(text("ALTER TABLE facts ADD COLUMN confirmed INTEGER DEFAULT 1"))

    # messages table migrations
    if "messages" in inspector.get_table_names():
        msg_columns = {c["name"] for c in inspector.get_columns("messages")}
        with _engine.begin() as conn:
            if "reasoning_content" not in msg_columns:
                conn.execute(text("ALTER TABLE messages ADD COLUMN reasoning_content TEXT"))


# 先迁移再建表（对新表不影响）
_migrate()
Base.metadata.create_all(_engine)


@contextmanager
def db_session():
    with Session(_engine) as s:
        yield s
        s.commit()


# ---------- Chroma 向量库 ----------

_chroma = None
_episodic = None


def _get_episodic():
    global _chroma, _episodic
    if _episodic is not None:
        return _episodic
    if not _CHROMADB_AVAILABLE:
        return None
    try:
        _chroma = _chromadb_mod.PersistentClient(path=str(settings.data_dir / "chroma"))
        _episodic = _chroma.get_or_create_collection("episodic")
        return _episodic
    except Exception:
        return None


# ---------- 会话历史 ----------

def append_message(session_id: str, msg: dict) -> int:
    """把一条 OpenAI 风格的消息存到 SQLite，返回 row id。"""
    content_val = msg.get("content")
    if isinstance(content_val, list):
        # Thinking blocks require an API-generated signature to replay.
        # Extract text parts only so history never contains unrestorable thinking blocks.
        text_parts = [b.get("text", "") for b in content_val if isinstance(b, dict) and b.get("type") == "text"]
        content_val = "\n".join(text_parts)
    elif not content_val:
        content_val = ""
    row = Message(
        session_id=session_id,
        role=msg.get("role"),
        content=content_val,
        tool_calls=json.dumps(msg.get("tool_calls")) if msg.get("tool_calls") else None,
        tool_call_id=msg.get("tool_call_id"),
        name=msg.get("name"),
        reasoning_content=msg.get("reasoning_content") or None,
        ts=time.time(),
    )
    with db_session() as s:
        s.add(row)
        s.flush()
        return row.id


def load_history(session_id: str, limit: int = 40) -> list[dict]:
    """读取最近 N 条消息，复原成 OpenAI messages 格式。"""
    with db_session() as s:
        stmt = (
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        rows = list(s.scalars(stmt))
        rows.reverse()
        out: list[dict] = []
        for r in rows:
            # _msg_id 是内部字段，用于滚动摘要 covers_until_msg_id 计算；不会发给模型
            m: dict = {"role": r.role, "content": r.content or "", "_msg_id": r.id}
            if r.tool_calls:
                m["tool_calls"] = json.loads(r.tool_calls)
            if r.tool_call_id:
                m["tool_call_id"] = r.tool_call_id
            if r.name:
                m["name"] = r.name
            if r.reasoning_content:
                m["reasoning_content"] = r.reasoning_content
            out.append(m)
    return out


def latest_summary(session_id: str) -> Optional[str]:
    with db_session() as s:
        stmt = (
            select(Summary)
            .where(Summary.session_id == session_id)
            .order_by(Summary.id.desc())
            .limit(1)
        )
        row = s.scalars(stmt).first()
        return row.content if row else None


def latest_summary_meta(session_id: str) -> Optional[dict]:
    """返回最近一条滚动摘要及其覆盖范围。"""
    with db_session() as s:
        stmt = (
            select(Summary)
            .where(Summary.session_id == session_id)
            .order_by(Summary.id.desc())
            .limit(1)
        )
        row = s.scalars(stmt).first()
        if row is None:
            return None
        return {"content": row.content, "covers_until_msg_id": int(row.covers_until_msg_id or 0)}


def write_summary(session_id: str, content: str, covers_until_msg_id: int) -> None:
    with db_session() as s:
        s.add(Summary(
            session_id=session_id, content=content,
            covers_until_msg_id=covers_until_msg_id, ts=time.time(),
        ))


# ---------- 会话历史查询 ----------

def list_sessions(limit: int = 50) -> list[dict]:
    """List distinct session IDs with message counts and timestamps."""
    with db_session() as s:
        rows = s.execute(
            text(
                "SELECT session_id, COUNT(*) as msg_count, MIN(ts) as first_ts, MAX(ts) as last_ts "
                "FROM messages WHERE role IN ('user', 'assistant') "
                "GROUP BY session_id ORDER BY last_ts DESC LIMIT :limit"
            ),
            {"limit": limit},
        ).fetchall()
        result = []
        for r in rows:
            preview_stmt = (
                select(Message.content)
                .where(Message.session_id == r.session_id, Message.role == "user")
                .order_by(Message.id.asc())
                .limit(1)
            )
            first_msg = s.scalars(preview_stmt).first() or ""
            result.append({
                "session_id": r.session_id,
                "msg_count": r.msg_count,
                "first_ts": r.first_ts,
                "last_ts": r.last_ts,
                "preview": first_msg[:120],
            })
        return result


def search_messages(q: str = "", session_id: Optional[str] = None, limit: int = 40) -> list[dict]:
    """Search messages by keyword, optionally scoped to a session."""
    with db_session() as s:
        stmt = select(Message).where(Message.role.in_(["user", "assistant"]))
        if q:
            stmt = stmt.where(Message.content.contains(q))
        if session_id:
            stmt = stmt.where(Message.session_id == session_id)
        stmt = stmt.order_by(Message.ts.desc()).limit(limit)
        rows = list(s.scalars(stmt))
        return [
            {
                "id": r.id,
                "session_id": r.session_id,
                "role": r.role,
                "content": (r.content or "")[:500],
                "ts": r.ts,
            }
            for r in rows
        ]


# ---------- 长期记忆 API ----------

def remember(
    key: str,
    value: str,
    tags: Optional[list[str] | str] = None,
    type: str = "project",
    source: str = "explicit",
    importance: int = 3,
    expires_at: Optional[float] = None,
    confirmed: int = 1,
) -> str:
    """写一条长期事实。同时写 SQLite 和 Chroma。"""
    fact_id = str(uuid.uuid4())
    if isinstance(tags, str):
        tag_str = tags
    else:
        tag_str = ",".join(tags or [])
    now = time.time()
    with db_session() as s:
        s.add(Fact(
            id=fact_id, key=key, value=value,
            type=type, source=source, importance=importance,
            tags=tag_str, ts=now, expires_at=expires_at, confirmed=confirmed,
        ))
    # 向量化
    text_for_embed = f"{key}: {value}"
    try:
        vec = llm.embed([text_for_embed])[0]
        _get_episodic().add(
            ids=[fact_id],
            documents=[text_for_embed],
            metadatas=[{
                "key": key,
                "tags": tag_str,
                "type": type,
                "source": source,
                "importance": importance,
                "confirmed": confirmed,
            }],
            embeddings=[vec],
        )
    except Exception as e:
        print(f"[memory] embedding failed, fact stored in SQL only: {e}")
    return fact_id


def upsert_memory(
    key: str,
    value: str,
    tags: Optional[list[str] | str] = None,
    type: str = "project",
    source: str = "chat",
    importance: int = 3,
    confirmed: int = 1,
) -> str:
    """Create or update a confirmed fact by key."""
    if isinstance(tags, str):
        tag_str = tags
    else:
        tag_str = ",".join(tags or [])
    now = time.time()
    with db_session() as s:
        row = s.scalars(select(Fact).where(Fact.key == key).limit(1)).first()
        if row is not None:
            row.value = value
            row.type = type
            row.source = source
            row.importance = importance
            row.tags = tag_str
            row.confirmed = confirmed
            row.ts = now
            return row.id
    return remember(
        key=key,
        value=value,
        tags=tag_str,
        type=type,
        source=source,
        importance=importance,
        confirmed=confirmed,
    )


def store_candidate(
    key: str,
    value: str,
    tags: Optional[list[str]] = None,
    type: str = "project",
    importance: int = 3,
) -> str:
    """存储候选记忆（confirmed=0），等待用户确认。"""
    return remember(
        key=key, value=value, tags=tags,
        type=type, source="chat", importance=importance,
        confirmed=0,
    )


def confirm_memory(fact_id: str) -> dict:
    """确认候选记忆，使其生效。"""
    merged_metadata = None
    with db_session() as s:
        row = s.get(Fact, fact_id)
        if row is None:
            return {"ok": False, "error": "memory not found"}
        row.confirmed = 1
        merged_metadata = {
            "key": row.key,
            "tags": row.tags or "",
            "type": row.type or "project",
            "source": row.source or "chat",
            "importance": row.importance or 3,
            "confirmed": 1,
        }
    # 同步更新 Chroma
    try:
        _get_episodic().update(
            ids=[fact_id],
            metadatas=[merged_metadata],
        )
    except Exception:
        pass
    return {"ok": True, "id": fact_id}


def reject_memory(fact_id: str) -> dict:
    """拒绝/删除候选记忆。"""
    return forget(fact_id)


def forget(fact_id: str) -> dict:
    """删除一条记忆（按 ID）。"""
    with db_session() as s:
        row = s.get(Fact, fact_id)
        if row is None:
            return {"ok": False, "error": "memory not found"}
        s.delete(row)
    try:
        _get_episodic().delete(ids=[fact_id])
    except Exception:
        pass
    return {"ok": True, "id": fact_id}


def list_memories(
    type: Optional[str] = None,
    confirmed: Optional[int] = None,
    limit: int = 50,
) -> list[dict]:
    """列出记忆，支持按类型和确认状态过滤。"""
    with db_session() as s:
        stmt = select(Fact)
        if type:
            stmt = stmt.where(Fact.type == type)
        if confirmed is not None:
            stmt = stmt.where(Fact.confirmed == confirmed)
        stmt = stmt.order_by(Fact.ts.desc()).limit(limit)
        rows = list(s.scalars(stmt))
        return [
            {
                "id": r.id,
                "title": r.key,
                "content": r.value,
                "type": r.type,
                "source": r.source,
                "importance": r.importance,
                "tags": r.tags,
                "created_at": r.ts,
                "expires_at": r.expires_at,
                "confirmed": bool(r.confirmed),
            }
            for r in rows
        ]


def recall(query: str, top_k: int = 5) -> list[dict]:
    """语义召回长期事实。只返回已确认且未过期的记忆。"""
    now = time.time()
    try:
        vec = llm.embed([query])[0]
        res = _get_episodic().query(query_embeddings=[vec], n_results=top_k * 2)
    except Exception:
        # 无嵌入模型时退化为关键词 LIKE
        with db_session() as s:
            stmt = (
                select(Fact)
                .where(Fact.confirmed == 1)
                .where(
                    Fact.value.like(f"%{query}%")
                    | Fact.key.like(f"%{query}%")
                    | Fact.tags.like(f"%{query}%")
                )
                .limit(top_k)
            )
            rows = list(s.scalars(stmt))
        return [
            {
                "id": r.id,
                "key": r.key,
                "value": r.value,
                "type": r.type,
                "importance": r.importance,
                "tags": r.tags,
            }
            for r in rows
        ]

    out: list[dict] = []
    seen_ids = set()
    for i, doc in enumerate(res["documents"][0]):
        fact_id = res["ids"][0][i]
        if fact_id in seen_ids:
            continue
        seen_ids.add(fact_id)
        # 补充 SQLite 数据（用于过期检查）
        with db_session() as s:
            row = s.get(Fact, fact_id)
        if row is None:
            continue
        if row.confirmed != 1:
            continue
        if row.expires_at and row.expires_at < now:
            continue
        out.append({
            "id": row.id,
            "key": row.key,
            "value": row.value,
            "type": row.type,
            "importance": row.importance,
            "tags": row.tags,
        })
        if len(out) >= top_k:
            break
    return out


# ---------- 滚动摘要 ----------

SUMMARIZE_PROMPT = """你是一个对话压缩助手。请把对话压成简明要点（200 字内），
保留：用户偏好、未完成的任务、关键事实、人物/项目名。忽略寒暄。

{context}

输出格式：
- 用 markdown bullet
- 一条要点一行
"""


def _estimate_tokens(messages: list[dict]) -> int:
    """Very rough token estimator.

    目标：只用于“是否需要滚动摘要”的阈值判断，不追求精确。
    经验上英文 ~4 chars/token，中文更接近 ~1.5-2 chars/token。
    这里采用折中：按 UTF-8 字符数估算，并给 tool / role 增加少量开销。
    """
    total_chars = 0
    tool_calls = 0
    for m in messages:
        content = m.get("content") or ""
        if isinstance(content, str):
            total_chars += len(content)
        if m.get("tool_calls"):
            tool_calls += 1
    # 2 chars/token 比较保守（偏“多算”），避免上下文溢出
    base = int(total_chars / 2)
    overhead = len(messages) * 8 + tool_calls * 60
    return base + overhead


def summarize_and_truncate(
    session_id: str,
    all_messages: list[dict],
    keep_recent: int = 12,
    token_budget: int = 8000,
) -> list[dict]:
    """如果消息太多或 token 预算超标，把前半段压成摘要，保留最近 N 条。

    返回新的 messages 列表（带 system 摘要前缀）。
    """
    summary_meta = latest_summary_meta(session_id)
    existing_summary = (summary_meta or {}).get("content") if summary_meta else ""
    covers_until = int((summary_meta or {}).get("covers_until_msg_id") or 0)

    # 去掉内部字段，避免传入模型
    def _strip(msgs: list[dict]) -> list[dict]:
        cleaned = []
        for m in msgs:
            if "_msg_id" in m:
                mm = dict(m)
                mm.pop("_msg_id", None)
                cleaned.append(mm)
            else:
                cleaned.append(m)
        return cleaned

    # 如果当前窗口里最早的消息 id 已经大于 covers_until，说明更早内容只在摘要里；
    # 即使本轮不触发压缩，也应把摘要前置注入，保证上下文完整。
    min_msg_id = None
    for m in all_messages:
        mid = m.get("_msg_id")
        if isinstance(mid, int):
            min_msg_id = mid if min_msg_id is None else min(min_msg_id, mid)

    needs_compact = not (len(all_messages) <= keep_recent + 2 and _estimate_tokens(all_messages) <= token_budget)

    # 不需要压缩：仅在“摘要能补齐更早上下文”时注入摘要
    if not needs_compact:
        if existing_summary and min_msg_id is not None and covers_until and covers_until < min_msg_id:
            return [{"role": "system", "content": f"[历史会话摘要]\n{existing_summary}"}, *_strip(all_messages)]
        return _strip(all_messages)

    # 只压缩“尚未被 covers_until 覆盖”的新增部分，避免重复压同一段
    unsummarized = [m for m in all_messages if isinstance(m.get("_msg_id"), int) and m["_msg_id"] > covers_until]
    if len(unsummarized) <= keep_recent:
        # 消息条数不够切分，只能缩减保留条数来满足 token 预算
        kept = list(unsummarized)
        while _estimate_tokens(kept) > token_budget and len(kept) > 4:
            kept.pop(0)
        out = _strip(kept)
        if existing_summary:
            return [{"role": "system", "content": f"[历史会话摘要]\n{existing_summary}"}, *out]
        return out

    to_keep = unsummarized[-keep_recent:]
    to_compress = unsummarized[:-keep_recent]
    # 压缩到这里为止（分段累计）
    new_covers_until = int(to_compress[-1].get("_msg_id") or covers_until)

    new_conv = "\n".join(
        f"{m['role']}: {m.get('content','')[:400]}" for m in to_compress if m.get("content")
    )
    if existing_summary:
        context = f"已有摘要（覆盖到消息 {covers_until}）：\n{existing_summary}\n\n新增对话（请合并进摘要）：\n{new_conv}"
    else:
        context = f"要压缩的对话：\n{new_conv}"
    resp = llm.chat_completion(
        messages=[{"role": "user", "content": SUMMARIZE_PROMPT.format(context=context)}],
        temperature=0.2,
    )
    summary_text = resp["choices"][0]["message"]["content"]
    write_summary(session_id, summary_text, covers_until_msg_id=new_covers_until)

    return [
        {"role": "system", "content": f"[历史会话摘要]\n{summary_text}"},
        *_strip(to_keep),
    ]


# ---------- Sleep-time 压缩 ----------

SLEEP_COMPRESS_PROMPT = """请从以下对话历史中提炼出值得长期记住的结构化事实。

要求：
1. 只提取用户偏好、项目背景、工作规则、重要决定
2. 不要提取临时信息、寒暄、已知常识
3. 每条事实包含：title（短标题）、content（详细内容）、type（preference/project/rule）、importance（1-5）
4. 如果同一主题已有多个事实，合并成一条
5. 如果没有值得记住的，返回空数组

输出 JSON 数组格式：
[{{"title": "...", "content": "...", "type": "project", "importance": 4, "tags": ["tag1"]}}]

对话历史：
{history}
"""


def compress_history_to_facts(session_id: str) -> dict:
    """把会话历史压缩成结构化事实存入长期记忆。"""
    history = load_history(session_id, limit=200)
    if len(history) < 5:
        return {"ok": False, "note": "会话历史太短，无需压缩", "facts_added": 0}

    conv_text = "\n".join(
        f"{m['role']}: {m.get('content', '')[:500]}"
        for m in history if m.get("content")
    )
    resp = llm.chat_completion(
        messages=[{"role": "user", "content": SLEEP_COMPRESS_PROMPT.format(history=conv_text)}],
        temperature=0.2,
    )
    raw = resp["choices"][0]["message"]["content"].strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1][4:] if parts[1].startswith("json") else parts[1]

    try:
        facts = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": "LLM 返回格式无法解析", "raw": raw}

    if not isinstance(facts, list):
        return {"ok": False, "error": "LLM 返回不是数组", "raw": raw}

    added = 0
    for f in facts:
        if not isinstance(f, dict):
            continue
        remember(
            key=f.get("title", "untitled"),
            value=f.get("content", ""),
            tags=f.get("tags", []),
            type=f.get("type", "project"),
            source="sleep_compress",
            importance=f.get("importance", 3),
            confirmed=1,
        )
        added += 1

    return {"ok": True, "facts_added": added}
