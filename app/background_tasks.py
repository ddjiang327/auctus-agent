"""后台异步任务队列 + worker 池。

镜像 cronjobs 的进程内调度模式（daemon 线程 + 停止事件），但这是一个持久化任务队列：

  enqueue() 入库 → worker 线程认领 queued 任务 → 跑 agent.chat(..., task_mode_active=True)
  → 存结果 → 标记 done/error → notify.dispatch_* 推送完成。

- 持久化：bg_jobs 表（重启不丢）。启动时把孤儿 running 复位为 queued（崩溃安全）。
- 并发：settings.bg_task_concurrency 个 worker（小并发，控制 LLM 成本）。
- 认领原子性：进程内 worker，用一把锁包住「选最旧 queued + 改 running」即可。
- 会话隔离：默认用 bg-<job_id> 作为 session_id，不与交互式会话冲突。
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from typing import Optional

from .config import settings
from . import notify

log = logging.getLogger(__name__)

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"

_RESULT_CLIP = 4000

_workers: list[threading.Thread] = []
_stop_event = threading.Event()
_wake_event = threading.Event()  # poked on enqueue so idle workers pick up immediately
_claim_lock = threading.Lock()
_IDLE_POLL_SECONDS = 5


def _db_path():
    return settings.data_dir / "secretary.db"


def _ensure_store() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bg_jobs (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                prompt TEXT,
                status TEXT,
                result TEXT,
                error TEXT,
                created_at REAL,
                started_at REAL,
                finished_at REAL
            )
            """
        )


def _row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "session_id": row[1],
        "prompt": row[2],
        "status": row[3],
        "result": row[4],
        "error": row[5],
        "created_at": row[6],
        "started_at": row[7],
        "finished_at": row[8],
    }


_COLUMNS = "id, session_id, prompt, status, result, error, created_at, started_at, finished_at"


# ── public queue API ──────────────────────────────────────────────────────────

def enqueue(prompt: str, session_id: Optional[str] = None) -> dict:
    """Queue a background task. Validates input at this boundary."""
    cleaned = (prompt or "").strip()
    if not cleaned:
        raise ValueError("prompt is empty")
    _ensure_store()
    job_id = uuid.uuid4().hex[:12]
    session_id = (session_id or "").strip() or f"bg-{job_id}"
    created_at = time.time()
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            f"INSERT INTO bg_jobs ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, session_id, cleaned, STATUS_QUEUED, None, None, created_at, None, None),
        )
    _wake_event.set()
    return get_job(job_id)


def get_job(job_id: str) -> Optional[dict]:
    _ensure_store()
    with sqlite3.connect(_db_path()) as conn:
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM bg_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_jobs(limit: int = 30) -> list[dict]:
    _ensure_store()
    limit = max(1, min(int(limit or 30), 100))
    with sqlite3.connect(_db_path()) as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM bg_jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ── worker internals ───────────────────────────────────────────────────────────

def _claim_next_job() -> Optional[dict]:
    """Atomically claim the oldest queued job and mark it running."""
    with _claim_lock:
        with sqlite3.connect(_db_path()) as conn:
            row = conn.execute(
                "SELECT id, session_id, prompt FROM bg_jobs WHERE status = ? "
                "ORDER BY created_at ASC LIMIT 1",
                (STATUS_QUEUED,),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE bg_jobs SET status = ?, started_at = ? WHERE id = ?",
                (STATUS_RUNNING, time.time(), row[0]),
            )
        return {"id": row[0], "session_id": row[1], "prompt": row[2]}


def _finish(job_id: str, status: str, *, result: str = "", error: str = "") -> None:
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            "UPDATE bg_jobs SET status = ?, result = ?, error = ?, finished_at = ? WHERE id = ?",
            (status, (result or "")[:_RESULT_CLIP], (error or "")[:_RESULT_CLIP], time.time(), job_id),
        )


def _run_job(job: dict) -> None:
    session_id = job["session_id"]
    prompt = job["prompt"]
    try:
        from . import agent, task_mode  # local import — avoid circular import at module load
        # Register a Task Mode entry so the execution live-view (/api/task/{id}/live)
        # shows steps, activity timeline and auto-screenshots for this background task.
        task_mode.create_or_resume(session_id, prompt)
        result = agent.chat(session_id, prompt, task_mode_active=True)
        reply = result.get("reply", "") if isinstance(result, dict) else str(result)
        try:
            task_mode.mark_after_reply(session_id, reply)
            reply = task_mode.strip_update_markers(reply)
        except Exception as exc:
            log.warning("task_mode update failed for %s: %s", job["id"], exc)
        _finish(job["id"], STATUS_DONE, result=reply)
        try:
            notify.dispatch_task_done(session_id, prompt, reply)
        except Exception as exc:
            log.warning("notify dispatch (done) failed for %s: %s", job["id"], exc)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        log.warning("bg job %s failed: %s", job["id"], err)
        _finish(job["id"], STATUS_ERROR, error=err)
        try:
            notify.dispatch_task_failed(session_id, prompt, err)
        except Exception as exc2:
            log.warning("notify dispatch (failed) failed for %s: %s", job["id"], exc2)


def _worker_loop() -> None:
    while not _stop_event.is_set():
        job = _claim_next_job()
        if job is None:
            _wake_event.wait(timeout=_IDLE_POLL_SECONDS)
            _wake_event.clear()
            continue
        _run_job(job)


def _recover_orphans() -> None:
    """On startup, requeue jobs left 'running' by a previous crash/shutdown."""
    _ensure_store()
    with sqlite3.connect(_db_path()) as conn:
        cur = conn.execute(
            "UPDATE bg_jobs SET status = ?, started_at = NULL WHERE status = ?",
            (STATUS_QUEUED, STATUS_RUNNING),
        )
        if cur.rowcount:
            log.info("Recovered %d orphaned background job(s) → queued", cur.rowcount)


def start_workers() -> None:
    """Start the worker pool. Safe to call multiple times (idempotent)."""
    global _workers
    if any(t.is_alive() for t in _workers):
        return
    _ensure_store()
    _recover_orphans()
    _stop_event.clear()
    count = max(1, min(int(settings.bg_task_concurrency or 2), 5))
    _workers = []
    for i in range(count):
        t = threading.Thread(target=_worker_loop, daemon=True, name=f"bg-worker-{i + 1}")
        t.start()
        _workers.append(t)
    log.info("Background task workers started (concurrency=%d)", count)


def stop_workers() -> None:
    """Signal all worker loops to exit."""
    _stop_event.set()
    _wake_event.set()
