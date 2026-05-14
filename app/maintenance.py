"""本地维护工具：配置检查、用量统计、日志清理。"""
from __future__ import annotations

import json
import os
import platform
import sqlite3
import time
from pathlib import Path
from typing import Optional

from . import evolution
from .config import settings


def doctor() -> dict:
    checks = [
        _check_directory("inputs", settings.workspace_dir),
        _check_directory("outputs", settings.output_dir),
        _check_directory("logs", settings.logs_dir),
        _check_directory("data", settings.data_dir),
        _check_model_config(),
        _check_windows_path_compatibility(),
    ]
    status = "ok" if all(c["status"] == "ok" for c in checks) else "warning"
    return {"status": status, "checks": checks}


def usage_summary(log_path: Optional[Path] = None) -> dict:
    log_path = log_path or (settings.logs_dir / "tool_calls.jsonl")
    totals = {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "by_model": {},
        "by_tool": {},
    }
    for entry in _read_log_entries(log_path):
        totals["calls"] += 1
        usage = entry.get("token_usage") or {}
        prompt = int(usage.get("prompt_tokens") or 0)
        completion = int(usage.get("completion_tokens") or 0)
        total = int(usage.get("total_tokens") or 0)
        totals["prompt_tokens"] += prompt
        totals["completion_tokens"] += completion
        totals["total_tokens"] += total

        model = entry.get("model") or "unknown"
        tool = entry.get("tool_name") or "unknown"
        _add_usage(totals["by_model"], model, prompt, completion, total)
        _add_usage(totals["by_tool"], tool, prompt, completion, total)
    return totals


def clean_logs(keep: int = 1000, log_path: Optional[Path] = None) -> dict:
    if keep < 0:
        raise ValueError("keep must be >= 0")
    log_path = log_path or (settings.logs_dir / "tool_calls.jsonl")
    if not log_path.exists():
        return {"ok": True, "before": 0, "after": 0, "path": str(log_path)}
    lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    kept = lines[-keep:] if keep else []
    backup_path = log_path.with_suffix(log_path.suffix + ".bak")
    backup_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    log_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return {
        "ok": True,
        "before": len(lines),
        "after": len(kept),
        "path": str(log_path),
        "backup": str(backup_path),
    }


def sleep_evolve(
    *,
    force: bool = False,
    min_interval_hours: float = 12,
    limit_messages: int = 120,
    limit_logs: int = 80,
    state_path: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> dict:
    """Run self-evolution as a sleep-time maintenance task with simple throttling."""
    state_path = state_path or (settings.data_dir / "maintenance_state.json")
    state = _read_state(state_path)
    now = time.time()
    last_run = float(state.get("last_evolve_scan_at") or 0)
    min_interval_seconds = max(0.0, min_interval_hours) * 3600
    if not force and last_run and now - last_run < min_interval_seconds:
        return {
            "ok": True,
            "skipped": True,
            "reason": "recently scanned",
            "last_run_at": last_run,
            "next_run_at": last_run + min_interval_seconds,
        }

    result = evolution.scan(limit_messages=limit_messages, limit_logs=limit_logs, path=db_path)
    state["last_evolve_scan_at"] = now
    state["last_evolve_result"] = result
    _write_state(state_path, state)
    return {"ok": True, "skipped": False, "evolution": result}


def stability_check(snapshot: bool = True, snapshot_dir: Optional[Path] = None) -> dict:
    """Check local data durability signals and optionally write a snapshot manifest."""
    checks = [
        _check_directory("inputs", settings.workspace_dir),
        _check_directory("outputs", settings.output_dir),
        _check_directory("logs", settings.logs_dir),
        _check_directory("data", settings.data_dir),
        _check_sqlite("secretary_db", settings.data_dir / "secretary.db"),
        _check_sqlite("chroma_db", settings.data_dir / "chroma" / "chroma.sqlite3"),
        _check_jsonl("tool_logs", settings.logs_dir / "tool_calls.jsonl"),
    ]
    manifest = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "ok" if all(item["status"] == "ok" for item in checks) else "warning",
        "checks": checks,
        "files": {
            "inputs": _directory_summary(settings.workspace_dir),
            "outputs": _directory_summary(settings.output_dir),
            "data": _directory_summary(settings.data_dir),
            "logs": _directory_summary(settings.logs_dir),
        },
    }
    if snapshot:
        target_dir = snapshot_dir or (settings.data_dir / "stability_snapshots")
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{time.strftime('%Y%m%d_%H%M%S')}_stability.json"
        path = target_dir / filename
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest["snapshot_path"] = str(path)
    return manifest


def _check_directory(name: str, path: Path) -> dict:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".doctor_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return {"name": name, "status": "ok", "path": str(path)}
    except Exception as e:
        return {"name": name, "status": "warning", "path": str(path), "message": f"{type(e).__name__}: {e}"}


def _check_model_config() -> dict:
    model = settings.model or ""
    key_name = _required_key_for_model(model)
    if key_name is None:
        return {"name": "model", "status": "ok", "model": model, "message": "local or unknown provider; no API key check"}
    if os.getenv(key_name) or getattr(settings, key_name.lower(), None):
        return {"name": "model", "status": "ok", "model": model, "required_env": key_name}
    return {
        "name": "model",
        "status": "warning",
        "model": model,
        "required_env": key_name,
        "message": f"missing {key_name}",
    }


def _check_windows_path_compatibility() -> dict:
    paths = [settings.workspace_dir, settings.output_dir, settings.logs_dir, settings.data_dir]
    risky = [str(p) for p in paths if any(ch in str(p) for ch in '<>:"|?*')]
    if platform.system() == "Windows" and risky:
        return {"name": "windows_paths", "status": "warning", "message": "path contains Windows-invalid characters", "paths": risky}
    return {"name": "windows_paths", "status": "ok", "paths": [str(p) for p in paths]}


def _check_sqlite(name: str, path: Path) -> dict:
    if not path.exists():
        return {"name": name, "status": "ok", "path": str(path), "message": "not created yet"}
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
        result = row[0] if row else "unknown"
        status = "ok" if result == "ok" else "warning"
        return {"name": name, "status": status, "path": str(path), "quick_check": result}
    except Exception as e:
        return {"name": name, "status": "warning", "path": str(path), "message": f"{type(e).__name__}: {e}"}


def _check_jsonl(name: str, path: Path) -> dict:
    if not path.exists():
        return {"name": name, "status": "ok", "path": str(path), "lines": 0, "bad_lines": 0}
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    bad = 0
    for raw in lines:
        try:
            json.loads(raw)
        except json.JSONDecodeError:
            bad += 1
    return {
        "name": name,
        "status": "ok" if bad == 0 else "warning",
        "path": str(path),
        "lines": len(lines),
        "bad_lines": bad,
    }


def _directory_summary(path: Path) -> dict:
    if not path.exists():
        return {"path": str(path), "exists": False, "files": 0, "bytes": 0}
    files = [item for item in path.rglob("*") if item.is_file()]
    return {
        "path": str(path),
        "exists": True,
        "files": len(files),
        "bytes": sum(item.stat().st_size for item in files),
    }


def _required_key_for_model(model: str) -> Optional[str]:
    normalized = model.lower()
    if normalized.startswith("ollama/") or normalized.startswith("local/"):
        return None
    if "claude" in normalized or normalized.startswith("anthropic/"):
        return "ANTHROPIC_API_KEY"
    if normalized.startswith("deepseek/"):
        return "DEEPSEEK_API_KEY"
    if normalized.startswith("openai/") or normalized.startswith("gpt-"):
        return "OPENAI_API_KEY"
    return None


def _read_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_log_entries(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    entries = []
    for raw in log_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            entries.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return entries


def _add_usage(bucket: dict, key: str, prompt: int, completion: int, total: int) -> None:
    item = bucket.setdefault(key, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    item["calls"] += 1
    item["prompt_tokens"] += prompt
    item["completion_tokens"] += completion
    item["total_tokens"] += total
