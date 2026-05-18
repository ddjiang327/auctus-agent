"""Managed long-running terminal sessions for local CLI tasks."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


MAX_BUFFER_LINES = 2000


@dataclass
class TerminalSession:
    id: str
    command: str
    working_directory: str
    created_at: float
    process: subprocess.Popen[str]
    label: str = ""
    output: deque[dict] = field(default_factory=lambda: deque(maxlen=MAX_BUFFER_LINES))
    lock: threading.Lock = field(default_factory=threading.Lock)
    exit_code: Optional[int] = None
    ended_at: Optional[float] = None

    @property
    def status(self) -> str:
        code = self.process.poll()
        if code is None:
            return "running"
        self.exit_code = code
        if self.ended_at is None:
            self.ended_at = time.time()
        return "exited"

    def append_line(self, stream: str, line: str) -> None:
        with self.lock:
            self.output.append({
                "ts": time.time(),
                "stream": stream,
                "text": line.rstrip("\n"),
            })

    def snapshot(self, tail: int = 80) -> dict:
        status = self.status
        with self.lock:
            lines = list(self.output)[-max(1, min(int(tail or 80), 500)):]
        return {
            "id": self.id,
            "label": self.label,
            "command": self.command,
            "working_directory": self.working_directory,
            "created_at": self.created_at,
            "status": status,
            "exit_code": self.exit_code,
            "ended_at": self.ended_at,
            "tail": lines,
        }


_sessions: dict[str, TerminalSession] = {}
_sessions_lock = threading.Lock()


def start(command: str, working_directory: Path, label: str = "") -> dict:
    command = (command or "").strip()
    if not command:
        return {"error": "empty command"}
    cwd = str(working_directory.resolve())
    session_id = f"term-{uuid.uuid4().hex[:10]}"
    process = subprocess.Popen(
        ["/bin/zsh", "-lc", command],
        cwd=cwd,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
        start_new_session=True,
    )
    session = TerminalSession(
        id=session_id,
        command=command,
        working_directory=cwd,
        created_at=time.time(),
        process=process,
        label=(label or "").strip(),
    )
    with _sessions_lock:
        _sessions[session_id] = session
    _start_reader(session, "stdout", process.stdout)
    _start_reader(session, "stderr", process.stderr)
    threading.Thread(target=_waiter, args=(session,), daemon=True).start()
    return session.snapshot(tail=20)


def list_sessions() -> dict:
    with _sessions_lock:
        sessions = list(_sessions.values())
    return {
        "sessions": [
            {
                "id": s.id,
                "label": s.label,
                "command": s.command,
                "working_directory": s.working_directory,
                "created_at": s.created_at,
                "status": s.status,
                "exit_code": s.exit_code,
                "ended_at": s.ended_at,
            }
            for s in sorted(sessions, key=lambda item: item.created_at, reverse=True)
        ]
    }


def tail(session_id: str, lines: int = 80) -> dict:
    session = _get(session_id)
    if session is None:
        return {"error": f"session not found: {session_id}"}
    return session.snapshot(tail=lines)


def send(session_id: str, text: str, append_newline: bool = True) -> dict:
    session = _get(session_id)
    if session is None:
        return {"error": f"session not found: {session_id}"}
    if session.status != "running":
        return {"error": f"session is not running: {session_id}", "status": session.status}
    if session.process.stdin is None:
        return {"error": "session stdin is unavailable"}
    data = text or ""
    if append_newline and not data.endswith("\n"):
        data += "\n"
    session.process.stdin.write(data)
    session.process.stdin.flush()
    return {"ok": True, "id": session_id, "sent_chars": len(data)}


def stop(session_id: str, force: bool = False) -> dict:
    session = _get(session_id)
    if session is None:
        return {"error": f"session not found: {session_id}"}
    if session.status != "running":
        return session.snapshot(tail=20)
    try:
        if force:
            os.killpg(session.process.pid, signal.SIGKILL)
        else:
            os.killpg(session.process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "id": session_id, "status": "stopping", "force": force}


def _get(session_id: str) -> Optional[TerminalSession]:
    with _sessions_lock:
        return _sessions.get(session_id)


def _start_reader(session: TerminalSession, stream_name: str, pipe) -> None:
    def run() -> None:
        if pipe is None:
            return
        try:
            for line in pipe:
                session.append_line(stream_name, line)
        except Exception as exc:
            session.append_line("system", f"[reader error: {type(exc).__name__}: {exc}]")

    threading.Thread(target=run, daemon=True).start()


def _waiter(session: TerminalSession) -> None:
    code = session.process.wait()
    session.exit_code = code
    session.ended_at = time.time()
    session.append_line("system", f"[process exited with code {code}]")
