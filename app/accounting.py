"""User and usage accounting for commercial-mode foundations.

This module uses sqlite3 directly so it can be called from the LLM layer without
creating import cycles with the SQLAlchemy-backed memory module.
"""
from __future__ import annotations

import contextvars
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any, Iterator, Optional

from .config import settings


LOCAL_USER_ID = "local"
ROUTES = {"local", "byo", "proxy"}
PROVIDERS = {"anthropic", "openai", "deepseek", "dashscope", "auctus_hosted"}

_usage_context: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "usage_context",
    default={
        "user_id": LOCAL_USER_ID,
        "session_id": "",
        "route": "local",
        "tool_name": "",
    },
)


def db_path() -> Path:
    return settings.data_dir / "secretary.db"


def init_db(path: Optional[Path] = None) -> None:
    """Create users/api_keys/usage tables if they do not exist."""
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL DEFAULT 'local',
                display_name TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                route TEXT NOT NULL DEFAULT 'byo',
                encrypted_key TEXT,
                key_hint TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                metadata TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        # Migration: add metadata column if not exists
        try:
            conn.execute("SELECT metadata FROM api_keys LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE api_keys ADD COLUMN metadata TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_accounts (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                label TEXT,
                email_address TEXT NOT NULL,
                username TEXT NOT NULL,
                imap_host TEXT NOT NULL,
                imap_port INTEGER NOT NULL DEFAULT 993,
                imap_ssl INTEGER NOT NULL DEFAULT 1,
                smtp_host TEXT NOT NULL,
                smtp_port INTEGER NOT NULL DEFAULT 465,
                smtp_ssl INTEGER NOT NULL DEFAULT 1,
                encrypted_password TEXT NOT NULL,
                password_hint TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usage (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                session_id TEXT,
                route TEXT NOT NULL DEFAULT 'local',
                model TEXT NOT NULL,
                tool_name TEXT,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                cost REAL NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS setup_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_inbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                direction TEXT NOT NULL,
                from_user TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_user_created ON usage(user_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_session ON usage(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_email_user_active ON email_accounts(user_id, active, updated_at)")
        conn.commit()


def ensure_local_user(user_id: str = LOCAL_USER_ID, path: Optional[Path] = None) -> str:
    init_db(path)
    now = time.time()
    with sqlite3.connect(path or db_path()) as conn:
        conn.execute(
            """
            INSERT INTO users (id, kind, display_name, created_at, updated_at)
            VALUES (?, 'local', 'Local User', ?, ?)
            ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at
            """,
            (user_id, now, now),
        )
        conn.commit()
    return user_id


def set_route(route: str) -> str:
    route = route.strip().lower()
    if route not in ROUTES:
        raise ValueError(f"unsupported route: {route}")
    settings.llm_route = route
    set_setup_state({"llm_route": route})
    return route


def restore_route_from_db() -> None:
    """Called at startup to restore the route saved by set_route()."""
    saved = get_setup_state().get("llm_route", "").strip().lower()
    if saved and saved in ROUTES:
        settings.llm_route = saved


def restore_model_from_db() -> None:
    """Called at startup to restore the model saved by set_model()."""
    saved = get_setup_state().get("model", "").strip()
    if saved:
        settings.model = saved


def current_route() -> str:
    route = (settings.llm_route or "local").strip().lower()
    return route if route in ROUTES else "local"


def get_setup_state(path: Optional[Path] = None) -> dict[str, str]:
    init_db(path)
    with sqlite3.connect(path or db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT key, value FROM setup_state").fetchall()
    return {row["key"]: row["value"] for row in rows}


def set_setup_state(values: dict[str, Any], path: Optional[Path] = None) -> dict[str, str]:
    init_db(path)
    now = time.time()
    with sqlite3.connect(path or db_path()) as conn:
        for key, value in values.items():
            if value is None:
                continue
            conn.execute(
                """
                INSERT INTO setup_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (str(key), str(value), now),
            )
        conn.commit()
    return get_setup_state(path=path)


def complete_setup(path: Optional[Path] = None) -> dict[str, str]:
    return set_setup_state({"onboarding_completed": "1"}, path=path)


def setup_completed(path: Optional[Path] = None) -> bool:
    return get_setup_state(path=path).get("onboarding_completed") == "1"


def save_hosted_account(email: str, region: str = "auto", api_key: Optional[str] = None, base_url: Optional[str] = None, path: Optional[Path] = None) -> dict:
    email = email.strip().lower()
    if "@" not in email or len(email) > 180:
        raise ValueError("invalid login email")
    region = (region or "auto").strip().lower()
    if region not in {"auto", "global", "cn"}:
        region = "auto"
    state = set_setup_state(
        {
            "hosted_email": email,
            "hosted_region": region,
            "hosted_api_key": api_key or "",
            "hosted_base_url": (base_url or "http://localhost:8001").rstrip("/"),
            "hosted_balance_cents": get_setup_state(path=path).get("hosted_balance_cents", "0"),
            "hosted_free_tokens": get_setup_state(path=path).get("hosted_free_tokens", "50000"),
        },
        path=path,
    )
    return hosted_account_summary(state)


def hosted_account_summary(state: Optional[dict[str, str]] = None, path: Optional[Path] = None) -> dict:
    data = state or get_setup_state(path=path)
    email = data.get("hosted_email", "")
    return {
        "logged_in": bool(email),
        "email": email,
        "region": data.get("hosted_region", "auto"),
        "base_url": data.get("hosted_base_url", "http://localhost:8001"),
        "balance_cents": _as_int(data.get("hosted_balance_cents")),
        "free_tokens": _as_int(data.get("hosted_free_tokens")),
        "recharge_url": "/billing",
    }


def provider_for_model(model: str) -> Optional[str]:
    normalized = (model or "").lower()
    if "claude" in normalized or normalized.startswith("anthropic/"):
        return "anthropic"
    if normalized.startswith("deepseek/"):
        return "deepseek"
    if normalized.startswith("openai/") or normalized.startswith("gpt-"):
        return "openai"
    if normalized.startswith("dashscope/") or normalized.startswith("qwen/"):
        return "dashscope"
    return None


def set_api_key(provider: str, api_key: str, user_id: str = LOCAL_USER_ID, base_url: Optional[str] = None, path: Optional[Path] = None) -> dict:
    provider = provider.strip().lower()
    api_key = api_key.strip()
    if provider not in PROVIDERS and provider != "auctus_hosted":
        raise ValueError(f"unsupported provider: {provider}")
    if not api_key:
        raise ValueError("empty api key")
    ensure_local_user(user_id=user_id, path=path)
    now = time.time()
    key_id = str(uuid.uuid4())
    encrypted = _encrypt_secret(api_key)
    hint = _key_hint(api_key)
    # For auctus_hosted, use proxy route and store base_url in metadata
    route = 'proxy' if provider == 'auctus_hosted' else 'byo'
    metadata = json.dumps({"base_url": base_url}) if base_url and provider == 'auctus_hosted' else None
    with sqlite3.connect(path or db_path()) as conn:
        conn.execute(
            "UPDATE api_keys SET active = 0, updated_at = ? WHERE user_id = ? AND provider = ? AND active = 1",
            (now, user_id, provider),
        )
        conn.execute(
            """
            INSERT INTO api_keys (
                id, user_id, provider, route, encrypted_key, key_hint, active, metadata, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (key_id, user_id, provider, route, encrypted, hint, 1, metadata, now, now),
        )
        conn.commit()
    return {"id": key_id, "provider": provider, "key_hint": hint, "active": True}


def list_api_keys(user_id: str = LOCAL_USER_ID, path: Optional[Path] = None) -> list[dict]:
    init_db(path)
    with sqlite3.connect(path or db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, provider, route, key_hint, active, created_at, updated_at
            FROM api_keys
            WHERE user_id = ? AND active = 1
            ORDER BY provider
            """,
            (user_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "provider": row["provider"],
            "route": row["route"],
            "key_hint": row["key_hint"],
            "active": bool(row["active"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def get_api_key(provider: str, user_id: str = LOCAL_USER_ID, path: Optional[Path] = None) -> Optional[str]:
    provider = provider.strip().lower()
    init_db(path)
    with sqlite3.connect(path or db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT encrypted_key
            FROM api_keys
            WHERE user_id = ? AND provider = ? AND active = 1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id, provider),
        ).fetchone()
    if not row or not row["encrypted_key"]:
        return None
    return _decrypt_secret(row["encrypted_key"])


def get_api_key_with_metadata(provider: str, user_id: str = LOCAL_USER_ID, path: Optional[Path] = None) -> Optional[dict]:
    """Get API key and metadata for a provider."""
    provider = provider.strip().lower()
    init_db(path)
    with sqlite3.connect(path or db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT encrypted_key, metadata
            FROM api_keys
            WHERE user_id = ? AND provider = ? AND active = 1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id, provider),
        ).fetchone()
    if not row or not row["encrypted_key"]:
        return None
    result = {"api_key": _decrypt_secret(row["encrypted_key"])}
    if row["metadata"]:
        try:
            result["metadata"] = json.loads(row["metadata"])
        except json.JSONDecodeError:
            result["metadata"] = {}
    return result


def delete_api_key(provider: str, user_id: str = LOCAL_USER_ID, path: Optional[Path] = None) -> dict:
    provider = provider.strip().lower()
    now = time.time()
    with sqlite3.connect(path or db_path()) as conn:
        cur = conn.execute(
            "UPDATE api_keys SET active = 0, updated_at = ? WHERE user_id = ? AND provider = ? AND active = 1",
            (now, user_id, provider),
        )
        conn.commit()
    return {"ok": True, "provider": provider, "removed": cur.rowcount}


@contextmanager
def usage_context(**updates: str) -> Iterator[None]:
    """Temporarily add user/session/tool metadata to model-call usage rows."""
    current = dict(_usage_context.get())
    current.update({k: v for k, v in updates.items() if v is not None})
    token = _usage_context.set(current)
    try:
        yield
    finally:
        _usage_context.reset(token)


def current_usage_context() -> dict[str, str]:
    return dict(_usage_context.get())


def record_model_call(
    *,
    model: str,
    usage: dict[str, Any],
    cost: Optional[float] = None,
    path: Optional[Path] = None,
) -> Optional[str]:
    """Persist one LLM call's token/cost usage. Returns row id or None."""
    if not usage:
        return None
    ctx = current_usage_context()
    user_id = ctx.get("user_id") or LOCAL_USER_ID
    ensure_local_user(user_id=user_id, path=path)

    prompt = _as_int(usage.get("prompt_tokens"))
    completion = _as_int(usage.get("completion_tokens"))
    total = _as_int(usage.get("total_tokens")) or prompt + completion
    row_id = str(uuid.uuid4())
    with sqlite3.connect(path or db_path()) as conn:
        conn.execute(
            """
            INSERT INTO usage (
                id, user_id, session_id, route, model, tool_name,
                prompt_tokens, completion_tokens, total_tokens, cost, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                user_id,
                ctx.get("session_id", ""),
                ctx.get("route", "local") or "local",
                model or settings.model,
                ctx.get("tool_name", ""),
                prompt,
                completion,
                total,
                float(cost or 0),
                time.time(),
            ),
        )
        conn.commit()
    return row_id


def usage_summary(
    path: Optional[Path] = None,
    user_id: Optional[str] = None,
    since: Optional[float] = None,
) -> dict:
    """Aggregate usage rows from SQLite."""
    init_db(path)
    params: list[Any] = []
    clauses: list[str] = []
    if user_id:
        clauses.append("user_id = ?")
        params.append(user_id)
    if since is not None:
        clauses.append("created_at >= ?")
        params.append(since)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    totals = {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "by_model": {},
        "by_tool": {},
        "by_route": {},
    }
    with sqlite3.connect(path or db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT model, route, COALESCE(tool_name, '') AS tool_name,
                   prompt_tokens, completion_tokens, total_tokens, cost
            FROM usage
            {where}
            """,
            params,
        ).fetchall()

    for row in rows:
        prompt = int(row["prompt_tokens"] or 0)
        completion = int(row["completion_tokens"] or 0)
        total = int(row["total_tokens"] or 0)
        cost_value = float(row["cost"] or 0)
        totals["calls"] += 1
        totals["prompt_tokens"] += prompt
        totals["completion_tokens"] += completion
        totals["total_tokens"] += total
        totals["cost"] += cost_value
        _add_usage(totals["by_model"], row["model"] or "unknown", prompt, completion, total, cost_value)
        _add_usage(totals["by_route"], row["route"] or "local", prompt, completion, total, cost_value)
        if row["tool_name"]:
            _add_usage(totals["by_tool"], row["tool_name"], prompt, completion, total, cost_value)
    return totals


def daily_cost_report(
    *,
    day: Optional[date] = None,
    path: Optional[Path] = None,
    user_id: Optional[str] = None,
) -> dict:
    """Aggregate model usage/cost for one local calendar day."""
    target_day = day or date.today()
    start_ts, end_ts = _local_day_bounds(target_day)
    init_db(path)

    params: list[Any] = [start_ts, end_ts]
    clauses = ["created_at >= ?", "created_at < ?"]
    if user_id:
        clauses.append("user_id = ?")
        params.append(user_id)
    where = f"WHERE {' AND '.join(clauses)}"

    totals = {
        "date": target_day.isoformat(),
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "by_model": {},
        "by_tool": {},
        "by_route": {},
    }
    with sqlite3.connect(path or db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT model, route, COALESCE(tool_name, '') AS tool_name,
                   prompt_tokens, completion_tokens, total_tokens, cost
            FROM usage
            {where}
            """,
            params,
        ).fetchall()

    for row in rows:
        prompt = int(row["prompt_tokens"] or 0)
        completion = int(row["completion_tokens"] or 0)
        total = int(row["total_tokens"] or 0)
        cost_value = float(row["cost"] or 0)
        totals["calls"] += 1
        totals["prompt_tokens"] += prompt
        totals["completion_tokens"] += completion
        totals["total_tokens"] += total
        totals["cost"] += cost_value
        _add_usage(totals["by_model"], row["model"] or "unknown", prompt, completion, total, cost_value)
        _add_usage(totals["by_route"], row["route"] or "local", prompt, completion, total, cost_value)
        if row["tool_name"]:
            _add_usage(totals["by_tool"], row["tool_name"], prompt, completion, total, cost_value)
    return totals


def write_daily_cost_report(
    *,
    day: Optional[date] = None,
    output_dir: Optional[Path] = None,
    path: Optional[Path] = None,
    user_id: Optional[str] = None,
) -> dict:
    """Write a Markdown daily cost report and return its path."""
    report = daily_cost_report(day=day, path=path, user_id=user_id)
    target_dir = output_dir or settings.output_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{report['date']}_cost_report.md"
    out_path = target_dir / filename
    out_path.write_text(_format_daily_cost_report(report), encoding="utf-8")
    return {"ok": True, "path": str(out_path), "filename": filename, "report": report}


def _add_usage(bucket: dict, key: str, prompt: int, completion: int, total: int, cost: float) -> None:
    item = bucket.setdefault(
        key,
        {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0.0},
    )
    item["calls"] += 1
    item["prompt_tokens"] += prompt
    item["completion_tokens"] += completion
    item["total_tokens"] += total
    item["cost"] += cost


def _format_daily_cost_report(report: dict) -> str:
    lines = [
        f"# Auctus Agent Cost Report - {report['date']}",
        "",
        "## Summary",
        "",
        f"- Model calls: {report['calls']}",
        f"- Prompt tokens: {report['prompt_tokens']}",
        f"- Completion tokens: {report['completion_tokens']}",
        f"- Total tokens: {report['total_tokens']}",
        f"- Cost: {report['cost']:.6f}",
        "",
    ]
    lines.extend(_format_usage_table("By Model", report.get("by_model") or {}))
    lines.extend(_format_usage_table("By Route", report.get("by_route") or {}))
    lines.extend(_format_usage_table("By Tool", report.get("by_tool") or {}))
    return "\n".join(lines).rstrip() + "\n"


def _format_usage_table(title: str, bucket: dict) -> list[str]:
    lines = [f"## {title}", ""]
    if not bucket:
        return [*lines, "No usage.", ""]
    lines.append("| Name | Calls | Total Tokens | Cost |")
    lines.append("| --- | ---: | ---: | ---: |")
    for name, item in sorted(bucket.items()):
        lines.append(f"| {name} | {item['calls']} | {item['total_tokens']} | {item['cost']:.6f} |")
    lines.append("")
    return lines


def _local_day_bounds(day: date) -> tuple[float, float]:
    start = datetime.combine(day, dt_time.min)
    end = start + timedelta(days=1)
    return start.timestamp(), end.timestamp()


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _secret_key_path() -> Path:
    return settings.data_dir / "local_secret.key"


def _load_master_key() -> bytes:
    path = _secret_key_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secrets.token_bytes(32))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    raw = path.read_bytes()
    if len(raw) < 32:
        raw = hashlib.sha256(raw).digest()
    return raw[:32]


def _encrypt_secret(secret: str) -> str:
    key = _load_master_key()
    nonce = secrets.token_bytes(16)
    plaintext = secret.encode("utf-8")
    stream = _keystream(key, nonce, len(plaintext))
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, stream))
    tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + tag + ciphertext).decode("ascii")


def _decrypt_secret(token: str) -> str:
    key = _load_master_key()
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    if len(raw) < 48:
        raise ValueError("invalid encrypted key")
    nonce, tag, ciphertext = raw[:16], raw[16:48], raw[48:]
    expected = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError("encrypted key integrity check failed")
    stream = _keystream(key, nonce, len(ciphertext))
    plaintext = bytes(a ^ b for a, b in zip(ciphertext, stream))
    return plaintext.decode("utf-8")


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(out[:length])


def _key_hint(api_key: str) -> str:
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}...{api_key[-4:]}"


EMAIL_PROVIDERS: dict[str, dict] = {
    "gmail": {"imap_host": "imap.gmail.com", "imap_port": 993, "imap_ssl": True, "smtp_host": "smtp.gmail.com", "smtp_port": 465, "smtp_ssl": True},
    "outlook": {"imap_host": "outlook.office365.com", "imap_port": 993, "imap_ssl": True, "smtp_host": "smtp.office365.com", "smtp_port": 587, "smtp_ssl": False},
    "icloud": {"imap_host": "imap.mail.me.com", "imap_port": 993, "imap_ssl": True, "smtp_host": "smtp.mail.me.com", "smtp_port": 587, "smtp_ssl": False},
    "yahoo": {"imap_host": "imap.mail.yahoo.com", "imap_port": 993, "imap_ssl": True, "smtp_host": "smtp.mail.yahoo.com", "smtp_port": 465, "smtp_ssl": True},
    "qq": {"imap_host": "imap.qq.com", "imap_port": 993, "imap_ssl": True, "smtp_host": "smtp.qq.com", "smtp_port": 465, "smtp_ssl": True},
    "163": {"imap_host": "imap.163.com", "imap_port": 993, "imap_ssl": True, "smtp_host": "smtp.163.com", "smtp_port": 465, "smtp_ssl": True},
}


def save_email_account(
    email_address: str,
    username: str,
    password: str,
    imap_host: str,
    imap_port: int = 993,
    imap_ssl: bool = True,
    smtp_host: str = "",
    smtp_port: int = 465,
    smtp_ssl: bool = True,
    label: str = "",
    user_id: str = LOCAL_USER_ID,
    path: Optional[Path] = None,
) -> dict:
    init_db(path)
    ensure_local_user(user_id, path)
    account_id = str(uuid.uuid4())
    now = time.time()
    encrypted = _encrypt_secret(password)
    hint = f"...{password[-4:]}" if len(password) >= 4 else "****"
    effective_smtp = smtp_host.strip() or imap_host
    with sqlite3.connect(path or db_path()) as conn:
        conn.execute(
            """
            INSERT INTO email_accounts
              (id, user_id, label, email_address, username,
               imap_host, imap_port, imap_ssl,
               smtp_host, smtp_port, smtp_ssl,
               encrypted_password, password_hint, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                account_id, user_id, label or email_address, email_address,
                username or email_address,
                imap_host, imap_port, 1 if imap_ssl else 0,
                effective_smtp, smtp_port, 1 if smtp_ssl else 0,
                encrypted, hint, now, now,
            ),
        )
        conn.commit()
    return {
        "id": account_id,
        "email_address": email_address,
        "label": label or email_address,
        "imap_host": imap_host,
        "imap_port": imap_port,
        "imap_ssl": bool(imap_ssl),
        "smtp_host": effective_smtp,
        "smtp_port": smtp_port,
        "smtp_ssl": bool(smtp_ssl),
        "password_hint": hint,
        "active": True,
    }


def list_email_accounts(user_id: str = LOCAL_USER_ID, path: Optional[Path] = None) -> list[dict]:
    init_db(path)
    with sqlite3.connect(path or db_path()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT id, label, email_address, username, imap_host, imap_port, imap_ssl,
               smtp_host, smtp_port, smtp_ssl, password_hint
               FROM email_accounts WHERE user_id=? AND active=1 ORDER BY updated_at DESC""",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_email_account(account_id: str, path: Optional[Path] = None) -> Optional[dict]:
    init_db(path)
    with sqlite3.connect(path or db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM email_accounts WHERE id=? AND active=1",
            (account_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["password"] = _decrypt_secret(d.pop("encrypted_password"))
    except Exception:
        d["password"] = ""
        d.pop("encrypted_password", None)
    return d


def delete_email_account(account_id: str, path: Optional[Path] = None) -> dict:
    init_db(path)
    now = time.time()
    with sqlite3.connect(path or db_path()) as conn:
        conn.execute(
            "UPDATE email_accounts SET active=0, updated_at=? WHERE id=?",
            (now, account_id),
        )
        conn.commit()
    return {"ok": True, "id": account_id}


def add_telegram_inbox_message(direction: str, text: str, from_user: str = "", path: Optional[Path] = None) -> int:
    """Store an incoming or outgoing Telegram message. Returns the inserted row id."""
    init_db(path)
    now = time.time()
    with sqlite3.connect(path or db_path()) as conn:
        cur = conn.execute(
            "INSERT INTO telegram_inbox (direction, from_user, text, created_at) VALUES (?, ?, ?, ?)",
            (direction, from_user or "", text, now),
        )
        conn.commit()
        return cur.lastrowid or 0


def get_telegram_inbox_messages(since_id: int = 0, limit: int = 50, path: Optional[Path] = None) -> list[dict]:
    """Return Telegram inbox messages with id > since_id, oldest first."""
    init_db(path)
    with sqlite3.connect(path or db_path()) as conn:
        rows = conn.execute(
            "SELECT id, direction, from_user, text, created_at FROM telegram_inbox WHERE id > ? ORDER BY id ASC LIMIT ?",
            (since_id, limit),
        ).fetchall()
    return [
        {"id": r[0], "direction": r[1], "from_user": r[2], "text": r[3], "created_at": r[4]}
        for r in rows
    ]


init_db()
ensure_local_user()
