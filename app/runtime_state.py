"""Local runtime state for chat reliability.

The state is intentionally in-memory and cheap. It is used by the UI/API to
explain what the agent is doing without adding model calls.
"""
from __future__ import annotations

import time
from typing import Any


VALID_STATUSES = {"idle", "running", "waiting_for_user", "blocked", "monitoring"}
_STATE: dict[str, dict[str, Any]] = {}


def set_state(session_id: str, status: str, reason: str = "", **extra: Any) -> dict[str, Any]:
    if status not in VALID_STATUSES:
        status = "idle"
    item = {
        "session_id": session_id,
        "status": status,
        "reason": reason,
        "updated_at": time.time(),
    }
    item.update(extra)
    _STATE[session_id] = item
    return item


def get_state(session_id: str) -> dict[str, Any]:
    return _STATE.get(session_id) or {
        "session_id": session_id,
        "status": "idle",
        "reason": "",
        "updated_at": 0.0,
    }


def list_states(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 50), 200))
    return sorted(_STATE.values(), key=lambda x: x.get("updated_at", 0), reverse=True)[:limit]
