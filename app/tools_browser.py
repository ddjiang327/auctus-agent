"""Playwright-based browser automation tools.

Design:
- One singleton browser session per agent process (cheaper than start-per-call).
- Chromium is downloaded lazily on first use into a path the standalone app can write to
  (`data/playwright_browsers/`), so the PyInstaller binary doesn't have to ship a 150MB
  chromium upfront.
- All tools return plain dicts and degrade gracefully when Playwright is missing.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from .config import settings


log = logging.getLogger(__name__)

# Single shared session for the lifetime of the process
_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "ready": False,
    "playwright": None,
    "browser": None,
    "context": None,
    "page": None,
    "install_attempted": False,
}


def _browsers_dir() -> Path:
    path = (settings.data_dir / "playwright_browsers").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ensure_chromium_installed() -> Optional[str]:
    """Download Chromium on first use. Returns an error string on failure, None on success."""
    if _STATE["install_attempted"]:
        return None  # already tried; either succeeded or we'll find out on launch
    _STATE["install_attempted"] = True

    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_browsers_dir())

    cand = _browsers_dir()
    has_existing = any(p.name.startswith("chromium") and p.is_dir() and any(p.iterdir()) for p in cand.iterdir())
    if has_existing:
        log.info("Chromium already installed at %s", cand)
        return None

    log.info("Downloading Chromium for Playwright (one-time, ~150MB) ...")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            timeout=600,
            env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(cand)},
        )
        if proc.returncode != 0:
            return f"playwright install failed: {proc.stderr.strip() or proc.stdout.strip()}"
    except subprocess.TimeoutExpired:
        return "playwright install timed out after 10 minutes"
    except Exception as exc:
        return f"playwright install error: {type(exc).__name__}: {exc}"
    return None


def _ensure_session() -> Optional[str]:
    """Lazy-init the browser session. Returns error string on failure, None on success."""
    if _STATE["ready"]:
        return None
    with _LOCK:
        if _STATE["ready"]:
            return None

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return "Playwright not installed. Run: pip install playwright"

        install_err = _ensure_chromium_installed()
        if install_err:
            return install_err

        try:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_browsers_dir())
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=bool(getattr(settings, "browser_headless", True)))
            context = browser.new_context(
                user_agent="Mozilla/5.0 (compatible; AuctusAgent/0.1)",
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            _STATE.update(
                {"ready": True, "playwright": pw, "browser": browser, "context": context, "page": page}
            )
            return None
        except Exception as exc:
            return f"failed to start browser: {type(exc).__name__}: {exc}"


def _close_session_internal() -> None:
    """Tear down singleton (used by browser_close and process shutdown)."""
    if not _STATE["ready"]:
        return
    try:
        if _STATE.get("context"):
            _STATE["context"].close()
        if _STATE.get("browser"):
            _STATE["browser"].close()
        if _STATE.get("playwright"):
            _STATE["playwright"].stop()
    except Exception as exc:
        log.warning("Error closing browser session: %s", exc)
    finally:
        _STATE.update({"ready": False, "playwright": None, "browser": None, "context": None, "page": None})


# ─────────────────────────── public tool functions ────────────────────────────


def browser_open(url: str, wait_for: str = "domcontentloaded") -> dict:
    """Open a URL in a headless browser. JS is fully executed."""
    err = _ensure_session()
    if err:
        return {"error": err}
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"error": "url must start with http:// or https://"}
    try:
        page = _STATE["page"]
        wait_until = wait_for if wait_for in {"load", "domcontentloaded", "networkidle"} else "domcontentloaded"
        page.goto(url, wait_until=wait_until, timeout=12_000)
        return {"ok": True, "url": page.url, "title": page.title()}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def browser_read(selector: str = "", max_chars: int = 8000) -> dict:
    """Return readable text from the current page. If selector is given, only that element's text."""
    if not _STATE["ready"]:
        return {"error": "no page open. Call browser_open first."}
    max_chars = min(max(int(max_chars or 8000), 500), 30_000)
    try:
        page = _STATE["page"]
        if selector:
            element = page.query_selector(selector)
            if element is None:
                return {"error": f"selector not found: {selector}"}
            text = (element.inner_text() or "").strip()
        else:
            text = (page.evaluate("() => document.body.innerText") or "").strip()
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars] + f"\n\n[truncated to {max_chars} characters]"
        return {"url": page.url, "title": page.title(), "text": text, "truncated": truncated}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def browser_click(selector: str, timeout_ms: int = 5000) -> dict:
    """Click the first element matching the CSS selector."""
    if not _STATE["ready"]:
        return {"error": "no page open. Call browser_open first."}
    selector = (selector or "").strip()
    if not selector:
        return {"error": "selector required"}
    try:
        page = _STATE["page"]
        page.click(selector, timeout=int(timeout_ms))
        return {"ok": True, "url": page.url, "title": page.title()}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def browser_type(selector: str, text: str, submit: bool = False) -> dict:
    """Fill text into an input matching the selector. If submit=True, press Enter after typing."""
    if not _STATE["ready"]:
        return {"error": "no page open. Call browser_open first."}
    selector = (selector or "").strip()
    if not selector:
        return {"error": "selector required"}
    try:
        page = _STATE["page"]
        page.fill(selector, text or "")
        if submit:
            page.press(selector, "Enter")
        return {"ok": True, "url": page.url}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def browser_screenshot(filename: str = "") -> dict:
    """Save a PNG screenshot of the current page to outputs/. Returns the saved path."""
    if not _STATE["ready"]:
        return {"error": "no page open. Call browser_open first."}
    name = (filename or "").strip() or "screenshot.png"
    if not name.lower().endswith(".png"):
        name = name + ".png"
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:80] or "screenshot.png"
    out_path = (settings.output_dir / safe).resolve()
    try:
        _STATE["page"].screenshot(path=str(out_path), full_page=False)
        return {"ok": True, "path": str(out_path), "size_bytes": out_path.stat().st_size}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def browser_close() -> dict:
    """Close the current browser session (releases Chromium memory)."""
    _close_session_internal()
    return {"ok": True}


def browser_wait(selector: str, timeout_ms: int = 10_000, state: str = "visible") -> dict:
    """Wait until an element matching `selector` reaches `state` (visible/attached/hidden/detached) or timeout."""
    if not _STATE["ready"]:
        return {"error": "no page open. Call browser_open first."}
    selector = (selector or "").strip()
    if not selector:
        return {"error": "selector required"}
    valid = {"visible", "attached", "hidden", "detached"}
    if state not in valid:
        state = "visible"
    try:
        _STATE["page"].wait_for_selector(selector, state=state, timeout=int(timeout_ms))
        return {"ok": True, "selector": selector, "state": state}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def browser_back() -> dict:
    """Go back one page in history."""
    if not _STATE["ready"]:
        return {"error": "no page open. Call browser_open first."}
    try:
        page = _STATE["page"]
        page.go_back(timeout=10_000)
        return {"ok": True, "url": page.url, "title": page.title()}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def browser_forward() -> dict:
    """Go forward one page in history."""
    if not _STATE["ready"]:
        return {"error": "no page open. Call browser_open first."}
    try:
        page = _STATE["page"]
        page.go_forward(timeout=10_000)
        return {"ok": True, "url": page.url, "title": page.title()}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def browser_scroll(direction: str = "down", pixels: int = 600) -> dict:
    """Scroll the current page. direction: up | down | top | bottom. pixels only used for up/down."""
    if not _STATE["ready"]:
        return {"error": "no page open. Call browser_open first."}
    direction = (direction or "down").lower().strip()
    try:
        page = _STATE["page"]
        if direction == "top":
            page.evaluate("() => window.scrollTo(0, 0)")
        elif direction == "bottom":
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        elif direction == "up":
            page.evaluate("(y) => window.scrollBy(0, -y)", int(pixels))
        else:
            page.evaluate("(y) => window.scrollBy(0, y)", int(pixels))
        return {"ok": True, "direction": direction}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def browser_evaluate(script: str) -> dict:
    """Run a JavaScript expression on the current page and return its result.

    Example scripts:
      "() => document.title"
      "() => document.querySelectorAll('article').length"
      "() => Array.from(document.querySelectorAll('h2')).map(h => h.innerText)"
    """
    if not _STATE["ready"]:
        return {"error": "no page open. Call browser_open first."}
    script = (script or "").strip()
    if not script:
        return {"error": "script required"}
    try:
        result = _STATE["page"].evaluate(script)
        text = str(result)
        truncated = len(text) > 8000
        if truncated:
            text = text[:8000] + " [truncated]"
        return {"ok": True, "result": text, "truncated": truncated}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
