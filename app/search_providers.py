"""Web search provider abstraction.

Default: DuckDuckGo via `ddgs` library (no key, free, rate-limited).
Optional upgrades: Tavily (free tier 1000/mo) or Brave Search (free tier 2000/mo)
when API keys are configured in settings.

All providers return the same shape: list[{"title", "url", "snippet"}].
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from html import unescape
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen

import httpx

from .config import settings


log = logging.getLogger(__name__)


SearchResult = dict[str, str]  # {"title", "url", "snippet"}


def _ddg_search(query: str, max_results: int) -> list[SearchResult]:
    """DuckDuckGo HTML search using stdlib networking.

    Avoid the `ddgs` native networking stack here: on some macOS/Xcode Python
    environments its Rust system-configuration dependency can abort the process.
    """
    request = Request(
        f"https://duckduckgo.com/html/?q={quote_plus(query)}",
        headers={
            "User-Agent": "AuctusAgent/0.1 (+local personal assistant)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=8) as response:
        html = response.read(1_000_000).decode("utf-8", errors="replace")

    results: list[SearchResult] = []
    pattern = re.compile(
        r'<a[^>]+class=["\']result__a["\'][^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>'
        r".*?"
        r'(?:<a[^>]+class=["\']result__snippet["\'][^>]*>(.*?)</a>)?',
        re.I | re.S,
    )
    for href, title_html, snippet_html in pattern.findall(html):
        url = _normalize_ddg_url(unescape(href))
        title = _strip_html(title_html)
        snippet = _strip_html(snippet_html)
        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results


def _normalize_ddg_url(url: str) -> str:
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    return url


def _strip_html(value: str) -> str:
    text = re.sub(r"(?is)<[^>]+>", " ", value or "")
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _tavily_search(query: str, max_results: int, api_key: str) -> list[SearchResult]:
    """Tavily — search optimized for AI agents, returns concise summaries."""
    response = httpx.post(
        "https://api.tavily.com/search",
        json={
            "api_key": api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
        },
        timeout=8,
    )
    response.raise_for_status()
    data = response.json()
    return [
        {
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "snippet": str(item.get("content") or ""),
        }
        for item in data.get("results", [])
    ]


def _brave_search(query: str, max_results: int, api_key: str) -> list[SearchResult]:
    """Brave Search API."""
    response = httpx.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": max_results},
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        },
        timeout=8,
    )
    response.raise_for_status()
    data = response.json()
    results = data.get("web", {}).get("results", [])
    return [
        {
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "snippet": str(item.get("description") or ""),
        }
        for item in results[:max_results]
    ]


def _resolve_provider() -> str:
    """Pick a provider based on settings + available API keys.

    Auto rule: prefer Tavily > Brave > DDG when keys exist; otherwise DDG.
    Explicit setting wins (`search_provider = "tavily"` etc.).
    """
    explicit = (getattr(settings, "search_provider", "") or "auto").lower().strip()
    if explicit in {"tavily", "brave", "ddg", "duckduckgo"}:
        return "duckduckgo" if explicit == "ddg" else explicit
    if getattr(settings, "tavily_api_key", None):
        return "tavily"
    if getattr(settings, "brave_api_key", None):
        return "brave"
    return "duckduckgo"


def search(query: str, max_results: int = 5) -> dict:
    """Run a search using the configured provider with safe fallback to DDG."""
    query = (query or "").strip()
    if not query:
        return {"error": "empty search query"}
    max_results = min(max(int(max_results or 5), 1), 10)
    provider = _resolve_provider()

    try:
        if provider == "tavily":
            key = settings.tavily_api_key
            if not key:
                raise RuntimeError("tavily provider selected but no API key configured")
            results = _tavily_search(query, max_results, key)
        elif provider == "brave":
            key = settings.brave_api_key
            if not key:
                raise RuntimeError("brave provider selected but no API key configured")
            results = _brave_search(query, max_results, key)
        else:
            results = _ddg_search(query, max_results)
    except Exception as exc:
        # If a paid provider fails, fall back to DDG so the agent doesn't break mid-task
        if provider != "duckduckgo":
            log.warning("%s search failed (%s); falling back to DuckDuckGo", provider, exc)
            try:
                results = _ddg_search(query, max_results)
                provider = "duckduckgo (fallback)"
            except Exception as inner:
                return {"error": f"all search providers failed: {inner}"}
        else:
            return {"error": f"{type(exc).__name__}: {exc}"}

    return {
        "query": query,
        "provider": provider,
        "results": results,
        "count": len(results),
    }
