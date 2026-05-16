"""Trace data types for the closed learning loop.

TraceSession  – one complete user task from input to final reply.
TraceEvent    – a single step inside a session (message, tool_call,
                tool_result, permission, error).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class TraceEvent:
    """A single event in a trace session.

    t               – seconds since session start (float).
    type            – "message", "tool_call", "tool_result", "permission", "error".
    role            – "user" / "assistant" / "tool" (only for type="message").
    content         – message text (type="message").
    tool            – tool name (type="tool_call" / "tool_result").
    tool_input      – arguments passed to the tool (JSON-decoded dict).
    tool_output     – result returned by the tool.
    ok              – whether the tool call succeeded.
    output_files    – files produced by this step.
    permission_scope – what was authorised (type="permission").
    permission_choice – "once" / "always" / "no".
    error           – error message (type="tool_result" or "error").
    risk_level      – risk classification of the tool call.
    token_usage     – tokens consumed during the step.
    model           – model used.
    """
    t: float
    type: str  # "message" | "tool_call" | "tool_result" | "permission" | "error"
    role: Optional[str] = None
    content: Optional[str] = None
    tool: Optional[str] = None
    tool_input: Optional[dict[str, Any]] = None
    tool_output: Optional[Any] = None
    ok: Optional[bool] = None
    output_files: list[str] = field(default_factory=list)
    permission_scope: Optional[str] = None  # "terminal" | "files" | "calendar"
    permission_choice: Optional[str] = None  # "once" | "always" | "no"
    error: Optional[str] = None
    risk_level: Optional[str] = None
    token_usage: Optional[dict[str, int]] = None
    model: Optional[str] = None


@dataclass
class TraceSession:
    """One complete task session.

    session_id      – the task/session identifier.
    task_summary    – short LLM-generated summary of what the task was about.
    intent_labels   – abstract category labels (e.g., "file_operation", "report_generation").
    started_at      – epoch timestamp.
    ended_at        – epoch timestamp.
    events          – ordered list of TraceEvent.
    outcome         – structured outcome (success, risk_events, artifacts, notes).
    model           – primary model used.
    total_tokens    – sum of token_usage across all events.
    total_cost      – estimated cost (from LiteLLM response_cost or 0).
    """
    session_id: str
    task_summary: str = ""
    intent_labels: list[str] = field(default_factory=list)
    started_at: float = 0.0
    ended_at: float = 0.0
    events: list[TraceEvent] = field(default_factory=list)
    outcome: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    total_tokens: int = 0
    total_cost: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["events"] = [asdict(e) for e in self.events]
        return d

    @property
    def success(self) -> bool:
        return self.outcome.get("success", len(self.events) > 0)

    @property
    def risk_events(self) -> int:
        return self.outcome.get("risk_events", 0)

    @property
    def duration_seconds(self) -> float:
        if self.started_at and self.ended_at:
            return self.ended_at - self.started_at
        return 0.0

    @property
    def tool_count(self) -> int:
        return sum(1 for e in self.events if e.type == "tool_call")

    @property
    def error_count(self) -> int:
        return sum(1 for e in self.events if e.type == "error" or (e.type == "tool_result" and not e.ok))
