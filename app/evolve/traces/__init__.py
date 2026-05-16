"""Trace subsystem for the closed learning loop.

Collects structured trace sessions from tool-call logs and conversation
messages, normalizes them into TraceSession/TraceEvent records, and writes
them to JSONL storage for later use by eval and skill generation.
"""

from .schema import TraceSession, TraceEvent
from .collector import collect_traces, collect_one_trace
from .store import TraceStore

__all__ = [
    "TraceSession",
    "TraceEvent",
    "collect_traces",
    "collect_one_trace",
    "TraceStore",
]
