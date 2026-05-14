"""Self-evolution loop: learn candidates from conversations and task logs."""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from . import llm, memory
from .config import settings


LEARNING_TYPES = {"preference", "workflow", "prompt_rule", "tool_strategy", "error_pattern"}
STATUSES = {"pending", "applied", "rejected", "disabled"}
RUNTIME_TYPES = {"prompt_rule", "tool_strategy", "error_pattern", "workflow"}


def db_path() -> Path:
    return settings.data_dir / "secretary.db"


def init_db(path: Optional[Path] = None) -> None:
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_candidates (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                evidence TEXT,
                confidence REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at REAL NOT NULL,
                applied_at REAL,
                memory_id TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_learning_status ON learning_candidates(status)")
        conn.commit()


def scan(limit_messages: int = 120, limit_logs: int = 80, path: Optional[Path] = None) -> dict:
    """Scan recent history/logs and store pending learning candidates."""
    init_db(path)
    messages = _recent_messages(limit_messages, path=path)
    logs = _recent_tool_logs(limit_logs)
    if not messages and not logs:
        return {"ok": True, "candidates_added": 0, "note": "no history or logs to scan"}

    raw = _ask_for_candidates(messages, logs)
    candidates = _parse_candidates(raw)
    added: list[str] = []
    for item in candidates:
        candidate_id = store_candidate(
            type=item.get("type", "preference"),
            title=item.get("title", "Untitled learning"),
            content=item.get("content", ""),
            evidence=item.get("evidence", ""),
            confidence=float(item.get("confidence") or 0),
            path=path,
        )
        if candidate_id:
            added.append(candidate_id)
    return {"ok": True, "candidates_added": len(added), "ids": added}


def store_candidate(
    *,
    type: str,
    title: str,
    content: str,
    evidence: str = "",
    confidence: float = 0,
    path: Optional[Path] = None,
) -> Optional[str]:
    """Store one pending learning candidate after normalization."""
    init_db(path)
    normalized_type = type if type in LEARNING_TYPES else "preference"
    title = title.strip()[:160]
    content = content.strip()
    if not title or not content:
        return None
    confidence = max(0.0, min(1.0, float(confidence or 0)))
    candidate_id = str(uuid.uuid4())
    with sqlite3.connect(path or db_path()) as conn:
        conn.execute(
            """
            INSERT INTO learning_candidates (
                id, type, title, content, evidence, confidence, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (candidate_id, normalized_type, title, content, evidence.strip(), confidence, time.time()),
        )
        conn.commit()
    return candidate_id


def list_candidates(status: str = "pending", path: Optional[Path] = None, limit: int = 50) -> list[dict]:
    init_db(path)
    if status not in STATUSES and status != "all":
        status = "pending"
    params: list[Any] = []
    where = ""
    if status != "all":
        where = "WHERE status = ?"
        params.append(status)
    params.append(limit)
    with sqlite3.connect(path or db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT id, type, title, content, evidence, confidence, status, created_at, applied_at, memory_id
            FROM learning_candidates
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def apply_candidate(candidate_id: str, path: Optional[Path] = None) -> dict:
    init_db(path)
    with sqlite3.connect(path or db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM learning_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "candidate not found"}
        if row["status"] != "pending":
            return {"ok": False, "error": f"candidate already {row['status']}"}

    memory_type = _memory_type_for_learning(row["type"])
    memory_id = memory.remember(
        key=row["title"],
        value=row["content"],
        tags=["evolution", row["type"]],
        type=memory_type,
        source="evolution",
        importance=_importance_for_confidence(float(row["confidence"] or 0)),
        confirmed=1,
    )
    with sqlite3.connect(path or db_path()) as conn:
        conn.execute(
            """
            UPDATE learning_candidates
            SET status = 'applied', applied_at = ?, memory_id = ?
            WHERE id = ?
            """,
            (time.time(), memory_id, candidate_id),
        )
        conn.commit()
    return {"ok": True, "id": candidate_id, "memory_id": memory_id}


def reject_candidate(candidate_id: str, path: Optional[Path] = None) -> dict:
    init_db(path)
    with sqlite3.connect(path or db_path()) as conn:
        cur = conn.execute(
            """
            UPDATE learning_candidates
            SET status = 'rejected'
            WHERE id = ? AND status = 'pending'
            """,
            (candidate_id,),
        )
        conn.commit()
    if cur.rowcount == 0:
        return {"ok": False, "error": "pending candidate not found"}
    return {"ok": True, "id": candidate_id}


def disable_candidate(candidate_id: str, path: Optional[Path] = None) -> dict:
    """Disable an applied learning so it no longer affects runtime behavior."""
    init_db(path)
    with sqlite3.connect(path or db_path()) as conn:
        cur = conn.execute(
            """
            UPDATE learning_candidates
            SET status = 'disabled'
            WHERE id = ? AND status = 'applied'
            """,
            (candidate_id,),
        )
        conn.commit()
    if cur.rowcount == 0:
        return {"ok": False, "error": "applied candidate not found"}
    return {"ok": True, "id": candidate_id}


def rollback_candidate(candidate_id: str, path: Optional[Path] = None) -> dict:
    """Move a non-pending learning back to pending and remove its linked memory if possible."""
    init_db(path)
    with sqlite3.connect(path or db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, status, memory_id FROM learning_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": "candidate not found"}
        if row["status"] == "pending":
            return {"ok": False, "error": "candidate already pending"}
        memory_id = row["memory_id"]
        conn.execute(
            """
            UPDATE learning_candidates
            SET status = 'pending', applied_at = NULL, memory_id = NULL
            WHERE id = ?
            """,
            (candidate_id,),
        )
        conn.commit()

    memory_result = None
    if memory_id:
        memory_result = memory.forget(memory_id)
    return {"ok": True, "id": candidate_id, "memory_removed": memory_result}


def applied_learnings(
    *,
    types: Optional[set[str]] = None,
    path: Optional[Path] = None,
    limit: int = 20,
) -> list[dict]:
    """Return applied learning items that can influence future runs."""
    init_db(path)
    selected_types = types or RUNTIME_TYPES
    selected_types = {item for item in selected_types if item in LEARNING_TYPES}
    if not selected_types:
        return []

    placeholders = ", ".join("?" for _ in selected_types)
    params: list[Any] = [*sorted(selected_types), limit]
    with sqlite3.connect(path or db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT id, type, title, content, evidence, confidence, applied_at, memory_id
            FROM learning_candidates
            WHERE status = 'applied'
              AND type IN ({placeholders})
            ORDER BY confidence DESC, applied_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def build_runtime_context(user_text: str, path: Optional[Path] = None, limit: int = 8) -> str:
    """Build a compact system context from confirmed self-evolution learnings."""
    rules = applied_learnings(
        types={"prompt_rule", "tool_strategy", "error_pattern"},
        path=path,
        limit=limit,
    )
    workflows = _match_workflows(user_text, path=path, limit=3)
    skill_ctx = ""
    try:
        from .evolve.skills import build_skill_context
        skill_ctx = build_skill_context(user_text, path=path)
    except Exception:
        pass
    if not rules and not workflows and not skill_ctx:
        return ""

    parts: list[str] = []
    if rules or workflows:
        lines = ["[自我学习规则]", "这些规则来自用户确认过的历史学习项；与当前用户要求冲突时，以当前用户要求为准。"]
        if rules:
            lines.append("通用规则：")
            for item in rules:
                lines.append(f"- {item['title']}：{item['content']}")
        if workflows:
            lines.append("可能适用的工作流：")
            for item in workflows:
                lines.append(f"- {item['title']}：{item['content']}")
        parts.append("\n".join(lines))
    if skill_ctx:
        parts.append(skill_ctx)
    return "\n\n".join(parts)


def build_tool_retry_hint(
    *,
    tool_name: str,
    error: str,
    user_input: str = "",
    path: Optional[Path] = None,
    limit: int = 3,
) -> str:
    """Return confirmed self-learning hints for a failed tool call."""
    if not error:
        return ""
    learnings = applied_learnings(types={"tool_strategy", "error_pattern"}, path=path, limit=30)
    if not learnings:
        return ""

    query_tokens = _tokens(f"{tool_name} {error} {user_input}")
    scored: list[tuple[int, float, dict]] = []
    for item in learnings:
        haystack = f"{item.get('title', '')} {item.get('content', '')} {item.get('evidence', '')}"
        score = len(query_tokens & _tokens(haystack))
        if score > 0:
            scored.append((score, float(item.get("confidence") or 0), item))
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    matched = [item for _, _, item in scored[:limit]]
    if not matched:
        return ""

    lines = ["[工具重试建议]", "以下建议来自用户确认过的历史错误模式/工具策略："]
    for item in matched:
        lines.append(f"- {item['title']}：{item['content']}")
    return "\n".join(lines)


def _recent_messages(limit: int, path: Optional[Path] = None) -> list[dict]:
    init_db(path)
    with sqlite3.connect(path or db_path()) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT session_id, role, content, name, ts
                FROM messages
                WHERE content IS NOT NULL AND content != ''
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    out = [dict(row) for row in rows]
    out.reverse()
    return out


def _recent_tool_logs(limit: int) -> list[dict]:
    log_path = settings.logs_dir / "tool_calls.jsonl"
    if not log_path.exists():
        return []
    raw_lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    items: list[dict] = []
    for raw in raw_lines[-limit:]:
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items.append({
            "tool_name": entry.get("tool_name"),
            "status": entry.get("status"),
            "error": entry.get("error"),
            "user_input": (entry.get("user_input") or "")[:500],
        })
    return items


def _match_workflows(user_text: str, path: Optional[Path] = None, limit: int = 3) -> list[dict]:
    workflows = applied_learnings(types={"workflow"}, path=path, limit=30)
    scored: list[tuple[int, float, dict]] = []
    text_tokens = _tokens(user_text)
    for item in workflows:
        haystack = f"{item.get('title', '')} {item.get('content', '')}"
        score = len(text_tokens & _tokens(haystack))
        if score > 0:
            scored.append((score, float(item.get("confidence") or 0), item))
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [item for _, _, item in scored[:limit]]


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    ascii_tokens = {token for token in re.findall(r"[a-z0-9_]{2,}", lowered)}
    cjk_tokens = {lowered[i:i + 2] for i in range(max(0, len(lowered) - 1)) if "\u4e00" <= lowered[i] <= "\u9fff"}
    return ascii_tokens | cjk_tokens


def _ask_for_candidates(messages: list[dict], logs: list[dict]) -> str:
    prompt = f"""你是这个私人秘书 Agent 的学习分析器。请从历史对话和工具日志中提炼“候选学习项”。

只输出 JSON 数组，不要输出解释。每项字段：
- type: preference / workflow / prompt_rule / tool_strategy / error_pattern
- title: 短标题
- content: 可执行、可复用的学习内容
- evidence: 证据摘要，说明来自哪些对话/任务现象
- confidence: 0 到 1

规则：
1. 不要提取密码、API key、隐私敏感内容。
2. 不要把单次临时任务当成长期偏好。
3. workflow 要描述触发条件和步骤。
4. error_pattern 要描述失败模式和下次规避方式。
5. 如果没有可靠学习项，返回 []。

历史对话：
{json.dumps(messages[-120:], ensure_ascii=False)}

工具日志：
{json.dumps(logs[-80:], ensure_ascii=False)}
"""
    resp = llm.chat_completion(messages=[{"role": "user", "content": prompt}], temperature=0.1)
    return resp["choices"][0]["message"]["content"]


def _parse_candidates(raw: str) -> list[dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1][4:] if len(parts) > 1 and parts[1].startswith("json") else parts[1]
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, IndexError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _memory_type_for_learning(learning_type: str) -> str:
    if learning_type == "preference":
        return "preference"
    if learning_type in {"prompt_rule", "tool_strategy", "error_pattern"}:
        return "rule"
    return "project"


def _importance_for_confidence(confidence: float) -> int:
    if confidence >= 0.85:
        return 5
    if confidence >= 0.65:
        return 4
    if confidence >= 0.4:
        return 3
    return 2


init_db()
