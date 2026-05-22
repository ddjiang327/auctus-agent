"""User-defined skills created via conversation.

Skills are prompt snippets the agent injects into every turn to apply
user-defined behavior rules. They are created from natural language
descriptions in the chat, and stored in data/skills.json.

Distinct from:
- Playbooks (trigger-regex → inject): precise keyword-match, managed in UI
- Auto-skills (evolve/skills.py): automatically learned from sessions
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .config import settings

_SKILLS_PATH: Path = settings.data_dir / "skills.json"


def _read() -> list[dict]:
    if not _SKILLS_PATH.exists():
        return []
    try:
        data = json.loads(_SKILLS_PATH.read_text(encoding="utf-8"))
        return data.get("skills", [])
    except Exception:
        return []


def _write(skills: list[dict]) -> None:
    _SKILLS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SKILLS_PATH.write_text(
        json.dumps({"skills": skills}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_skills() -> list[dict]:
    return _read()


def create_skill(
    name: str,
    description: str,
    prompt_content: str,
    trigger_keywords: list[str] | None = None,
) -> dict:
    skill = {
        "id": uuid.uuid4().hex[:10],
        "name": name.strip(),
        "description": description.strip(),
        "trigger_keywords": [k.strip() for k in (trigger_keywords or []) if k.strip()],
        "prompt_content": prompt_content.strip(),
        "enabled": True,
        "created_at": time.time(),
    }
    skills = _read()
    skills.append(skill)
    _write(skills)
    return skill


def toggle_skill(skill_id: str) -> dict:
    skills = _read()
    for s in skills:
        if s["id"] == skill_id:
            s["enabled"] = not s.get("enabled", True)
    _write(skills)
    return next((s for s in skills if s["id"] == skill_id), {})


def delete_skill(skill_id: str) -> bool:
    skills = _read()
    new_skills = [s for s in skills if s["id"] != skill_id]
    _write(new_skills)
    return len(new_skills) < len(skills)


def context_for_all_enabled() -> str:
    """Return a system-prompt block injecting all enabled skill instructions."""
    skills = [s for s in _read() if s.get("enabled")]
    if not skills:
        return ""
    parts = ["[用户自定义技能]（以下是用户定义的行为规则，请在相关场景下遵守）"]
    for s in skills:
        name = s.get("name", "")
        content = (s.get("prompt_content") or "").strip()
        if content:
            parts.append(f"### {name}\n{content}")
    return "\n\n".join(parts) if len(parts) > 1 else ""
