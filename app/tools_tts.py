"""Text-to-speech via OpenAI TTS API.

Default model: tts-1 (fast, cheap).
Reuses `openai_api_key` unless a dedicated `tts_api_key` is configured.
Output: MP3 file saved to outputs/.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from .config import settings


log = logging.getLogger(__name__)


_VALID_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}
_VALID_FORMATS = {"mp3", "opus", "aac", "flac", "wav"}
_MAX_CHARS = 4000  # OpenAI hard limit is 4096; leave a small buffer


def _resolve_api_key() -> Optional[str]:
    return getattr(settings, "tts_api_key", None) or getattr(settings, "openai_api_key", None)


def _safe_filename(text: str, fmt: str) -> str:
    base = (text or "speech").strip().lower()
    safe = "".join(c if c.isalnum() else "_" for c in base[:40]).strip("_") or "speech"
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return f"{safe}_{stamp}.{fmt}"


def text_to_speech(text: str, voice: str = "", fmt: str = "mp3", speed: float = 1.0) -> dict:
    """Convert text to speech audio and save it to outputs/. Returns the saved path + duration estimate."""
    text = (text or "").strip()
    if not text:
        return {"error": "empty text"}
    if len(text) > _MAX_CHARS:
        return {
            "error": f"text too long ({len(text)} > {_MAX_CHARS} chars). Split into chunks and call this tool multiple times.",
        }

    chosen_voice = (voice or settings.tts_voice or "alloy").lower().strip()
    if chosen_voice not in _VALID_VOICES:
        chosen_voice = "alloy"
    fmt = (fmt or "mp3").lower().strip()
    if fmt not in _VALID_FORMATS:
        fmt = "mp3"
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        speed = 1.0
    speed = max(0.25, min(speed, 4.0))

    api_key = _resolve_api_key()
    if not api_key:
        return {
            "error": "未配置 OpenAI API key。请到设置里填入 OPENAI_API_KEY（或单独的 TTS_API_KEY）。"
        }

    try:
        response = httpx.post(
            "https://api.openai.com/v1/audio/speech",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "tts-1",
                "input": text,
                "voice": chosen_voice,
                "response_format": fmt,
                "speed": speed,
            },
            timeout=60,
        )
    except Exception as exc:
        return {"error": f"TTS API call failed: {type(exc).__name__}: {exc}"}

    if response.status_code != 200:
        try:
            detail = response.json().get("error", {}).get("message", response.text[:300])
        except Exception:
            detail = response.text[:300]
        return {"error": f"TTS API returned {response.status_code}: {detail}"}

    audio_bytes = response.content
    out_path = (settings.output_dir / _safe_filename(text, fmt)).resolve()
    try:
        out_path.write_bytes(audio_bytes)
    except Exception as exc:
        return {"error": f"failed to write audio file: {exc}"}

    approx_seconds = round(len(text.split()) / (150 * speed) * 60, 1)

    return {
        "ok": True,
        "path": str(out_path),
        "size_bytes": len(audio_bytes),
        "voice": chosen_voice,
        "format": fmt,
        "approx_duration_seconds": approx_seconds,
    }
