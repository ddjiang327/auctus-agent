"""Image generation via OpenAI Images API.

Default model: gpt-image-1 (returns b64-encoded PNG, saved to outputs/).
Uses the existing `openai_api_key` from settings; no extra config required.
Falls back with a clear error if no key is set.
"""
from __future__ import annotations

import base64
import logging
import time
from typing import Optional

import httpx

from .config import settings


log = logging.getLogger(__name__)


_VALID_SIZES = {"1024x1024", "1024x1536", "1536x1024", "auto"}
_VALID_QUALITY = {"low", "medium", "high", "auto"}


def _resolve_api_key() -> Optional[str]:
    """Prefer a dedicated image key, fall back to the main OpenAI key."""
    return getattr(settings, "image_api_key", None) or getattr(settings, "openai_api_key", None)


def _safe_filename(prompt: str) -> str:
    """Build a filesystem-safe name from the first words of the prompt."""
    base = (prompt or "image").strip().lower()
    safe = "".join(c if c.isalnum() else "_" for c in base[:50]).strip("_") or "image"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return f"{safe}_{stamp}.png"


def generate_image(prompt: str, size: str = "1024x1024", quality: str = "auto") -> dict:
    """Generate an image from a text prompt and save it to outputs/. Returns the saved path."""
    prompt = (prompt or "").strip()
    if not prompt:
        return {"error": "empty prompt"}
    if size not in _VALID_SIZES:
        size = "1024x1024"
    if quality not in _VALID_QUALITY:
        quality = "auto"

    api_key = _resolve_api_key()
    if not api_key:
        return {
            "error": "未配置 OpenAI API key。请到设置里填入 OPENAI_API_KEY（或单独的 IMAGE_API_KEY）。"
        }

    try:
        response = httpx.post(
            "https://api.openai.com/v1/images/generations",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-image-1",
                "prompt": prompt,
                "size": size,
                "quality": quality,
                "n": 1,
            },
            timeout=120,
        )
    except Exception as exc:
        return {"error": f"image API call failed: {type(exc).__name__}: {exc}"}

    if response.status_code != 200:
        try:
            detail = response.json().get("error", {}).get("message", response.text[:300])
        except Exception:
            detail = response.text[:300]
        return {"error": f"image API returned {response.status_code}: {detail}"}

    try:
        data = response.json()["data"][0]
        b64 = data.get("b64_json")
        if not b64:
            return {"error": "image API response missing b64_json"}
        png_bytes = base64.b64decode(b64)
    except Exception as exc:
        return {"error": f"failed to decode image response: {exc}"}

    out_path = (settings.output_dir / _safe_filename(prompt)).resolve()
    try:
        out_path.write_bytes(png_bytes)
    except Exception as exc:
        return {"error": f"failed to write image: {exc}"}

    return {
        "ok": True,
        "path": str(out_path),
        "size_bytes": len(png_bytes),
        "prompt": prompt,
        "image_size": size,
    }
