"""Bounded parallel sub-agent runner for research fan-out."""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from typing import Any


MAX_SUBTASKS = 3
MAX_TIMEOUT_SECONDS = 90


def run_parallel_subagents(
    tasks: list[str],
    expected_output: str = "",
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Run up to three independent research subtasks in parallel."""
    cleaned = [str(task).strip() for task in tasks if str(task).strip()]
    if not cleaned:
        return {"error": "tasks must contain at least one non-empty subtask"}
    cleaned = cleaned[:MAX_SUBTASKS]
    timeout_seconds = max(10, min(int(timeout_seconds or 60), MAX_TIMEOUT_SECONDS))

    started = time.time()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(cleaned)) as pool:
        futures = {
            pool.submit(_run_one, idx, task, expected_output): (idx, task)
            for idx, task in enumerate(cleaned, start=1)
        }
        try:
            for future in as_completed(futures, timeout=timeout_seconds):
                idx, task = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append({
                        "index": idx,
                        "task": task,
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
        except TimeoutError:
            for future, (idx, task) in futures.items():
                if not future.done():
                    future.cancel()
                    results.append({
                        "index": idx,
                        "task": task,
                        "ok": False,
                        "error": f"subagent timed out after {timeout_seconds}s",
                    })

    results.sort(key=lambda item: int(item.get("index") or 0))
    return {
        "ok": True,
        "count": len(results),
        "timeout_seconds": timeout_seconds,
        "elapsed_seconds": round(time.time() - started, 2),
        "results": results,
    }


def _run_one(index: int, task: str, expected_output: str) -> dict[str, Any]:
    from . import agent

    session_id = f"subagent-{uuid.uuid4().hex[:12]}"
    context = (
        "[SUBAGENT]\n"
        "你是主 Agent 启动的受限子任务执行器。只完成当前子任务，不要再启动 sub-agent。"
        "优先给结构化要点、来源、风险和待核验项。"
    )
    if expected_output:
        context += f"\n期望输出：{expected_output[:500]}"
    result = agent.chat(session_id, task, extra_system_context=context, allow_tools=True)
    return {
        "index": index,
        "task": task,
        "session_id": session_id,
        "ok": not bool(result.get("stopped")),
        "reply": str(result.get("reply") or "")[:6000],
        "files": result.get("files") or [],
    }
