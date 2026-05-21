"""Lightweight user preference profile.

All foreground reads are local setup_state lookups. Refresh and candidate
generation run after the response in a background thread.
"""
from __future__ import annotations

import re
import time
from typing import Any

from . import accounting, memory


SUMMARY_KEY = "user_preference_summary"
UPDATED_KEY = "user_preference_summary_updated_at"

_PREFERENCE_PATTERNS = (
    r"(?:以后|默认|下次|今后).{0,12}(?:用|按|不要|别|记得|优先)",
    r"(?:我喜欢|我偏好|我希望|我习惯|我不喜欢|我讨厌|我更喜欢)",
    r"(?:prefer|preference|by default|next time|always|never)\b",
)


def get_summary(max_chars: int = 800) -> str:
    state = accounting.get_setup_state()
    summary = (state.get(SUMMARY_KEY) or "").strip()
    if not summary:
        return ""
    return summary[:max(120, min(max_chars, 1200))]


def context(max_chars: int = 800) -> str:
    summary = get_summary(max_chars=max_chars)
    if not summary:
        return ""
    return f"[用户偏好摘要]\n{summary}\n"


def refresh_summary(max_items: int = 16) -> dict[str, Any]:
    """Build a compact preference profile from confirmed local facts."""
    facts = memory.list_memories(confirmed=1, limit=80)
    selected = []
    for fact in facts:
        ftype = fact.get("type") or ""
        importance = int(fact.get("importance") or 0)
        tags = (fact.get("tags") or "").lower()
        if ftype in {"preference", "rule"} or importance >= 4 or "user_profile" in tags or "persona" in tags:
            title = (fact.get("title") or "").strip()
            content = (fact.get("content") or "").strip()
            if content:
                selected.append((importance, f"- {title}: {content}" if title else f"- {content}"))
        if len(selected) >= max_items:
            break
    selected.sort(key=lambda x: x[0], reverse=True)
    summary = "\n".join(line for _, line in selected[:max_items])
    if len(summary) > 800:
        summary = summary[:797].rstrip() + "..."
    accounting.set_setup_state({
        SUMMARY_KEY: summary,
        UPDATED_KEY: str(time.time()),
    })
    return {"ok": True, "summary": summary, "items": len(selected)}


def maybe_store_preference_candidate(user_text: str, reply: str = "") -> dict[str, Any]:
    """Create a candidate memory only for explicit preference-like statements."""
    text = (user_text or "").strip()
    if not text or len(text) > 700:
        return {"ok": True, "created": False}
    lowered = text.lower()
    if not any(re.search(pattern, lowered, re.IGNORECASE) for pattern in _PREFERENCE_PATTERNS):
        return {"ok": True, "created": False}
    value = text[:500]
    fact_id = memory.store_candidate(
        key="User preference candidate",
        value=value,
        tags=["auto_review", "preference"],
        type="preference",
        importance=4,
    )
    return {"ok": True, "created": True, "memory_id": fact_id}
