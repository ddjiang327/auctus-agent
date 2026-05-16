"""Trace storage: JSONL file I/O for TraceSessions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ...config import settings
from .schema import TraceSession


class TraceStore:
    """Persist TraceSessions to JSONL files under data/evolve/traces/sessions/."""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or (settings.data_dir / "evolve" / "traces" / "sessions")
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, session: TraceSession) -> Path:
        """Append one TraceSession as a JSONL line."""
        path = self.base_dir / f"{session.session_id}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(session.to_dict(), ensure_ascii=False) + "\n")
        return path

    def load(self, session_id: str) -> Optional[TraceSession]:
        """Load the latest trace for a session_id."""
        path = self.base_dir / f"{session_id}.jsonl"
        if not path.exists():
            return None

        lines = [
            line for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not lines:
            return None

        try:
            data = json.loads(lines[-1])
        except json.JSONDecodeError:
            return None

        from .schema import TraceEvent
        events = [TraceEvent(**e) for e in data.pop("events", [])]
        session = TraceSession(**data)
        session.events = events
        return session

    def list_sessions(self, limit: int = 100) -> list[str]:
        """List available session IDs."""
        paths = sorted(
            self.base_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return [p.stem for p in paths[:limit]]

    def count(self) -> int:
        """Total number of trace sessions stored."""
        return len(list(self.base_dir.glob("*.jsonl")))
