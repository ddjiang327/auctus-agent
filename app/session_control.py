"""Per-session concurrency control.

Two primitives keyed by ``session_id``:

- **lock** (``threading.Lock``) — only one ``agent.chat`` may run per session at a time.
  Used by the chat endpoint to serialize, so two concurrent submits don't write
  interleaved messages into history.

- **stop event** (``threading.Event``) — cooperative cancellation signal.
  ``agent._chat_with_tools`` polls this between iterations and bails out when set.
  The UI's Stop button (and the new-message-replaces-old flow) sets it.

Tools that run as a single blocking call (e.g. Chromium download, an HTTP fetch)
won't see the signal until they return — that's fine; we cancel at iteration
boundaries, which fires within seconds in normal use.
"""
from __future__ import annotations

import threading
from typing import Dict


_registry_lock = threading.Lock()
_locks: Dict[str, threading.Lock] = {}
_stop_events: Dict[str, threading.Event] = {}


def get_lock(session_id: str) -> threading.Lock:
    with _registry_lock:
        lock = _locks.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _locks[session_id] = lock
        return lock


def get_stop_event(session_id: str) -> threading.Event:
    with _registry_lock:
        ev = _stop_events.get(session_id)
        if ev is None:
            ev = threading.Event()
            _stop_events[session_id] = ev
        return ev


def request_stop(session_id: str) -> None:
    """Signal the in-flight agent loop (if any) for this session to bail out."""
    get_stop_event(session_id).set()


def reset_stop(session_id: str) -> None:
    """Clear the stop signal so a fresh task can run."""
    get_stop_event(session_id).clear()


def is_stopped(session_id: str) -> bool:
    """Return True iff a stop has been requested and not yet reset."""
    return get_stop_event(session_id).is_set()
