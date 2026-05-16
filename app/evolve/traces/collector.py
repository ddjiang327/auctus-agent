"""Trace collector: normalise tool-call logs + messages into TraceSessions."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from ... import llm
from ...config import settings
from .schema import TraceSession, TraceEvent


def _read_tool_logs(task_id: str) -> list[dict]:
    """Read all tool-call log entries for a given task_id."""
    log_path = settings.logs_dir / "tool_calls.jsonl"
    if not log_path.exists():
        return []

    entries: list[dict] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("task_id") == task_id:
            entries.append(entry)
    return entries


def _parse_ts(raw: Any) -> float:
    """Parse a timestamp from the log entry.  Accepts float epoch or ISO-8601 string."""
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt).timestamp()
            except ValueError:
                continue
    return time.time()


def _entries_to_events(entries: list[dict], start_ts: float) -> list[TraceEvent]:
    """Convert raw log entries into ordered TraceEvent list."""
    events: list[TraceEvent] = []
    for entry in entries:
        t = max(0.0, _parse_ts(entry.get("ts")) - start_ts)
        tool_name = entry.get("tool_name", "")
        status = entry.get("status", "")
        error = entry.get("error")
        risk = entry.get("risk_level", "low")
        tool_input = entry.get("tool_input")
        tool_output = entry.get("tool_output")
        token_info = entry.get("token_usage") or {}
        model = entry.get("model", "")

        if isinstance(tool_input, str):
            try:
                tool_input = json.loads(tool_input)
            except (json.JSONDecodeError, TypeError):
                pass
        if isinstance(tool_output, str):
            try:
                tool_output = json.loads(tool_output)
            except (json.JSONDecodeError, TypeError):
                pass

        # Extract output files from tool result
        output_files: list[str] = []
        if isinstance(tool_output, dict):
            for key in ("path", "files"):
                val = tool_output.get(key)
                if isinstance(val, str):
                    output_files.append(val)
                elif isinstance(val, list):
                    output_files.extend(str(v) for v in val)

        events.append(TraceEvent(
            t=t,
            type="tool_call",
            tool=tool_name,
            tool_input=tool_input if isinstance(tool_input, dict) else None,
            ok=status == "ok",
            risk_level=risk,
            token_usage=token_info if isinstance(token_info, dict) else None,
            model=model,
        ))

        events.append(TraceEvent(
            t=t + 0.001,
            type="tool_result",
            tool=tool_name,
            tool_output=tool_output,
            ok=status == "ok",
            output_files=output_files,
            error=error if status != "ok" else None,
        ))

    return events


def _summarize_trace(session: TraceSession) -> TraceSession:
    """Use LLM to generate a task summary and intent labels."""
    if not session.events:
        return session

    tools_used = list({e.tool for e in session.events if e.tool})
    errors = [e.error for e in session.events if e.error]

    prompt = f"""你是一个任务分析器。请用一句简短中文总结这个任务，并给出 1-3 个意图标签。

工具调用序列：{', '.join(tools_used) if tools_used else '（无）'}
是否成功：{'是' if session.success else '否'}
错误信息：{', '.join(errors) if errors else '（无）'}
风险事件数：{session.risk_events}

请输出 JSON：
{{"summary": "一句话总结", "intent": ["tag1", "tag2"]}}
"""

    try:
        resp = llm.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        raw = resp["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        session.task_summary = data.get("summary", "")[:200]
        session.intent_labels = data.get("intent", [])[:5]
    except Exception:
        session.task_summary = f"Task with {len(tools_used)} tools"
        session.intent_labels = tools_used[:3] if tools_used else ["unknown"]

    return session


def collect_one_trace(task_id: str) -> Optional[TraceSession]:
    """Collect a single trace session from tool logs for the given task_id."""
    entries = _read_tool_logs(task_id)
    if not entries:
        return None

    start_ts = min(_parse_ts(e.get("ts")) for e in entries)
    end_ts = max(_parse_ts(e.get("ts")) for e in entries)
    events = _entries_to_events(entries, start_ts)

    total_tokens = sum(
        (e.token_usage or {}).get("total_tokens", 0)
        for e in events
    )

    models = list({e.model for e in events if e.model})
    success = all(
        e.ok for e in events
        if e.type == "tool_result"
    )
    risk_count = sum(
        1 for e in events
        if e.risk_level in ("medium", "high")
    )

    session = TraceSession(
        session_id=task_id,
        started_at=start_ts,
        ended_at=end_ts,
        events=events,
        model=models[0] if models else "",
        total_tokens=total_tokens,
        outcome={
            "success": success,
            "risk_events": risk_count,
            "artifacts": list({
                f
                for e in events
                for f in e.output_files
            }),
        },
    )

    session = _summarize_trace(session)
    return session


def collect_traces(limit: int = 100) -> list[TraceSession]:
    """Collect trace sessions from recent tool logs.

    Groups log entries by task_id, builds TraceSessions, and returns
    the most recent `limit` sessions (successful ones first).
    """
    log_path = settings.logs_dir / "tool_calls.jsonl"
    if not log_path.exists():
        return []

    groups: dict[str, list[dict]] = {}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        tid = entry.get("task_id", "")
        if not tid:
            continue
        groups.setdefault(tid, []).append(entry)

    sessions: list[TraceSession] = []
    for tid in sorted(groups.keys(), reverse=True):
        if len(sessions) >= limit:
            break
        session = collect_one_trace(tid)
        if session:
            sessions.append(session)

    return sessions
