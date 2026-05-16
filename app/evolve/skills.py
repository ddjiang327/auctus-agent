"""Skill closed learning loop.

Extracts reusable task Skills from conversation history, stores them as
Markdown SkillDocs, and injects the most relevant skills into the system
prompt at runtime.

Lifecycle:  pending → applied (→ disabled)
                          ↑
                       rollback
            pending → rejected
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .. import llm
from ..config import settings


STATUSES = {"pending", "applied", "rejected", "disabled"}

_SCAN_PROMPT = """你是一个任务模式分析器。请从以下历史对话和工具日志中，提炼出值得复用的"技能（Skill）"。

只输出 JSON 数组，不要输出解释。每项格式：
{{
  "name": "短名称（10 字以内）",
  "triggers": {{
    "keywords": ["触发该技能的中英文关键词，5-10 个"],
    "tool_patterns": ["调用此技能时通常需要的工具名，如 make_spreadsheet"]
  }},
  "level0": "L0 短版：3-8 行 Markdown 要点，总结执行步骤（每行以 - 开头）",
  "level1": "L1 完整版：更详细的分步说明（可留空字符串）",
  "confidence": 0.0到1.0之间的数字,
  "evidence": "触发提炼的现象描述（一句话）"
}}

规则：
1. 只提炼重复出现或明确成功的任务模式；单次临时任务不提炼。
2. 不包含密码、API Key、用户隐私。
3. confidence < 0.5 的技能不要提炼。
4. 没有可靠技能时返回 []。

历史对话：
{messages}

工具日志：
{logs}
"""


def _skills_dir(status: str) -> Path:
    d = settings.data_dir / "evolve" / "skills" / status
    d.mkdir(parents=True, exist_ok=True)
    return d


def _db_path() -> Path:
    return settings.data_dir / "secretary.db"


def init_db(path: Optional[Path] = None) -> None:
    target = path or _db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS skill_candidates (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                triggers_json TEXT NOT NULL DEFAULT '{}',
                level0 TEXT NOT NULL DEFAULT '',
                level1 TEXT NOT NULL DEFAULT '',
                doc_path TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at REAL NOT NULL,
                applied_at REAL,
                source TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_skill_status ON skill_candidates(status)")
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS skill_fts USING fts5(
                id UNINDEXED,
                name,
                search_text,
                tokenize='unicode61'
            )
        """)
        conn.commit()


def _write_skill_doc(skill_id: str, name: str, triggers: dict, level0: str, level1: str, confidence: float, status: str) -> Path:
    doc_path = _skills_dir(status) / f"{skill_id}.md"
    keywords_json = json.dumps(triggers.get("keywords", []), ensure_ascii=False)
    tools_json = json.dumps(triggers.get("tool_patterns", []), ensure_ascii=False)
    doc_path.write_text(
        f"---\n"
        f"skill_id: {skill_id}\n"
        f"name: {name}\n"
        f"status: {status}\n"
        f"confidence: {confidence:.2f}\n"
        f"triggers:\n"
        f"  keywords: {keywords_json}\n"
        f"  tool_patterns: {tools_json}\n"
        f"created_at: {time.strftime('%Y-%m-%d')}\n"
        f"---\n\n"
        f"## L0\n{level0}\n\n"
        f"## L1\n{level1 or '（暂无详细版本）'}\n",
        encoding="utf-8",
    )
    return doc_path


def _store_skill(
    *,
    name: str,
    triggers: dict,
    level0: str,
    level1: str = "",
    confidence: float = 0,
    source: str = "",
    path: Optional[Path] = None,
) -> Optional[str]:
    init_db(path)
    name = name.strip()[:80]
    level0 = level0.strip()
    if not name or not level0:
        return None
    confidence = max(0.0, min(1.0, float(confidence or 0)))
    skill_id = f"sk_{int(time.time())}_{str(uuid.uuid4())[:8]}"
    doc_path = _write_skill_doc(skill_id, name, triggers, level0, level1, confidence, "pending")
    with sqlite3.connect(path or _db_path()) as conn:
        conn.execute(
            """
            INSERT INTO skill_candidates
              (id, name, triggers_json, level0, level1, doc_path, confidence, status, created_at, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                skill_id, name,
                json.dumps(triggers, ensure_ascii=False),
                level0, level1, str(doc_path),
                confidence, time.time(), source[:500],
            ),
        )
        conn.commit()
    return skill_id


def skill_scan(
    limit_messages: int = 120,
    limit_logs: int = 80,
    path: Optional[Path] = None,
) -> dict:
    """Scan recent conversation history and extract pending skill candidates."""
    from .. import evolution as _evo
    init_db(path)
    messages = _evo._recent_messages(limit_messages, path=path)
    logs = _evo._recent_tool_logs(limit_logs)
    if not messages and not logs:
        return {"ok": True, "skills_added": 0, "note": "no history or logs to scan"}

    prompt = _SCAN_PROMPT.format(
        messages=json.dumps(messages[-120:], ensure_ascii=False),
        logs=json.dumps(logs[-80:], ensure_ascii=False),
    )
    resp = llm.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    raw = resp["choices"][0]["message"]["content"].strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1][4:] if len(parts) > 1 and parts[1].startswith("json") else parts[1]
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        items = []
    if not isinstance(items, list):
        items = []

    added: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        sid = _store_skill(
            name=str(item.get("name") or ""),
            triggers=item.get("triggers") if isinstance(item.get("triggers"), dict) else {},
            level0=str(item.get("level0") or ""),
            level1=str(item.get("level1") or ""),
            confidence=float(item.get("confidence") or 0),
            source=str(item.get("evidence") or ""),
            path=path,
        )
        if sid:
            added.append(sid)
    return {"ok": True, "skills_added": len(added), "ids": added}


def list_skills(
    status: str = "pending",
    path: Optional[Path] = None,
    limit: int = 50,
) -> list[dict]:
    init_db(path)
    if status not in STATUSES and status != "all":
        status = "pending"
    where = "" if status == "all" else "WHERE status = ?"
    params: list[Any] = ([] if status == "all" else [status]) + [limit]
    with sqlite3.connect(path or _db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT id, name, triggers_json, level0, confidence, status, created_at, applied_at "
            f"FROM skill_candidates {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_skill(skill_id: str, path: Optional[Path] = None) -> Optional[dict]:
    init_db(path)
    with sqlite3.connect(path or _db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM skill_candidates WHERE id = ?", (skill_id,)
        ).fetchone()
    return dict(row) if row else None


def apply_skill(skill_id: str, path: Optional[Path] = None) -> dict:
    init_db(path)
    with sqlite3.connect(path or _db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM skill_candidates WHERE id = ?", (skill_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "skill not found"}
        if row["status"] != "pending":
            return {"ok": False, "error": f"skill is already {row['status']}"}
        row = dict(row)

    old_path = Path(row["doc_path"]) if row.get("doc_path") else None
    new_path = _skills_dir("applied") / f"{skill_id}.md"
    if old_path and old_path.exists():
        shutil.move(str(old_path), str(new_path))
        content = new_path.read_text(encoding="utf-8")
        new_path.write_text(content.replace("status: pending", "status: applied"), encoding="utf-8")

    now = time.time()
    triggers = json.loads(row.get("triggers_json") or "{}")
    search_text = " ".join([
        row.get("name", ""),
        row.get("level0", ""),
        " ".join(triggers.get("keywords", [])),
    ])
    with sqlite3.connect(path or _db_path()) as conn:
        conn.execute(
            "UPDATE skill_candidates SET status='applied', applied_at=?, doc_path=? WHERE id=?",
            (now, str(new_path), skill_id),
        )
        conn.execute("DELETE FROM skill_fts WHERE id = ?", (skill_id,))
        conn.execute(
            "INSERT INTO skill_fts (id, name, search_text) VALUES (?, ?, ?)",
            (skill_id, row["name"], search_text),
        )
        conn.commit()
    return {"ok": True, "id": skill_id}


def reject_skill(skill_id: str, path: Optional[Path] = None) -> dict:
    init_db(path)
    with sqlite3.connect(path or _db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status, doc_path FROM skill_candidates WHERE id=?", (skill_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "skill not found"}
        if row["status"] != "pending":
            return {"ok": False, "error": f"skill is {row['status']}, not pending"}
        old_path = Path(row["doc_path"]) if row["doc_path"] else None
        if old_path and old_path.exists():
            new_path = _skills_dir("rejected") / old_path.name
            shutil.move(str(old_path), str(new_path))
            conn.execute(
                "UPDATE skill_candidates SET status='rejected', doc_path=? WHERE id=?",
                (str(new_path), skill_id),
            )
        else:
            conn.execute("UPDATE skill_candidates SET status='rejected' WHERE id=?", (skill_id,))
        conn.commit()
    return {"ok": True, "id": skill_id}


def disable_skill(skill_id: str, path: Optional[Path] = None) -> dict:
    init_db(path)
    with sqlite3.connect(path or _db_path()) as conn:
        cur = conn.execute(
            "UPDATE skill_candidates SET status='disabled' WHERE id=? AND status='applied'",
            (skill_id,),
        )
        conn.execute("DELETE FROM skill_fts WHERE id=?", (skill_id,))
        conn.commit()
    if cur.rowcount == 0:
        return {"ok": False, "error": "applied skill not found"}
    return {"ok": True, "id": skill_id}


def rollback_skill(skill_id: str, path: Optional[Path] = None) -> dict:
    init_db(path)
    with sqlite3.connect(path or _db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM skill_candidates WHERE id=?", (skill_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "skill not found"}
        if row["status"] == "pending":
            return {"ok": False, "error": "skill is already pending"}
        row = dict(row)

        old_path = Path(row["doc_path"]) if row.get("doc_path") else None
        new_path = _skills_dir("pending") / f"{skill_id}.md"
        if old_path and old_path.exists():
            shutil.move(str(old_path), str(new_path))
            content = new_path.read_text(encoding="utf-8")
            new_path.write_text(
                content.replace(f"status: {row['status']}", "status: pending"),
                encoding="utf-8",
            )
        conn.execute(
            "UPDATE skill_candidates SET status='pending', applied_at=NULL, doc_path=? WHERE id=?",
            (str(new_path), skill_id),
        )
        conn.execute("DELETE FROM skill_fts WHERE id=?", (skill_id,))
        conn.commit()
    return {"ok": True, "id": skill_id}


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    ascii_tok = {t for t in re.findall(r"[a-z0-9_]{2,}", lowered)}
    cjk_tok = {lowered[i:i+2] for i in range(max(0, len(lowered)-1)) if "一" <= lowered[i] <= "鿿"}
    return ascii_tok | cjk_tok


def match_skills(user_text: str, limit: int = 3, path: Optional[Path] = None) -> list[dict]:
    """Return top-k applied skills most relevant to user_text, with match scores."""
    init_db(path)
    with sqlite3.connect(path or _db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, name, triggers_json, level0, level1, confidence "
            "FROM skill_candidates WHERE status='applied'",
        ).fetchall()
    if not rows:
        return []
    text_tokens = _tokens(user_text)
    scored: list[tuple[int, float, dict]] = []
    for row in rows:
        d = dict(row)
        triggers = json.loads(d.get("triggers_json") or "{}")
        keywords = triggers.get("keywords", [])
        tool_patterns = triggers.get("tool_patterns", [])
        haystack = f"{d['name']} {d['level0']} {' '.join(keywords)}"
        score = len(text_tokens & _tokens(haystack))
        for t in tool_patterns:
            if t in user_text:
                score += 2
        if score > 0:
            scored.append((score, float(d.get("confidence") or 0), d))
    scored.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return [{**d, "match_score": s} for s, _, d in scored[:limit]]


def _all_applied_skills(path: Optional[Path] = None) -> list[dict]:
    """Return all applied skills (name + keywords only, for index use)."""
    init_db(path)
    with sqlite3.connect(path or _db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, triggers_json FROM skill_candidates WHERE status='applied'",
        ).fetchall()
    return [dict(r) for r in rows]


def build_skill_context(user_text: str, path: Optional[Path] = None, strong_threshold: int = 4) -> str:
    """Build compact skill context for system prompt injection.

    - Matched skills (score > 0): inject full L0 steps; L1 if strong match.
    - Unmatched applied skills: inject a compact index so the LLM can reason
      about intent and activate them even on fuzzy / ambiguous input.
    """
    matched = match_skills(user_text, limit=3, path=path)
    matched_names = {s["name"] for s in matched}

    lines: list[str] = []

    if matched:
        lines += [
            "[可用技能]",
            "以下技能与当前请求相关，请按步骤执行：",
        ]
        for skill in matched:
            is_strong = skill["match_score"] >= strong_threshold
            l1 = (skill.get("level1") or "").strip()
            body = l1 if is_strong and l1 else skill["level0"].strip()
            label = "（详细版）" if is_strong and l1 else ""
            lines.append(f"\n【{skill['name']}】{label}")
            lines.append(body)

    # Compact index for skills that didn't match by keyword —
    # lets the LLM pick them up when user intent is fuzzy.
    others = [s for s in _all_applied_skills(path) if s["name"] not in matched_names]
    if others:
        lines.append("\n[其他可用技能 — 根据用户意图自行判断是否适用]")
        for s in others:
            triggers = json.loads(s.get("triggers_json") or "{}")
            kws = "、".join(triggers.get("keywords", [])[:6])
            lines.append(f"- 【{s['name']}】触发词：{kws}")

    return "\n".join(lines) if lines else ""


init_db()
