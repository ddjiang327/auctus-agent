"""Agent 可调用的工具。

设计要点：
- 每个工具是一个普通 Python 函数。
- 用 TOOL_SCHEMAS 暴露 OpenAI function-calling 格式给 LLM。
- run_tool() 根据 name 分发，并自动写 logs/tool_calls.jsonl。
"""
from __future__ import annotations

import json
import base64
import mimetypes
import re
import subprocess
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from html import unescape
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen
from pathlib import Path
from typing import Any, Optional

from openpyxl import Workbook
from jinja2 import Template

from .config import settings
from . import accounting
from . import cronjobs
from . import evolution
from . import memory
from . import llm
from . import terminal_sessions


# ---------- 工具实现 ----------

# ---- 文件读取 ----

_READABLE_SUFFIXES = {
    ".txt",
    ".md",
    ".json",
    ".csv",
    ".html",
    ".htm",
    ".css",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".py",
    ".pdf",
    ".xlsx",
    ".xls",
    ".docx",
}
_READABLE_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_WRITABLE_TEXT_SUFFIXES = {".txt", ".md", ".json", ".csv", ".html", ".css", ".js", ".jsx", ".ts", ".tsx", ".py", ".ics"}
_MAX_READ_CHARS = 50_000
_CHAT_AUTHORIZED_PATHS: ContextVar[tuple[Path, ...]] = ContextVar("chat_authorized_paths", default=())
_TERMINAL_ACCESS_OVERRIDE: ContextVar[Optional[str]] = ContextVar("terminal_access_override", default=None)
_PERMISSION_SCOPE_OVERRIDE: ContextVar[Optional[str]] = ContextVar("permission_scope_override", default=None)
_MEMORY_TYPES = {"preference", "project", "rule", "temporary"}
DEFAULT_PERMISSION_SCOPE = "full_computer"
DEFAULT_TERMINAL_ACCESS = "enabled"
_REMEMBER_KEYWORDS = ("记住", "保存", "保存到记忆", "加入记忆", "remember", "save this", "save this memory")
_FORGET_KEYWORDS = ("删除记忆", "删掉记忆", "忘记", "forget", "delete memory", "remove memory")
_TERMINAL_KEYWORDS = (
    "终端",
    "命令",
    "shell",
    "terminal",
    "command",
    "run",
    "执行",
    "运行",
    "打开",
    "启动",
    # 文件级操作也算"明确授权"（用于 rm/mv 等）
    "删除",
    "删掉",
    "移到废纸篓",
    "废纸篓",
    "回收站",
    "trash",
    "确认执行",
    "可以运行",
    "允许运行",
    "rm",
    "mv",
)
_AFFIRMATIVE_CONFIRMATIONS = ("是", "是的", "确认", "确认执行", "可以", "好", "好的", "yes", "y", "ok")
_BLOCKED_TERMINAL_PATTERNS = (
    r"\brm\s+-rf\s+/",
    r"\bsudo\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bmkfs\b",
    r"\bdd\s+",
    r"\bdiskutil\s+erase",
    r":\(\)\s*\{",
)


def read_file(path: str) -> dict:
    """读取已授权路径内的文件，支持常见文档和网页/代码文本文件。"""
    target = _authorized_path(path)
    if target is None:
        return {"error": "access denied: path is outside authorized workspace or chat-authorized paths"}
    if not target.exists():
        return {"error": f"file not found: {path}"}
    if target.is_dir():
        return _list_directory(target)

    suffix = target.suffix.lower()
    if suffix in _READABLE_IMAGE_SUFFIXES:
        return {
            "filename": target.name,
            "size": target.stat().st_size,
            "type": "image",
            "path": str(target),
            "note": "image file is authorized and readable, but this text tool cannot inspect pixels. Use a vision-capable model or OCR tool to analyze image content.",
        }
    if suffix not in _READABLE_SUFFIXES:
        return {"error": f"unsupported file type: {suffix}"}

    try:
        content = _extract_text(target, suffix)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    if len(content) > _MAX_READ_CHARS:
        content = content[:_MAX_READ_CHARS] + f"\n\n[内容已截断，原始长度约 {len(content)} 字符]"

    return {"filename": target.name, "size": target.stat().st_size, "content": content}


def write_file(path: str, content: str, overwrite: bool = True) -> dict:
    """Write a text file inside the authorized workspace or chat-authorized directory/file."""
    target = _authorized_path(path)
    workspace = settings.workspace_dir.resolve()
    if target is None:
        return {"error": "access denied: path is outside authorized workspace or chat-authorized paths"}
    if target.exists() and not overwrite:
        return {"error": f"file already exists: {path}"}
    suffix = target.suffix.lower()
    if suffix not in _WRITABLE_TEXT_SUFFIXES:
        return {"error": f"unsupported writable file type: {suffix}"}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {
        "path": str(target),
        "filename": target.name,
        "workspace": str(workspace),
        "size": target.stat().st_size,
    }


def run_terminal_command(command: str, working_directory: Optional[str] = None, timeout_seconds: int = 30) -> dict:
    """Run a terminal command after explicit terminal access is enabled."""
    if _terminal_access() != "enabled":
        return {"error": "terminal access is disabled. Enable it in Settings first."}
    command = (command or "").strip()
    if not command:
        return {"error": "empty command"}
    blocked = _blocked_terminal_reason(command)
    if blocked:
        return {"error": blocked}
    cwd = _terminal_cwd(working_directory)
    if cwd is None:
        return {"error": "working directory is outside allowed scope or does not exist"}
    timeout_seconds = min(max(int(timeout_seconds or 30), 1), 120)
    completed = subprocess.run(
        ["/bin/zsh", "-lc", command],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )
    stdout = completed.stdout[-12000:]
    stderr = completed.stderr[-12000:]
    return {
        "command": command,
        "working_directory": str(cwd),
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": len(completed.stdout) > len(stdout) or len(completed.stderr) > len(stderr),
    }


def analyze_image(path: str, question: str = "") -> dict:
    """Analyze an authorized image file with the configured vision model."""
    target = _authorized_path(path)
    if target is None:
        return {"error": "access denied: path is outside authorized workspace or chat-authorized paths"}
    if not target.exists() or not target.is_file():
        return {"error": f"image not found: {path}"}
    suffix = target.suffix.lower()
    if suffix not in _READABLE_IMAGE_SUFFIXES:
        return {"error": f"unsupported image type: {suffix}"}
    size = target.stat().st_size
    if size > 10 * 1024 * 1024:
        return {"error": "image is too large for inline analysis; keep it under 10 MB"}
    mime = mimetypes.guess_type(target.name)[0] or "image/png"
    data = base64.b64encode(target.read_bytes()).decode("ascii")
    prompt = (question or "").strip() or (
        "Analyze this image. Extract visible text, describe the important visual content, "
        "and mention anything that looks like an error, chart, table, UI state, or action item."
    )
    try:
        content = llm.vision_completion(
            image_data_url=f"data:{mime};base64,{data}",
            question=prompt,
        )
    except Exception as exc:
        return {
            "error": (
                f"{type(exc).__name__}: {exc}. "
                "Image analysis requires a vision-capable model and provider route."
            )
        }
    return {
        "filename": target.name,
        "path": str(target),
        "size": size,
        "content": content,
    }


def terminal_session_start(
    command: str,
    working_directory: Optional[str] = None,
    label: str = "",
) -> dict:
    """Start a managed long-running terminal session."""
    if _terminal_access() != "enabled":
        return {"error": "terminal access is disabled. Enable it in Settings first."}
    command = (command or "").strip()
    if not command:
        return {"error": "empty command"}
    blocked = _blocked_terminal_reason(command)
    if blocked:
        return {"error": blocked}
    cwd = _terminal_cwd(working_directory)
    if cwd is None:
        return {"error": "working directory is outside allowed scope or does not exist"}
    return terminal_sessions.start(command=command, working_directory=cwd, label=label)


def terminal_session_list() -> dict:
    """List managed terminal sessions."""
    return terminal_sessions.list_sessions()


def terminal_session_tail(session_id: str, lines: int = 80) -> dict:
    """Return recent output from a managed terminal session."""
    return terminal_sessions.tail(session_id=session_id, lines=lines)


def terminal_session_send(session_id: str, text: str, append_newline: bool = True) -> dict:
    """Send input to a managed terminal session."""
    if _terminal_access() != "enabled":
        return {"error": "terminal access is disabled. Enable it in Settings first."}
    return terminal_sessions.send(session_id=session_id, text=text, append_newline=append_newline)


def terminal_session_stop(session_id: str, force: bool = False) -> dict:
    """Stop a managed terminal session."""
    if _terminal_access() != "enabled":
        return {"error": "terminal access is disabled. Enable it in Settings first."}
    return terminal_sessions.stop(session_id=session_id, force=force)


@contextmanager
def chat_authorized_paths(paths: list[Path]):
    resolved = tuple(p.expanduser().resolve() for p in paths if p.exists())
    token = _CHAT_AUTHORIZED_PATHS.set(resolved)
    try:
        yield
    finally:
        _CHAT_AUTHORIZED_PATHS.reset(token)


@contextmanager
def terminal_access_override(access: Optional[str]):
    token = _TERMINAL_ACCESS_OVERRIDE.set(access)
    try:
        yield
    finally:
        _TERMINAL_ACCESS_OVERRIDE.reset(token)


@contextmanager
def permission_scope_override(scope: Optional[str]):
    token = _PERMISSION_SCOPE_OVERRIDE.set(scope)
    try:
        yield
    finally:
        _PERMISSION_SCOPE_OVERRIDE.reset(token)


def _authorized_path(path: str) -> Optional[Path]:
    target = (Path(path) if Path(path).is_absolute() else (settings.workspace_dir / path)).resolve()
    if _permission_scope() == "full_computer":
        return target
    workspace = settings.workspace_dir.resolve()
    try:
        target.relative_to(workspace)
        return target
    except ValueError:
        pass
    for allowed in _CHAT_AUTHORIZED_PATHS.get():
        if allowed.is_dir():
            try:
                target.relative_to(allowed)
                return target
            except ValueError:
                continue
        if target == allowed:
            return target
    return None


def _permission_scope() -> str:
    override = _PERMISSION_SCOPE_OVERRIDE.get()
    if override:
        return "full_computer" if override == "full_computer" else "workspace"
    try:
        scope = accounting.get_setup_state().get("permission_scope", DEFAULT_PERMISSION_SCOPE)
    except Exception:
        scope = DEFAULT_PERMISSION_SCOPE
    return "full_computer" if scope == "full_computer" else "workspace"


def _terminal_access() -> str:
    override = _TERMINAL_ACCESS_OVERRIDE.get()
    if override:
        return "enabled" if override == "enabled" else "disabled"
    try:
        access = accounting.get_setup_state().get("terminal_access", DEFAULT_TERMINAL_ACCESS)
    except Exception:
        access = DEFAULT_TERMINAL_ACCESS
    return "enabled" if access == "enabled" else "disabled"


def _terminal_cwd(working_directory: Optional[str]) -> Optional[Path]:
    raw = (working_directory or "").strip()
    target = Path(raw).expanduser().resolve() if raw else settings.workspace_dir.resolve()
    if not target.exists() or not target.is_dir():
        return None
    if _permission_scope() == "full_computer":
        return target
    workspace = settings.workspace_dir.resolve()
    try:
        target.relative_to(workspace)
        return target
    except ValueError:
        return None


def _blocked_terminal_reason(command: str) -> Optional[str]:
    lowered = command.lower()
    for pattern in _BLOCKED_TERMINAL_PATTERNS:
        if re.search(pattern, lowered):
            return f"blocked potentially destructive terminal command: {pattern}"
    return None


def _list_directory(target: Path) -> dict:
    items = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:200]:
        try:
            stat = child.stat()
            items.append({
                "name": child.name,
                "path": str(child),
                "type": "directory" if child.is_dir() else "file",
                "size": stat.st_size,
            })
        except OSError:
            continue
    return {
        "filename": target.name,
        "path": str(target),
        "type": "directory",
        "items": items,
        "truncated": len(items) >= 200,
    }
    return target


def _extract_text(target: Path, suffix: str) -> str:
    if suffix in (".txt", ".md", ".json", ".html", ".htm", ".css", ".js", ".jsx", ".ts", ".tsx", ".py"):
        return target.read_text(encoding="utf-8", errors="replace")

    if suffix == ".pdf":
        import fitz  # pymupdf
        doc = fitz.open(str(target))
        return "\n".join(page.get_text() for page in doc)

    if suffix == ".csv":
        import pandas as pd
        df = pd.read_csv(target)
        return df.to_markdown(index=False)

    if suffix in (".xlsx", ".xls"):
        import pandas as pd
        df = pd.read_excel(target)
        return df.to_markdown(index=False)

    if suffix == ".docx":
        from docx import Document
        doc = Document(str(target))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    return ""


# ---- 报告生成 ----

def make_markdown_report(title: str, sections: list[dict]) -> dict:
    """生成 Markdown 报告文件。

    sections: [{"heading": str, "content": str}]
    """
    lines = [f"# {title}\n"]
    for s in sections:
        lines.append(f"## {s['heading']}\n")
        lines.append(s.get("content", "") + "\n")
    content = "\n".join(lines)
    fname = f"{int(time.time())}_{_safe(title)}.md"
    out_path = settings.output_dir / fname
    out_path.write_text(content, encoding="utf-8")
    return {"path": str(out_path), "filename": fname}


def make_spreadsheet(title: str, sheets: list[dict]) -> dict:
    """生成 xlsx 文件并返回路径。

    sheets: [{ "name": "...", "headers": [...], "rows": [[...], ...] }]
    """
    wb = Workbook()
    wb.remove(wb.active)
    for sh in sheets:
        ws = wb.create_sheet(sh.get("name", "Sheet1")[:31])
        headers = sh.get("headers", [])
        if headers:
            ws.append(headers)
        for row in sh.get("rows", []):
            ws.append(row)
    fname = f"{int(time.time())}_{_safe(title)}.xlsx"
    out_path = settings.output_dir / fname
    wb.save(out_path)
    return {"path": str(out_path), "filename": fname}


WEBPAGE_TEMPLATE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>{{ title }}</title>
<style>
body{font-family:-apple-system,system-ui,sans-serif;max-width:780px;margin:40px auto;padding:0 20px;color:#222;line-height:1.7}
h1{border-bottom:2px solid #333;padding-bottom:8px}
h2{margin-top:32px;color:#0a66c2}
section{margin-bottom:24px}
code,pre{background:#f5f5f5;padding:2px 6px;border-radius:4px}
table{border-collapse:collapse;width:100%}
td,th{border:1px solid #ddd;padding:8px;text-align:left}
</style></head><body>
<h1>{{ title }}</h1>
{% for s in sections %}
<section><h2>{{ s.heading }}</h2>{{ s.html | safe }}</section>
{% endfor %}
<footer><small>由 Auctus Agent 生成 · {{ now }}</small></footer>
</body></html>
"""


def make_webpage(title: str, sections: list[dict]) -> dict:
    """生成一个简洁的 HTML 报告页。

    sections: [{ "heading": "...", "html": "..." }]
    """
    tpl = Template(WEBPAGE_TEMPLATE)
    html = tpl.render(title=title, sections=sections, now=time.strftime("%Y-%m-%d %H:%M"))
    fname = f"{int(time.time())}_{_safe(title)}.html"
    out_path = settings.output_dir / fname
    out_path.write_text(html, encoding="utf-8")
    return {"path": str(out_path), "filename": fname}


REACT_PROTOTYPE_TEMPLATE = """<!doctype html>
<html lang="zh"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }}</title>
<script src="https://cdn.tailwindcss.com"></script>
<script crossorigin src="https://unpkg.com/react@18/umd/react.development.js"></script>
<script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
<script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
</head>
<body class="bg-gray-50 min-h-screen">
<div id="root"></div>
<script type="text/babel">
{{ jsx_code }}
const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);
</script>
<footer class="text-center text-xs text-gray-400 py-4 mt-8">
  由 Auctus Agent 生成 · {{ now }}
</footer>
</body></html>
"""


def make_react_prototype(title: str, jsx_code: str) -> dict:
    """生成 React + Tailwind 单页原型（CDN 版，可直接浏览器打开）。

    jsx_code: 完整 JSX 代码，必须包含 function App() {...} 组件。
    """
    tpl = Template(REACT_PROTOTYPE_TEMPLATE)
    html = tpl.render(title=title, jsx_code=jsx_code, now=time.strftime("%Y-%m-%d %H:%M"))
    fname = f"{int(time.time())}_{_safe(title)}_prototype.html"
    out_path = settings.output_dir / fname
    out_path.write_text(html, encoding="utf-8")
    return {"path": str(out_path), "filename": fname}


# ---- 总结 ----

def summarize_text(text: str, style: str = "bullet") -> dict:
    """让模型对一段长文做总结。"""
    style_hint = {
        "bullet": "用 markdown 项目符号列要点",
        "executive": "写一段 150 字以内的高管摘要",
        "qa": "提炼成 Q&A 形式",
    }.get(style, "用 markdown 项目符号列要点")

    resp = llm.chat_completion(
        messages=[{
            "role": "user",
            "content": f"请总结以下内容，{style_hint}：\n\n{text}",
        }],
        temperature=0.2,
    )
    return {"summary": resp["choices"][0]["message"]["content"]}


# ---- 邮件处理 ----

def _current_date_context() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


_EMAIL_DATE_RULES = """时间上下文：
- 当前本地时间：{now}
- 如果邮件里出现"今天/明天/下周/两天后"等相对日期，必须基于当前本地时间推算。
- 如果邮件写了星期几和具体月日，但两者按当前年份不一致，不要擅自改年份；保留原文，并标注"日期需确认"。
- 不确定的日期不要编造年份。"""

_EMAIL_SUMMARY_PROMPT = """请对以下邮件内容进行结构化摘要，输出格式：

{date_rules}

- **发件人/来源**：
- **主题**：
- **核心目的**：一句话概括这封邮件想达成什么
- **关键信息**：3-5 条要点
- **需要行动吗**：是/否，如需行动列出具体事项
- **紧急程度**：高/中/低，简述理由

邮件内容：
{email_text}
"""


def summarize_email_text(email_text: str) -> dict:
    """总结粘贴的邮件内容，提取关键信息和行动项。"""
    resp = llm.chat_completion(
        messages=[{
            "role": "user",
            "content": _EMAIL_SUMMARY_PROMPT.format(
                date_rules=_EMAIL_DATE_RULES.format(now=_current_date_context()),
                email_text=email_text[:12000],
            ),
        }],
        temperature=0.2,
    )
    return {"summary": resp["choices"][0]["message"]["content"]}


_EMAIL_TASKS_PROMPT = """请从以下邮件中提取所有需要执行的任务/行动项。

{date_rules}

对每条任务，输出：
- 任务描述
- 截止日期（如有）
- 负责人（如有）
- 优先级判断（高/中/低）

如果没有明确任务，请明确说明"无待办事项"。

邮件内容：
{email_text}
"""


def extract_email_tasks(email_text: str) -> dict:
    """提取邮件中的待办事项。"""
    resp = llm.chat_completion(
        messages=[{
            "role": "user",
            "content": _EMAIL_TASKS_PROMPT.format(
                date_rules=_EMAIL_DATE_RULES.format(now=_current_date_context()),
                email_text=email_text[:12000],
            ),
        }],
        temperature=0.2,
    )
    return {"tasks": resp["choices"][0]["message"]["content"]}


_EMAIL_REPLY_PROMPT = """请根据以下邮件内容，起草一封回复邮件。

{date_rules}

要求：
- 语气：{tone}
- 必须回复的要点：{points_or_auto}
- 不添加编造信息，不确定的内容用括号标注待确认
- 输出格式：先给「主题行」，然后空一行，再给「正文」

原始邮件：
{email_text}
"""


def draft_email_reply(
    email_text: str,
    tone: str = "professional",
    points: Optional[list[str]] = None,
) -> dict:
    """生成邮件回复草稿。只生成文本，不执行发送。"""
    tone_hint = {
        "professional": "正式、商务",
        "friendly": "友好、亲切",
        "concise": "简短、直接",
        "apologetic": "委婉、致歉",
    }.get(tone, "正式、商务")

    if points:
        points_text = "\n".join(f"- {p}" for p in points)
    else:
        points_text = "（请自动判断需要回复的要点）"

    resp = llm.chat_completion(
        messages=[{
            "role": "user",
            "content": _EMAIL_REPLY_PROMPT.format(
                date_rules=_EMAIL_DATE_RULES.format(now=_current_date_context()),
                email_text=email_text[:12000],
                tone=tone_hint,
                points_or_auto=points_text,
            ),
        }],
        temperature=0.3,
    )
    return {"draft": resp["choices"][0]["message"]["content"]}


# ---- 邮件账户读取 ----

def list_inbox(account_id: str, limit: int = 20) -> dict:
    """读取已配置邮件账户的收件箱，返回最新邮件列表（仅标题/发件人/时间，不含正文）。"""
    from . import email_client
    account = accounting.get_email_account(account_id)
    if not account:
        return {"error": f"email account not found: {account_id}"}
    try:
        emails = email_client.list_inbox(account, limit=int(limit))
        return {"emails": emails, "count": len(emails), "account": account.get("email_address")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def search_emails(account_id: str, query: str, limit: int = 10) -> dict:
    """在已配置邮件账户中按关键词搜索邮件（匹配主题或发件人）。"""
    from . import email_client
    account = accounting.get_email_account(account_id)
    if not account:
        return {"error": f"email account not found: {account_id}"}
    try:
        emails = email_client.search_emails(account, query=query, limit=int(limit))
        return {"emails": emails, "count": len(emails), "query": query}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def get_email_thread(account_id: str, uid: str) -> dict:
    """读取一封邮件的完整正文（用于进一步总结、提取任务或起草回复）。"""
    from . import email_client
    account = accounting.get_email_account(account_id)
    if not account:
        return {"error": f"email account not found: {account_id}"}
    try:
        return email_client.get_email_content(account, uid=uid)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def configure_email_account(email_address: str, password: str, provider: str = "") -> dict:
    """通过对话配置邮件账户。自动识别常见服务商（gmail/outlook/icloud/yahoo/qq/163）的 IMAP/SMTP 设置，测试连接后保存。"""
    from . import email_client
    email_address = email_address.strip()
    provider = provider.strip().lower()

    # Auto-detect provider from email domain if not specified
    if not provider:
        domain = email_address.split("@")[-1].lower() if "@" in email_address else ""
        domain_map = {
            "gmail.com": "gmail",
            "googlemail.com": "gmail",
            "outlook.com": "outlook", "hotmail.com": "outlook",
            "live.com": "outlook", "msn.com": "outlook",
            "icloud.com": "icloud", "me.com": "icloud", "mac.com": "icloud",
            "yahoo.com": "yahoo", "yahoo.com.au": "yahoo",
            "qq.com": "qq",
            "163.com": "163", "126.com": "163",
        }
        provider = domain_map.get(domain, "")

    presets = accounting.EMAIL_PROVIDERS.get(provider, {})
    if not presets:
        return {
            "error": f"未能识别服务商。请手动提供 imap_host 和 smtp_host。已支持：{list(accounting.EMAIL_PROVIDERS.keys())}"
        }

    account_draft = {
        **presets,
        "email_address": email_address,
        "username": email_address,
        "password": password,
    }
    test = email_client.test_connection(account_draft)
    if not test.get("ok"):
        return {"error": f"连接测试失败：{test.get('error')}。请确认密码（Gmail/QQ 等需使用应用专用密码）。"}

    saved = accounting.save_email_account(
        email_address=email_address,
        username=email_address,
        password=password,
        imap_host=presets["imap_host"],
        imap_port=presets.get("imap_port", 993),
        imap_ssl=presets.get("imap_ssl", True),
        smtp_host=presets["smtp_host"],
        smtp_port=presets.get("smtp_port", 465),
        smtp_ssl=presets.get("smtp_ssl", True),
        label=f"{provider.upper()} - {email_address}",
    )
    return {
        "ok": True,
        "account_id": saved["id"],
        "email_address": email_address,
        "provider": provider,
        "label": saved["label"],
        "message": f"邮件账户已配置成功，account_id: {saved['id']}",
    }


def list_email_accounts_tool() -> dict:
    """列出所有已配置的邮件账户（不含密码）。"""
    accounts = accounting.list_email_accounts()
    return {"accounts": accounts, "count": len(accounts)}


def send_email_tool(account_id: str, to: str, subject: str, body: str, cc: str = "") -> dict:
    """通过已配置的邮件账户发送邮件。"""
    from . import email_client
    account = accounting.get_email_account(account_id)
    if not account:
        return {"error": f"email account not found: {account_id}"}
    try:
        return email_client.send_email(account, to=to, subject=subject, body=body, cc=cc)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def delete_email_tool(account_id: str, uid: str) -> dict:
    """永久删除收件箱中的一封邮件（不可恢复，请谨慎使用）。uid 来自 list_inbox 或 search_emails。"""
    from . import email_client
    account = accounting.get_email_account(account_id)
    if not account:
        return {"error": f"email account not found: {account_id}"}
    try:
        return email_client.delete_email(account, uid=uid)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def archive_email_tool(account_id: str, uid: str, archive_folder: str = "") -> dict:
    """将一封邮件从收件箱移至归档文件夹（Gmail 对应"所有邮件"，其他服务商对应 Archive）。"""
    from . import email_client
    account = accounting.get_email_account(account_id)
    if not account:
        return {"error": f"email account not found: {account_id}"}
    try:
        return email_client.archive_email(account, uid=uid, archive_folder=archive_folder)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ---- 记忆 ----

_MEMORY_CANDIDATE_PROMPT = """请从以下文本中提取值得长期记住的信息（用户身份资料、长期偏好、项目背景、工作规则、重要决定）。
不要提取临时信息或已知常识。

记忆取舍规则：
- 应该保留：用户姓名、年龄、职业、长期居住城市/区域、长期偏好、长期项目背景、明确要求你以后遵守的规则。
- 不应该保留：当前工作目录、桌面上有哪些文件、某个文件刚被创建/删除、一次性的路径/命令/操作记录、临时调试状态。
- 如果临时信息确实有用，只能标记为 temporary 且 importance <= 2；不要把它当作 preference/project/rule。
- 对同一主题的新事实，提取为一条更准确的新事实，不要同时保留旧事实和新事实。

文本：
{text}

以 JSON 数组返回，每项格式：
{{"key": "短标题", "value": "事实内容", "type": "preference|project|rule|temporary", "importance": 1-5}}

只输出 JSON，不要其他内容。"""

_TEMPORARY_MEMORY_PATTERNS = (
    "工作目录",
    "当前目录",
    "桌面上",
    "desktop",
    "刚删",
    "刚删除",
    "刚创建",
    "刚改",
    "文件夹",
    "localstorage",
    "ledger_records",
)

_IMPORTANT_PROFILE_PATTERNS = (
    "姓名",
    "名字",
    "年龄",
    "职业",
    "程序员",
    "住在",
    "居住",
    "城市",
    "偏好",
    "希望",
)


def extract_memory_candidates(text: str) -> dict:
    """从文本中提取值得长期记住的信息，存为候选记忆（confirmed=0），需用户确认后才生效。"""
    resp = llm.chat_completion(
        messages=[{
            "role": "user",
            "content": _MEMORY_CANDIDATE_PROMPT.format(text=text[:8000]),
        }],
        temperature=0.1,
    )
    raw = resp["choices"][0]["message"]["content"].strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1][4:] if parts[1].startswith("json") else parts[1]
    try:
        candidates_raw = json.loads(raw)
    except json.JSONDecodeError:
        candidates_raw = []

    stored: list[dict] = []
    for c in candidates_raw:
        if not isinstance(c, dict):
            continue
        key = str(c.get("key", "untitled")).strip() or "untitled"
        value = str(c.get("value", "")).strip()
        if not value:
            continue
        memory_type = _normalize_memory_type(c.get("type", "project"))
        importance = _normalize_importance(c.get("importance", 3))
        decision = _memory_candidate_decision(key, value, memory_type, importance)
        if not decision["store"]:
            continue
        memory_type = decision["type"]
        importance = decision["importance"]
        fact_id = memory.store_candidate(
            key=key,
            value=value,
            tags=[],
            type=memory_type,
            importance=importance,
        )
        stored.append({
            "id": fact_id,
            "key": key,
            "value": value,
            "type": memory_type,
            "importance": importance,
        })
    return {
        "candidates": stored,
        "count": len(stored),
        "note": "候选记忆已暂存，请用 confirm_memory 确认或 forget_memory 删除",
    }


def _memory_candidate_decision(key: str, value: str, memory_type: str, importance: int) -> dict:
    """Deterministic memory hygiene guard after LLM extraction."""
    text = f"{key} {value}".lower()
    has_temporary_signal = any(pattern.lower() in text for pattern in _TEMPORARY_MEMORY_PATTERNS)
    has_profile_signal = any(pattern.lower() in text for pattern in _IMPORTANT_PROFILE_PATTERNS)

    if has_temporary_signal and not has_profile_signal:
        return {"store": False, "type": "temporary", "importance": min(importance, 2)}
    if memory_type == "temporary" and importance <= 2 and not has_profile_signal:
        return {"store": False, "type": "temporary", "importance": importance}
    if has_profile_signal:
        return {"store": True, "type": "preference" if memory_type == "temporary" else memory_type, "importance": max(importance, 4)}
    return {"store": True, "type": memory_type, "importance": importance}


def remember(
    key: str,
    value: str,
    tags: Optional[list[str]] = None,
    type: str = "project",
    importance: int = 3,
) -> dict:
    fact_id = memory.remember(
        key, value, tags or [],
        type=_normalize_memory_type(type),
        importance=_normalize_importance(importance),
    )
    return {"ok": True, "id": fact_id}


def recall(query: str, top_k: int = 5) -> dict:
    return {"items": memory.recall(query, top_k=top_k)}


def list_memories(type: Optional[str] = None, show_candidates: bool = False) -> dict:
    """列出长期记忆。show_candidates=True 时只返回待确认的候选记忆。"""
    if show_candidates:
        items = memory.list_memories(type=type, confirmed=0)
    else:
        items = memory.list_memories(type=type, confirmed=1)
    return {"memories": items, "count": len(items)}


def confirm_memory(memory_id: str) -> dict:
    """确认一条候选记忆，使其正式生效。"""
    return memory.confirm_memory(memory_id)


def forget_memory(memory_id: str) -> dict:
    """删除一条记忆（按 ID）。"""
    return memory.forget(memory_id)


def create_cron_job(name: str, schedule: str, task: str, input_file: str = "") -> dict:
    """创建一个定时任务，生成脚本并注册到 Auctus Agent 的 cron 管理器。"""
    if input_file:
        script_body = cronjobs.template_agent_run(input_rel=input_file, task=task)
    else:
        script_body = f'echo "Running: {task}"\n.venv/bin/python agent.py run --task "{task}"\n'
    job = cronjobs.add_job(name=name, schedule=schedule, script_body=script_body, description=task)
    snippet = cronjobs.export_crontab_snippet()
    return {
        "ok": True,
        "job": job,
        "crontab_snippet": snippet,
        "note": "定时任务已创建，到时间会自动执行。",
    }


def list_cron_jobs() -> dict:
    """列出所有已注册的定时任务。"""
    jobs = cronjobs.list_jobs()
    return {"jobs": jobs, "count": len(jobs)}


def _resolve_cron_job_id(name_or_id: str) -> Optional[str]:
    """Find a job id by exact id match first, then by name. Returns None if not found."""
    name_or_id = (name_or_id or "").strip()
    if not name_or_id:
        return None
    for job in cronjobs.list_jobs():
        if job.get("id") == name_or_id or job.get("name") == name_or_id:
            return job.get("id")
    return None


def delete_cron_job(name_or_id: str) -> dict:
    """删除已创建的定时任务。可以传 name 或 id。"""
    job_id = _resolve_cron_job_id(name_or_id)
    if not job_id:
        return {"ok": False, "error": f"找不到定时任务：{name_or_id}"}
    return cronjobs.remove_job(job_id)


def toggle_cron_job(name_or_id: str, enabled: bool) -> dict:
    """启用或停用一个定时任务。enabled=true 启用，false 停用。"""
    job_id = _resolve_cron_job_id(name_or_id)
    if not job_id:
        return {"ok": False, "error": f"找不到定时任务：{name_or_id}"}
    return cronjobs.set_enabled(job_id, bool(enabled))


def list_outputs() -> dict:
    files = [p.name for p in settings.output_dir.iterdir() if p.is_file()]
    return {"files": sorted(files, reverse=True)[:50]}


def fetch_webpage(url: str, max_chars: int = 12000) -> dict:
    """Fetch a public http/https webpage and return readable text."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {"error": "url must start with http:// or https://"}
    max_chars = min(max(int(max_chars or 12000), 1000), 50000)
    request = Request(
        url,
        headers={
            "User-Agent": "AuctusAgent/0.1 (+local personal assistant)",
            "Accept": "text/html,text/plain,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=8) as response:
        raw = response.read(1_000_000)
        content_type = response.headers.get("content-type", "")
    text = raw.decode(_charset_from_content_type(content_type), errors="replace")
    readable = _html_to_text(text) if "html" in content_type.lower() or "<html" in text[:500].lower() else text
    readable = readable.strip()
    if len(readable) > max_chars:
        readable = readable[:max_chars] + f"\n\n[content truncated to {max_chars} characters]"
    return {
        "url": url,
        "content_type": content_type,
        "content": readable,
    }


def search_web(query: str, max_results: int = 5) -> dict:
    """Search the public web. Uses DuckDuckGo by default; if a Tavily or Brave API key is configured in settings, uses that for higher-quality agent-optimized results. Returns titles/snippets/URLs."""
    from . import search_providers
    return search_providers.search(query, max_results)


def _charset_from_content_type(content_type: str) -> str:
    match = re.search(r"charset=([\w.-]+)", content_type or "", re.I)
    return match.group(1) if match else "utf-8"


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<br\s*/?>", "\n", html)
    html = re.sub(r"(?is)</(p|div|section|article|header|footer|li|h[1-6])>", "\n", html)
    text = re.sub(r"(?is)<[^>]+>", " ", html)
    text = unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---- 监控工具 ----

# 物流单号格式识别规则
_CARRIER_PATTERNS: list[tuple[str, str]] = [
    ("UPS",            r"\b1Z[0-9A-Z]{16}\b"),
    ("FedEx",          r"\b\d{12}\b|\b\d{15}\b|\b\d{20}\b"),
    ("USPS",           r"\b(94|93|92|94|95)\d{18,20}\b"),
    ("DHL",            r"\b\d{10}\b"),
    ("Australia Post", r"\b[A-Z]{2}\d{8}AU\b"),
    ("顺丰",           r"\bSF\d{12}\b"),
    ("中通",           r"\b7[3-9]\d{9}\b"),
    ("圆通",           r"\bYT\d{16}\b"),
    ("韵达",           r"\bYD\d{16}\b"),
    ("EMS",            r"\bE[A-Z]\d{9}CN\b"),
]


def _detect_carrier(number: str) -> str:
    for carrier, pattern in _CARRIER_PATTERNS:
        if re.search(pattern, number, re.I):
            return carrier
    return "unknown"


def track_logistics(tracking_number: str, carrier: str = "") -> dict:
    """从快递单号查询物流状态。自动识别快递公司（UPS/FedEx/顺丰/EMS/澳邮等）并搜索最新状态。carrier 可选，留空则自动识别。"""
    number = tracking_number.strip()
    if not number:
        return {"error": "tracking number is empty"}
    detected = carrier.strip() or _detect_carrier(number)
    query = f"track package {number} {detected}" if detected != "unknown" else f"track package {number}"
    try:
        result = search_web(query, max_results=3)
        return {
            "tracking_number": number,
            "detected_carrier": detected,
            "search_results": result.get("results", []),
            "note": "以上为搜索结果，如需精准查询请访问快递公司官网或配置官方 API Key。",
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def fetch_element(url: str, selector: str, attribute: str = "") -> dict:
    """用 CSS selector 精准抓取网页中特定元素的文本或属性值。适合价格监控、库存状态、发布日期等定向提取。selector 为标准 CSS 选择器（如 '.price'、'#stock'）。"""
    from bs4 import BeautifulSoup
    import requests as _req

    url = url.strip()
    selector = selector.strip()
    if not url or not selector:
        return {"error": "url and selector are required"}

    headers = {"User-Agent": "Mozilla/5.0 (compatible; AuctusAgent/0.1; personal use)"}
    try:
        resp = _req.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        return {"ok": False, "error": f"fetch failed: {e}"}

    soup = BeautifulSoup(resp.text, "lxml")
    elements = soup.select(selector)
    if not elements:
        return {"ok": False, "url": url, "selector": selector, "error": "no elements matched selector"}

    results = []
    for el in elements[:10]:
        val = el.get(attribute, "") if attribute else el.get_text(strip=True)
        results.append(val)

    return {
        "ok": True,
        "url": url,
        "selector": selector,
        "attribute": attribute or "text",
        "matches": results,
        "count": len(results),
    }


# ---- IoT 网关控制 ----

_IOT_CONFIG_PATH = Path(__file__).parent.parent / "data" / "iot_gateways.json"


def configure_iot_gateway(
    gateway_type: str,
    base_url: str,
    api_token: str,
    confirmed: bool = False,
) -> dict:
    """保存 IoT 网关配置（URL + Token）。支持 Home Assistant、Tuya 等主流平台。"""
    if not confirmed:
        return {"error": "need confirmed=true to save gateway credentials"}
    base_url = base_url.rstrip("/").strip()
    if not base_url or not api_token:
        return {"error": "base_url and api_token are required"}
    gateway_type = (gateway_type or "generic").lower().strip()
    encrypted = accounting._encrypt_secret(api_token)
    hint = f"...{api_token[-4:]}" if len(api_token) >= 4 else "****"
    _IOT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if _IOT_CONFIG_PATH.exists():
        try:
            existing = json.loads(_IOT_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing[gateway_type] = {
        "type": gateway_type,
        "base_url": base_url,
        "encrypted_token": encrypted,
        "token_hint": hint,
        "updated_at": datetime.now().isoformat(),
    }
    _IOT_CONFIG_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "gateway_type": gateway_type,
        "base_url": base_url,
        "token_hint": hint,
        "message": f"{gateway_type} 网关已配置，token 已加密保存",
    }


def call_iot_gateway(
    endpoint: str,
    method: str = "GET",
    payload: Optional[dict] = None,
    gateway_type: str = "home_assistant",
    confirmed: bool = False,
) -> dict:
    """调用已配置的 IoT 网关 REST API（Home Assistant /api/...、Tuya 等）。需先用 configure_iot_gateway 完成配置。"""
    import requests as _req

    if not confirmed:
        return {"error": "need confirmed=true to call IoT gateway"}
    if not _IOT_CONFIG_PATH.exists():
        return {"error": "没有已配置的 IoT 网关。请先调用 configure_iot_gateway 设置网关 URL 和 Token。"}
    try:
        configs: dict = json.loads(_IOT_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"error": "IoT 配置文件损坏，请重新配置"}
    gw_type = (gateway_type or "home_assistant").lower().strip()
    cfg = configs.get(gw_type)
    if not cfg:
        return {"error": f"未找到 '{gw_type}' 网关配置。已配置: {list(configs.keys())}"}
    token = accounting._decrypt_secret(cfg["encrypted_token"])
    url = cfg["base_url"] + "/" + endpoint.lstrip("/")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    method = (method or "GET").upper()
    try:
        if method == "GET":
            resp = _req.get(url, headers=headers, timeout=15)
        elif method == "POST":
            resp = _req.post(url, headers=headers, json=payload or {}, timeout=15)
        elif method == "PUT":
            resp = _req.put(url, headers=headers, json=payload or {}, timeout=15)
        elif method == "DELETE":
            resp = _req.delete(url, headers=headers, timeout=15)
        else:
            return {"error": f"不支持的 HTTP 方法: {method}"}
        status = resp.status_code
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        return {"ok": status < 300, "status_code": status, "url": url, "method": method, "response": body}
    except Exception as e:
        return {"ok": False, "error": f"网关请求失败: {e}", "url": url}


# ---------- 工具安全与日志 ----------

_RISK = {
    "read_file": "medium",
    "write_file": "medium",
    "analyze_image": "low",
    "run_terminal_command": "high",
    "terminal_session_start": "high",
    "terminal_session_list": "low",
    "terminal_session_tail": "low",
    "terminal_session_send": "high",
    "terminal_session_stop": "high",
    "summarize_text": "low",
    "summarize_email_text": "low",
    "extract_email_tasks": "low",
    "draft_email_reply": "low",
    "list_inbox": "low",
    "search_emails": "low",
    "get_email_thread": "low",
    "configure_email_account": "high",
    "list_email_accounts_tool": "low",
    "send_email_tool": "high",
    "delete_email_tool": "high",
    "archive_email_tool": "medium",
    "make_markdown_report": "low",
    "make_spreadsheet": "low",
    "make_webpage": "medium",
    "make_react_prototype": "medium",
    "extract_memory_candidates": "medium",
    "remember": "medium",
    "recall": "low",
    "list_outputs": "low",
    "fetch_webpage": "low",
    "fetch_element": "low",
    "track_logistics": "low",
    "configure_iot_gateway": "high",
    "call_iot_gateway": "high",
    "search_web": "low",
    "list_memories": "low",
    "confirm_memory": "medium",
    "forget_memory": "high",
    "run_parallel_subagents": "low",
}

def _validate_tool_args(name: str, args: dict) -> Optional[str]:
    """校验参数是否满足 schema 的 required。返回错误信息或 None。"""
    required = _TOOL_REQUIRED_PARAMS.get(name)
    if not required:
        return None
    missing = required - set(args.keys())
    if missing:
        return f"missing required arguments: {sorted(missing)}"
    return None


def _normalize_memory_type(value: Any) -> str:
    value = str(value or "project")
    return value if value in _MEMORY_TYPES else "project"


def _normalize_importance(value: Any) -> int:
    try:
        importance = int(value)
    except (TypeError, ValueError):
        importance = 3
    return min(5, max(1, importance))


def _has_explicit_user_approval(name: str, user_input: str) -> bool:
    text = (user_input or "").lower()
    if name == "remember":
        return any(k.lower() in text for k in _REMEMBER_KEYWORDS)
    if name == "forget_memory":
        return any(k.lower() in text for k in _FORGET_KEYWORDS)
    if name in {"run_terminal_command", "terminal_session_start", "terminal_session_send", "terminal_session_stop"}:
        return any(k.lower() in text for k in _TERMINAL_KEYWORDS)
    return False


def _can_accept_implicit_soft_trash_confirmation(name: str, args: dict, user_input: str) -> bool:
    """Allow UI-confirmed safe file moves even if the model omits confirmed:true."""
    if name != "run_terminal_command":
        return False
    if not (_has_explicit_user_approval(name, user_input) or _is_affirmative_confirmation(user_input)):
        return False
    command = str(args.get("command") or "").strip()
    return _is_soft_trash_command(command) or _is_restore_from_trash_command(command)


def _is_affirmative_confirmation(user_input: str) -> bool:
    return (user_input or "").strip().lower() in _AFFIRMATIVE_CONFIRMATIONS


def _is_soft_trash_command(command: str) -> bool:
    lowered = command.strip().lower()
    if re.search(r"(^|[\s;&|])rm(\s|$)", lowered):
        return False
    if ".trash" in lowered and re.search(r"(^|[\s;&|])mv\s+", lowered):
        return True
    if "osascript" in lowered and "trash" in lowered:
        return True
    return False


def _is_restore_from_trash_command(command: str) -> bool:
    lowered = command.strip().lower()
    if re.search(r"(^|[\s;&|])rm(\s|$)", lowered):
        return False
    has_trash_source = ".trash" in lowered or "first item of trash" in lowered or " of trash " in lowered
    has_desktop_target = "/desktop" in lowered or "~/desktop" in lowered or "folder \"desktop\"" in lowered
    if not (has_trash_source and has_desktop_target):
        return False
    return bool(re.search(r"(^|[\s;&|])mv\s+", lowered) or "osascript" in lowered)


def _write_log(
    name: str,
    args: dict,
    result: Any,
    duration_ms: float,
    *,
    task_id: str = "",
    user_input: str = "",
    model: str = "",
    token_usage: Optional[dict] = None,
    status: str = "success",
    error: str = "",
) -> None:
    log_path = settings.logs_dir / "tool_calls.jsonl"
    safe_result = result
    if isinstance(result, dict) and "content" in result and isinstance(result["content"], str):
        safe_result = {**result, "content": result["content"][:200] + "…"}
    entry = {
        "task_id": task_id,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "user_input": user_input[:500] if user_input else "",
        "tool_name": name,
        "risk_level": _RISK.get(name, "unknown"),
        "tool_input": args,
        "tool_output": safe_result,
        "model": model,
        "token_usage": token_usage or {},
        "status": status,
        "error": error,
        "duration_ms": round(duration_ms, 1),
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _attach_retry_hint(result: Any, name: str, error: str, user_input: str) -> Any:
    if not error:
        return result
    hint = evolution.build_tool_retry_hint(tool_name=name, error=error, user_input=user_input)
    if not hint:
        return result
    if isinstance(result, dict):
        return {**result, "retry_hint": hint}
    return {"error": error, "result": result, "retry_hint": hint}


# ---------- Telegram / Feishu 集成 ----------

def get_integration_status() -> dict:
    """查看当前已配置的第三方连接状态（Telegram、飞书等）。"""
    from .config import settings
    from . import accounting as _acct
    state = _acct.get_setup_state()
    tg_token = settings.telegram_bot_token or ""
    tg_ids = settings.telegram_allowed_user_ids or ""
    feishu_app_id = state.get("feishu_app_id", "")
    return {
        "telegram": {
            "configured": bool(tg_token),
            "token_hint": f"...{tg_token[-6:]}" if len(tg_token) > 6 else ("(未配置)" if not tg_token else tg_token),
            "allowed_user_ids": tg_ids,
        },
        "feishu": {
            "configured": bool(feishu_app_id),
            "app_id": feishu_app_id if feishu_app_id else "(未配置)",
            "receive_mode": state.get("feishu_receive_mode", "websocket"),
            "domain": state.get("feishu_domain", "feishu"),
        },
    }


def configure_telegram(bot_token: str = "", allowed_user_ids: str = "", confirmed: bool = False) -> dict:
    """在对话中配置 Telegram Bot。验证 token，自动获取用户 ID（如未提供），保存到 .env。"""
    import urllib.request, urllib.parse, json as _json
    from .config import settings
    from pathlib import Path as _Path

    if not confirmed:
        return {"error": "需要用户确认。请在参数中加入 confirmed: true。"}

    token = bot_token.strip()
    if not token:
        return {"error": "需要提供 bot_token。请告知用户去 Telegram 找 @BotFather 发 /newbot 获取。"}

    def _tg(method: str, params: dict | None = None) -> dict:
        url = f"https://api.telegram.org/bot{token}/{method}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                return _json.loads(r.read())
        except Exception as e:
            return {"ok": False, "description": str(e)}

    verify = _tg("getMe")
    if not verify.get("ok"):
        return {"error": f"Token 无效：{verify.get('description', '验证失败')}"}

    bot_info = verify.get("result", {})
    bot_username = bot_info.get("username", "")

    ids_str = allowed_user_ids.strip()
    if not ids_str:
        updates = _tg("getUpdates", {"limit": 20, "timeout": 0})
        if updates.get("ok"):
            seen: dict[int, str] = {}
            for upd in updates.get("result", []):
                sender = (upd.get("message") or {}).get("from") or {}
                uid = sender.get("id")
                if uid and uid not in seen:
                    name = " ".join(filter(None, [sender.get("first_name"), sender.get("last_name")])) or str(uid)
                    seen[uid] = name
            if seen:
                ids_str = ",".join(str(k) for k in seen)

    env_path = _Path(".env")
    try:
        from dotenv import set_key as _set_key
        env_path.touch()
        _set_key(str(env_path), "TELEGRAM_BOT_TOKEN", token)
        if ids_str:
            _set_key(str(env_path), "TELEGRAM_ALLOWED_USER_IDS", ids_str)
    except Exception as e:
        return {"error": f"写入 .env 失败：{e}"}

    settings.telegram_bot_token = token
    settings.telegram_allowed_user_ids = ids_str

    return {
        "ok": True,
        "bot_username": bot_username,
        "allowed_user_ids": ids_str or "(未设置)",
        "message": (
            f"Telegram Bot 已配置成功！Bot 用户名：@{bot_username}。"
            + (f" 已绑定用户 ID：{ids_str}。" if ids_str else " 建议先发 /start 给 Bot，再重新调用以自动获取你的用户 ID。")
            + " 在手机 Telegram 搜索并打开这个 Bot，即可开始使用。"
        ),
    }


def configure_feishu(app_id: str = "", app_secret: str = "", verification_token: str = "", receive_mode: str = "websocket", domain: str = "feishu", confirmed: bool = False) -> dict:
    """在对话中配置飞书机器人。默认使用 WebSocket 长连接，不需要公网地址。"""
    import urllib.request, json as _json
    from . import accounting as _acct

    if not confirmed:
        return {"error": "需要用户确认。请在参数中加入 confirmed: true。"}

    app_id = app_id.strip()
    app_secret = app_secret.strip()
    verification_token = verification_token.strip()
    receive_mode = (receive_mode or "websocket").strip().lower()
    domain = (domain or "feishu").strip().lower()
    if not app_id or not app_secret:
        return {"error": "需要提供 app_id 和 app_secret。请到飞书开放平台 open.feishu.cn 创建应用后获取。"}
    if receive_mode not in {"websocket", "webhook"}:
        return {"error": "receive_mode 必须是 websocket 或 webhook。桌面版推荐 websocket。"}
    if domain not in {"feishu", "lark"}:
        return {"error": "domain 必须是 feishu 或 lark。"}

    def _get_token() -> tuple[str, str]:
        api_host = "open.larksuite.com" if domain == "lark" else "open.feishu.cn"
        url = f"https://{api_host}/open-apis/auth/v3/tenant_access_token/internal"
        data = _json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                resp = _json.loads(r.read())
                if resp.get("code") == 0:
                    return resp.get("tenant_access_token", ""), ""
                return "", resp.get("msg", "验证失败")
        except Exception as e:
            return "", str(e)

    access_token, err = _get_token()
    if not access_token:
        return {"error": f"飞书验证失败：{err}。请检查 App ID 和 App Secret 是否正确。"}

    _acct.set_setup_state({
        "feishu_app_id": app_id,
        "feishu_app_secret": app_secret,
        "feishu_verification_token": verification_token,
        "feishu_receive_mode": receive_mode,
        "feishu_domain": domain,
    })

    if receive_mode == "websocket":
        try:
            from . import feishu_bot as _feishu_bot
            _feishu_bot.run_in_thread()
        except Exception as e:
            return {"error": f"飞书配置已保存，但启动长连接失败：{type(e).__name__}: {e}"}

    msg = (
        f"飞书机器人已配置成功！App ID：{app_id}。"
        "当前使用 WebSocket 长连接模式：Auctus 会主动连接飞书开放平台，不需要公网地址。"
        "请在飞书开放平台的【事件订阅】选择【使用长连接接收事件】，订阅 im.message.receive_v1，"
        "并开通机器人接收消息和发送消息权限。"
    )
    if verification_token:
        msg += " Verification Token 已保存，主要用于 Webhook 备用模式。"
    return {"ok": True, "app_id": app_id, "receive_mode": receive_mode, "domain": domain, "message": msg}


def configure_discord(bot_token: str = "", allowed_user_ids: str = "", confirmed: bool = False) -> dict:
    """配置 Discord Bot。验证 token，保存到 .env，并立即起 bot 线程。"""
    import urllib.request, json as _json
    from .config import settings as _settings
    from pathlib import Path as _Path

    if not confirmed:
        return {"error": "需要用户确认。请在参数中加入 confirmed: true。"}

    token = (bot_token or "").strip()
    if not token:
        return {"error": "需要提供 bot_token。请告知用户去 https://discord.com/developers/applications 新建 application → Bot → Reset Token。"}

    # Verify the token by calling Discord's /users/@me
    try:
        req = urllib.request.Request(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bot {token}", "User-Agent": "AuctusAgent (configure, 1.0)"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            info = _json.loads(r.read())
    except Exception as exc:
        return {"error": f"Token 验证失败：{type(exc).__name__}: {exc}。请检查 bot token 是否正确，且 application 已勾选 MESSAGE CONTENT INTENT。"}

    bot_username = info.get("username", "")
    bot_id = info.get("id", "")
    if not bot_id:
        return {"error": "Token 看起来有效，但 Discord 没返回 bot id。"}

    ids_str = (allowed_user_ids or "").strip()
    env_path = _Path(".env")
    try:
        from dotenv import set_key as _set_key
        env_path.touch()
        _set_key(str(env_path), "DISCORD_BOT_TOKEN", token)
        if ids_str:
            _set_key(str(env_path), "DISCORD_ALLOWED_USER_IDS", ids_str)
    except Exception as exc:
        return {"error": f"写入 .env 失败：{exc}"}

    _settings.discord_bot_token = token
    if ids_str:
        _settings.discord_allowed_user_ids = ids_str

    # Start (or restart) the bot thread now that the token is in place
    try:
        from . import discord_bot as _dc_bot
        _dc_bot.run_in_thread()
    except Exception as exc:
        return {"error": f"Discord 配置已保存，但启动 bot 失败：{type(exc).__name__}: {exc}"}

    invite_url = (
        f"https://discord.com/api/oauth2/authorize?client_id={bot_id}"
        "&permissions=2147568640&scope=bot"
    )
    return {
        "ok": True,
        "bot_username": bot_username,
        "bot_id": bot_id,
        "invite_url": invite_url,
        "message": (
            f"Discord bot ({bot_username}) 已配置成功。点这个链接把它拉进你的服务器或 DM 它："
            f"{invite_url}"
        ),
    }


# ---------- 工具 schema（喂给 LLM）----------

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取已授权路径内的文件或目录。默认可读 workspace；如果用户在当前聊天里粘贴了绝对文件/文件夹路径，也可以读取该路径。支持 txt/md/json/html/css/js/ts/py/pdf/csv/xlsx/docx；目录会返回文件列表；图片会返回文件信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于授权 workspace 的文件名，或用户当前消息里明确给出的绝对路径。",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "在已授权 workspace 或用户当前聊天明确给出的文件夹/文件路径内写入或覆盖文本文件。只能写 txt/md/json/csv/html/css/js/jsx/ts/tsx/py 等文本文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于授权 workspace 的文件路径，例如 'notes/todo.md'",
                    },
                    "content": {"type": "string", "description": "要写入的完整文本内容"},
                    "overwrite": {"type": "boolean", "description": "是否覆盖已有文件，默认 true"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_image",
            "description": "分析已授权路径内的图片或截图。支持 png/jpg/jpeg/webp/gif。可提取图片文字、理解 UI 截图、图表、表格、报错信息。需要当前模型支持 vision。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于授权 workspace 的图片路径，或用户当前消息里明确给出的绝对图片路径。",
                    },
                    "question": {
                        "type": "string",
                        "description": "希望模型重点回答的问题，例如“这个截图报错是什么意思？”",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_terminal_command",
            "description": "执行本机终端命令。只有 Settings 已启用终端权限时才能使用。默认工作目录为授权 workspace；若文件权限为整台电脑，可指定其他工作目录。会拦截明显危险命令。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的 shell 命令"},
                    "working_directory": {"type": "string", "description": "命令执行目录，可选"},
                    "timeout_seconds": {"type": "integer", "description": "超时时间，默认 30 秒，最大 120 秒"},
                    "confirmed": {"type": "boolean", "description": "当你理解用户意图是执行此操作时，设为 true。不限语言或措辞，只要你判断用户确实想执行该命令即可。"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_session_start",
            "description": "启动一个受控的长期终端 session，用于 Claude Code、Codex、测试、构建、开发服务器等持续运行任务。启动后可用 terminal_session_tail 查看输出、terminal_session_send 继续输入、terminal_session_stop 停止。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要启动的 shell 命令"},
                    "working_directory": {"type": "string", "description": "命令执行目录，可选"},
                    "label": {"type": "string", "description": "方便用户识别的短标签，可选"},
                    "confirmed": {"type": "boolean", "description": "用户明确要求启动此终端任务时设为 true"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_session_list",
            "description": "列出 Auctus Agent 管理的长期终端 sessions，包括状态、命令、工作目录和 session_id。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_session_tail",
            "description": "查看受控终端 session 最近输出，用于判断 Claude Code/Codex/构建/测试是否完成、卡住或报错。",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "terminal_session_start 或 list 返回的 session id"},
                    "lines": {"type": "integer", "description": "返回最近多少行，默认 80，最大 500"},
                },
                "required": ["session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_session_send",
            "description": "向受控终端 session 发送输入，例如给 Claude Code/Codex 继续发新指令。必须是用户明确要求继续发送时才可使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "目标 session id"},
                    "text": {"type": "string", "description": "要发送到 stdin 的文本"},
                    "append_newline": {"type": "boolean", "description": "是否自动追加换行，默认 true"},
                    "confirmed": {"type": "boolean", "description": "用户明确要求发送此输入时设为 true"},
                },
                "required": ["session_id", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_session_stop",
            "description": "停止一个受控终端 session。默认发送 TERM，force=true 时强制终止。",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "目标 session id"},
                    "force": {"type": "boolean", "description": "是否强制终止，默认 false"},
                    "confirmed": {"type": "boolean", "description": "用户明确要求停止时设为 true"},
                },
                "required": ["session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_text",
            "description": "对一段长文（文章、邮件、会议纪要等）做总结。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "style": {"type": "string", "enum": ["bullet", "executive", "qa"]},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_email_text",
            "description": "总结粘贴的邮件内容，提取发件人、主题、核心目的、关键信息、行动项和紧急程度。",
            "parameters": {
                "type": "object",
                "properties": {
                    "email_text": {"type": "string", "description": "完整的邮件正文内容"},
                },
                "required": ["email_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_email_tasks",
            "description": "从邮件中提取所有待办事项/行动项，含任务描述、截止日期、负责人和优先级。",
            "parameters": {
                "type": "object",
                "properties": {
                    "email_text": {"type": "string", "description": "完整的邮件正文内容"},
                },
                "required": ["email_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_email_reply",
            "description": "根据原始邮件起草回复草稿。只生成文本，不会自动发送邮件。禁止执行任何发送、删除、归档操作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "email_text": {"type": "string", "description": "原始邮件正文"},
                    "tone": {"type": "string", "enum": ["professional", "friendly", "concise", "apologetic"], "description": "回复语气风格"},
                    "points": {"type": "array", "items": {"type": "string"}, "description": "必须在回复中提及的要点（可选）"},
                },
                "required": ["email_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "make_markdown_report",
            "description": "生成 Markdown 报告文件（.md），适合项目总结、分析报告。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "报告标题"},
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "content": {"type": "string", "description": "Markdown 格式正文"},
                            },
                            "required": ["heading", "content"],
                        },
                    },
                },
                "required": ["title", "sections"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "make_spreadsheet",
            "description": "生成 xlsx 表格文件并返回保存路径。多表的话传多个 sheet。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "文件标题（也是文件名一部分）"},
                    "sheets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "headers": {"type": "array", "items": {"type": "string"}},
                                "rows": {"type": "array", "items": {"type": "array"}},
                            },
                            "required": ["name", "headers", "rows"],
                        },
                    },
                },
                "required": ["title", "sheets"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "make_webpage",
            "description": "生成一个简洁的 HTML 报告页面，适合做总结/纪要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "html": {"type": "string", "description": "HTML 片段"},
                            },
                            "required": ["heading", "html"],
                        },
                    },
                },
                "required": ["title", "sections"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "make_react_prototype",
            "description": "生成 React + Tailwind 单页原型 HTML（CDN 版，可直接浏览器打开，无需构建）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "页面标题"},
                    "jsx_code": {
                        "type": "string",
                        "description": "完整 JSX 代码，必须包含 function App() {...} 组件，可使用 Tailwind class。",
                    },
                },
                "required": ["title", "jsx_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_memory_candidates",
            "description": "从文本中识别值得长期记住的信息（用户偏好、项目背景、规则），暂存为候选记忆（未确认），返回带 ID 的列表。用户需用 confirm_memory 确认或 forget_memory 删除。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要分析的文本"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "把一条值得长期记住的事实写入长期记忆（直接确认，立即生效）。例如用户偏好、项目名、关键人物。",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "短标题，例如 'preferred_language'"},
                    "value": {"type": "string", "description": "事实内容"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "type": {
                        "type": "string",
                        "enum": ["preference", "project", "rule", "temporary"],
                        "description": "记忆类型，默认 project",
                    },
                    "importance": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                        "description": "重要程度 1-5，默认 3",
                    },
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "用自然语言搜索长期记忆，返回最相关的若干条事实。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_inbox",
            "description": "读取已配置邮件账户的收件箱最新邮件（仅标题/发件人/时间，不含正文）。需先在设置里添加邮件账户并获取 account_id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_id": {"type": "string", "description": "邮件账户 ID（从设置中的邮件账户列表获取）"},
                    "limit": {"type": "integer", "description": "返回最多几封，默认 20，最大 50"},
                },
                "required": ["account_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_emails",
            "description": "在已配置邮件账户中按关键词搜索邮件，匹配主题或发件人。",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_id": {"type": "string", "description": "邮件账户 ID"},
                    "query": {"type": "string", "description": "搜索关键词（匹配主题或发件人）"},
                    "limit": {"type": "integer", "description": "最多返回几封，默认 10，最大 30"},
                },
                "required": ["account_id", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_email_thread",
            "description": "读取一封邮件的完整正文内容，用于进一步总结、提取任务或起草回复。uid 来自 list_inbox 或 search_emails 的返回值。",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_id": {"type": "string", "description": "邮件账户 ID"},
                    "uid": {"type": "string", "description": "邮件 UID（来自 list_inbox 或 search_emails 结果）"},
                },
                "required": ["account_id", "uid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_email_account",
            "description": "通过对话配置邮件账户。自动识别 Gmail/Outlook/iCloud/Yahoo/QQ/163 的服务器设置，测试连接后加密保存。首次使用邮件功能时调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "email_address": {"type": "string", "description": "邮箱地址，例如 user@gmail.com"},
                    "password": {"type": "string", "description": "邮箱密码或应用专用密码（Gmail/QQ 等需在账户设置中生成）"},
                    "provider": {"type": "string", "description": "服务商名称（可选，留空则自动从邮箱域名识别）：gmail / outlook / icloud / yahoo / qq / 163"},
                    "confirmed": {"type": "boolean", "description": "用户已确认提供凭据并同意保存，必须为 true 才能执行"},
                },
                "required": ["email_address", "password", "confirmed"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_email_accounts_tool",
            "description": "列出所有已配置的邮件账户及其 account_id，不含密码。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email_tool",
            "description": "通过已配置的邮件账户发送邮件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_id": {"type": "string", "description": "发件账户 ID（来自 list_email_accounts_tool）"},
                    "to": {"type": "string", "description": "收件人邮箱，多个用逗号分隔"},
                    "subject": {"type": "string", "description": "邮件主题"},
                    "body": {"type": "string", "description": "邮件正文（纯文本）"},
                    "cc": {"type": "string", "description": "抄送邮箱，可选，多个用逗号分隔"},
                    "confirmed": {"type": "boolean", "description": "用户已确认发送此邮件，必须为 true"},
                },
                "required": ["account_id", "to", "subject", "body", "confirmed"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_email_tool",
            "description": "永久删除收件箱中的一封邮件（不可恢复）。uid 来自 list_inbox 或 search_emails。",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_id": {"type": "string", "description": "邮件账户 ID"},
                    "uid": {"type": "string", "description": "要删除的邮件 UID"},
                    "confirmed": {"type": "boolean", "description": "用户已确认永久删除此邮件，必须为 true"},
                },
                "required": ["account_id", "uid", "confirmed"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "archive_email_tool",
            "description": "将一封邮件从收件箱移至归档（Gmail 为'所有邮件'，其他服务商为 Archive 文件夹）。uid 来自 list_inbox 或 search_emails。",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_id": {"type": "string", "description": "邮件账户 ID"},
                    "uid": {"type": "string", "description": "要归档的邮件 UID"},
                    "archive_folder": {"type": "string", "description": "归档文件夹名（可选，留空则自动选择）"},
                    "confirmed": {"type": "boolean", "description": "用户已确认归档此邮件，必须为 true"},
                },
                "required": ["account_id", "uid", "confirmed"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_outputs",
            "description": "列出最近生成的输出文件（表格/网页/报告）。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_webpage",
            "description": "读取一个公开 http/https 网页并提取可读文本。适合用户要求查看网页、总结网页或基于 URL 做分析。不支持登录后页面，也不做搜索引擎检索。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "完整网页 URL，必须以 http:// 或 https:// 开头"},
                    "max_chars": {"type": "integer", "description": "最多返回多少字符，默认 12000，最大 50000"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "搜索公开网页，返回标题、URL 和摘要。适合用户要求查价格、查新闻、查资料但没有给具体 URL 的任务。拿到搜索结果后，可再用 fetch_webpage 打开最相关页面。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {"type": "integer", "description": "最多返回多少条结果，默认 5，最大 10"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "track_logistics",
            "description": "查询快递/物流单号的最新状态。自动识别 UPS/FedEx/顺丰/EMS/澳邮/中通/圆通/韵达等常见快递公司。返回搜索结果供 Agent 解析状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "tracking_number": {"type": "string", "description": "物流单号"},
                    "carrier": {"type": "string", "description": "快递公司名称（可选，留空则自动从单号格式识别）"},
                },
                "required": ["tracking_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_element",
            "description": "用 CSS selector 精准抓取网页中特定元素的文本或属性值。适合价格监控、库存状态检测、发布日期追踪等场景。注意：对需要 JS 渲染或登录的页面效果有限。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "目标网页 URL"},
                    "selector": {"type": "string", "description": "CSS 选择器，例如 '.price'、'#stock-status'、'span.availability'"},
                    "attribute": {"type": "string", "description": "提取元素的哪个属性（可选，默认提取文本内容）。例如 'href'、'data-price'"},
                },
                "required": ["url", "selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_memories",
            "description": "列出长期记忆。可按类型过滤，或只列待确认的候选记忆。",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["preference", "project", "rule", "temporary"],
                        "description": "按记忆类型过滤（可选）",
                    },
                    "show_candidates": {
                        "type": "boolean",
                        "description": "true = 只返回待确认的候选记忆；false（默认）= 只返回已确认记忆",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_memory",
            "description": "确认一条候选记忆，使其正式生效。需提供记忆 ID（来自 extract_memory_candidates 或 list_memories 的返回值）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string", "description": "要确认的记忆 UUID"},
                },
                "required": ["memory_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget_memory",
            "description": "永久删除一条记忆（已确认或候选均可）。需提供记忆 ID。",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string", "description": "要删除的记忆 UUID"},
                    "confirmed": {"type": "boolean", "description": "必须传 true 才会执行删除"},
                },
                "required": ["memory_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_cron_job",
            "description": "创建定时任务。用户说每天/每周/某点钟执行某事时使用。**你必须自己把自然语言时间表达转成标准 5 段 cron 表达式**：'每天早上 8 点' → '0 8 * * *'；'每周一 9 点' → '0 9 * * 1'；'每月 1 号' → '0 0 1 * *'；'每小时' → '0 * * * *'。Auctus Agent 内置 scheduler 会按时直接在 app 内执行任务（不需要用户管 crontab）。创建成功后简单告知用户任务已设好，不要提技术细节。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "任务名称，简短易读，如 daily-report"},
                    "schedule": {"type": "string", "description": "标准 5 段 cron 表达式：分 时 日 月 周。例如 '0 9 * * *' 每天 9 点；'30 18 * * 5' 每周五 18:30。"},
                    "task": {"type": "string", "description": "任务描述（用户自然语言原话即可），到时间会作为 user message 发给 agent 执行"},
                    "input_file": {"type": "string", "description": "可选，inputs/ 下的文件名，如 data.md。有文件时 agent 会读取该文件执行任务"},
                },
                "required": ["name", "schedule", "task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_cron_job",
            "description": "删除已创建的定时任务。可以按 name 或 id 删除。用户说'取消/删除定时任务 xxx'时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name_or_id": {"type": "string", "description": "任务名称或 id"},
                },
                "required": ["name_or_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_cron_job",
            "description": "启用或停用一个定时任务（不删除）。停用后到点不会执行。用户说'暂停 xxx 任务'或'恢复 xxx 任务'时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name_or_id": {"type": "string", "description": "任务名称或 id"},
                    "enabled": {"type": "boolean", "description": "true 启用 / false 停用"},
                },
                "required": ["name_or_id", "enabled"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_cron_jobs",
            "description": "列出所有已注册的定时任务。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_iot_gateway",
            "description": "保存 IoT 网关配置（base URL + API token）。支持 Home Assistant、Tuya 等平台。配置加密存储后可用 call_iot_gateway 控制设备。",
            "parameters": {
                "type": "object",
                "properties": {
                    "gateway_type": {
                        "type": "string",
                        "enum": ["home_assistant", "tuya", "generic"],
                        "description": "网关类型，例如 'home_assistant' 或 'tuya'",
                    },
                    "base_url": {"type": "string", "description": "网关 base URL，例如 'http://homeassistant.local:8123'"},
                    "api_token": {"type": "string", "description": "访问令牌（Long-Lived Access Token 或 API Key）"},
                    "confirmed": {"type": "boolean", "description": "必须传 true 才会保存凭证"},
                },
                "required": ["gateway_type", "base_url", "api_token"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_iot_gateway",
            "description": "调用已配置的 IoT 网关 REST API 控制或查询设备状态。例如：开灯、关空调、查传感器读数。需先用 configure_iot_gateway 完成配置。",
            "parameters": {
                "type": "object",
                "properties": {
                    "endpoint": {"type": "string", "description": "API 路径，例如 '/api/states/light.living_room' 或 '/api/services/light/turn_on'"},
                    "method": {
                        "type": "string",
                        "enum": ["GET", "POST", "PUT", "DELETE"],
                        "description": "HTTP 方法（默认 GET）",
                    },
                    "payload": {"type": "object", "description": "POST/PUT 请求体，例如 {\"entity_id\": \"light.living_room\"}"},
                    "gateway_type": {
                        "type": "string",
                        "enum": ["home_assistant", "tuya", "generic"],
                        "description": "要调用哪个网关（默认 home_assistant）",
                    },
                    "confirmed": {"type": "boolean", "description": "必须传 true 才会执行调用"},
                },
                "required": ["endpoint"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_integration_status",
            "description": "查看当前已配置的第三方连接状态（Telegram、飞书等），包括是否已启用、账号信息。用户问【我有没有连接 Telegram/飞书】时调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_telegram",
            "description": "在对话中配置 Telegram Bot。用户说【连接/配置/绑定 Telegram】时调用。先询问 Bot Token，验证后自动获取用户 ID，保存配置。",
            "parameters": {
                "type": "object",
                "properties": {
                    "bot_token": {"type": "string", "description": "从 @BotFather 获取的 Bot Token"},
                    "allowed_user_ids": {"type": "string", "description": "允许使用 Bot 的 Telegram 用户 ID（逗号分隔），留空则自动从 getUpdates 获取"},
                    "confirmed": {"type": "boolean", "description": "必须传 true 才会保存配置"},
                },
                "required": ["bot_token", "confirmed"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_feishu",
            "description": "在对话中配置飞书机器人。用户说【连接/配置/绑定飞书】时调用。默认使用 WebSocket 长连接接收事件，不需要公网地址。先询问 App ID 和 App Secret，验证后保存。",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_id": {"type": "string", "description": "飞书开放平台应用的 App ID"},
                    "app_secret": {"type": "string", "description": "飞书开放平台应用的 App Secret"},
                    "verification_token": {"type": "string", "description": "事件订阅的 Verification Token（仅 Webhook 备用模式需要，可选）"},
                    "receive_mode": {"type": "string", "description": "接收模式，桌面版默认 websocket；可选 websocket 或 webhook"},
                    "domain": {"type": "string", "description": "平台域名类型，飞书填 feishu，国际版 Lark 填 lark"},
                    "confirmed": {"type": "boolean", "description": "必须传 true 才会保存配置"},
                },
                "required": ["app_id", "app_secret", "confirmed"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "configure_discord",
            "description": "在对话中配置 Discord Bot。用户说【连接/配置/绑定 Discord】时调用。先去 https://discord.com/developers/applications 新建 application → Bot → Reset Token，并务必勾选 MESSAGE CONTENT INTENT。然后把 token 给到这个工具，会自动验证、写入 .env、起 bot 线程，并返回邀请链接让用户把 bot 加进自己的服务器或 DM。",
            "parameters": {
                "type": "object",
                "properties": {
                    "bot_token": {"type": "string", "description": "Discord Bot Token（从 Developer Portal 复制）"},
                    "allowed_user_ids": {"type": "string", "description": "可选，逗号分隔的 Discord 用户 ID 白名单；留空表示允许任何 DM 这个 bot 的用户使用"},
                    "confirmed": {"type": "boolean", "description": "必须传 true 才会保存配置"},
                },
                "required": ["bot_token", "confirmed"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_open",
            "description": "在内置后台无头浏览器中打开 URL，会完整执行 JavaScript。仅适合 fetch_webpage 无法读取、登录、SPA、需要点击/输入的动态页面；普通搜索和静态网页优先用 search_web/fetch_webpage。打开后页面会保持在内存，可继续 browser_read/click/type/screenshot。首次使用会下载 ~150MB 的 Chromium（一次性）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "完整 URL，含 http:// 或 https://"},
                    "wait_for": {"type": "string", "description": "等待事件：domcontentloaded (默认，更快) / load / networkidle。除非必须等待长连接完成，否则不要用 networkidle。"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_read",
            "description": "读取当前浏览器页面的可见文本。selector 为空时返回整页 innerText；指定 CSS selector 时只返回该元素的文本。需要先调 browser_open。",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "可选 CSS 选择器，例如 '.price' 或 '#main'"},
                    "max_chars": {"type": "integer", "description": "返回文本上限，默认 8000，最大 30000"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "点击当前页面上匹配 CSS selector 的第一个元素。常用于'下一页'按钮、登录提交、菜单展开。需要先调 browser_open。",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS 选择器，例如 'button[type=submit]'"},
                    "timeout_ms": {"type": "integer", "description": "等待元素出现的毫秒数，默认 5000"},
                },
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_type",
            "description": "在 input/textarea 等输入框里填入文本。submit=true 时填完后按 Enter 提交。需要先调 browser_open。",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS 选择器定位输入框"},
                    "text": {"type": "string", "description": "要输入的文本"},
                    "submit": {"type": "boolean", "description": "是否按 Enter 提交，默认 false"},
                },
                "required": ["selector", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": "把当前浏览器页面截图保存为 PNG 到 outputs/。返回保存路径。需要先调 browser_open。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "可选文件名（不含路径），默认 screenshot.png"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_close",
            "description": "关闭当前浏览器会话，释放 Chromium 占用的内存。完成网页任务后建议调一次。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_wait",
            "description": "等待页面上某个元素出现或消失，再继续后续操作。处理 SPA / 登录回跳 / AJAX 渲染必备。state: visible (默认，可见且可点击) / attached (DOM 里存在但可能隐藏) / hidden / detached。",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS 选择器"},
                    "timeout_ms": {"type": "integer", "description": "最长等待毫秒，默认 10000"},
                    "state": {"type": "string", "description": "visible / attached / hidden / detached，默认 visible"},
                },
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_back",
            "description": "浏览器后退一页（等同于点浏览器后退按钮）。需要先调 browser_open。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_forward",
            "description": "浏览器前进一页。需要先调 browser_open。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_scroll",
            "description": "滚动当前页面。direction: down (默认) / up / top / bottom。长页面读完整内容前必须滚动。",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "description": "down / up / top / bottom"},
                    "pixels": {"type": "integer", "description": "滚动像素数，仅 up/down 生效，默认 600"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_evaluate",
            "description": "在当前页面跑一段 JavaScript 并返回结果。用于精准提取 (querySelectorAll 取值)、获取页面元数据 (document.title) 或读取 localStorage 等。脚本必须是表达式或箭头函数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {"type": "string", "description": "JS 表达式或箭头函数，如 '() => document.title'"},
                },
                "required": ["script"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "根据文字描述生成图片（OpenAI gpt-image-1 模型），自动保存为 PNG 到 outputs/ 目录。用户说'画一张/生成图片/给我做个图'时调用。需要在设置里配置 OPENAI_API_KEY。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "详细描述要生成什么样的图。越具体效果越好（构图、风格、颜色、光线等）。"},
                    "size": {"type": "string", "description": "尺寸：1024x1024 (默认正方形) / 1024x1536 (竖版) / 1536x1024 (横版) / auto"},
                    "quality": {"type": "string", "description": "质量：low / medium / high / auto (默认 auto)。high 慢且贵。"},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "text_to_speech",
            "description": "把文本转成语音音频（OpenAI tts-1），保存为 mp3 到 outputs/。用户说'读出来/朗读/语音播报/转成音频'时调用。单次最多 4000 字，长文要拆开多次调。需要在设置里配置 OPENAI_API_KEY。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要朗读的文字内容"},
                    "voice": {"type": "string", "description": "音色：alloy (默认中性) / echo (男低) / fable (英国男) / onyx (深沉) / nova (女) / shimmer (柔和女)"},
                    "fmt": {"type": "string", "description": "输出格式：mp3 (默认) / opus / aac / flac / wav"},
                    "speed": {"type": "number", "description": "语速倍率，0.25–4.0，默认 1.0"},
                },
                "required": ["text"],
            },
        },
    },
]

TOOL_SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "run_parallel_subagents",
        "description": "并行运行最多 3 个独立研究子任务，并把结果汇总回来。适合比较多个公司/商品/保险/来源；子任务必须相互独立。不要用于简单问题。",
        "parameters": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "1-3 个相互独立的子任务，每个子任务应具体、可单独完成。",
                },
                "expected_output": {
                    "type": "string",
                    "description": "希望每个子任务返回的格式，例如“价格、来源、优点、风险”。",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "总超时秒数，默认 60，最大 90。",
                },
            },
            "required": ["tasks"],
        },
    },
})

# 从 schema 提取 required 参数，用于前置校验
_TOOL_REQUIRED_PARAMS: dict[str, set[str]] = {}
for _sch in TOOL_SCHEMAS:
    _func = _sch.get("function", {})
    _params = _func.get("parameters", {})
    _TOOL_REQUIRED_PARAMS[_func["name"]] = set(_params.get("required", []))


_BASE_TOOL_NAMES = {
    "read_file",
    "write_file",
    "analyze_image",
    "summarize_text",
    "make_markdown_report",
    "make_spreadsheet",
    "make_webpage",
    "make_react_prototype",
    "list_outputs",
    "recall",
    "list_memories",
}
_RESEARCH_TOOL_NAMES = {
    "search_web",
    "fetch_webpage",
    "fetch_element",
    "track_logistics",
    "browser_open",
    "browser_read",
    "browser_click",
    "browser_type",
    "browser_screenshot",
    "browser_close",
    "browser_wait",
    "browser_back",
    "browser_forward",
    "browser_scroll",
    "browser_evaluate",
}
_TERMINAL_TOOL_NAMES = {
    "run_terminal_command",
    "terminal_session_start",
    "terminal_session_list",
    "terminal_session_tail",
    "terminal_session_send",
    "terminal_session_stop",
}
_EMAIL_TOOL_NAMES = {
    "summarize_email_text",
    "extract_email_tasks",
    "draft_email_reply",
    "list_inbox",
    "search_emails",
    "get_email_thread",
    "configure_email_account",
    "list_email_accounts_tool",
    "send_email_tool",
    "delete_email_tool",
    "archive_email_tool",
}
_MEMORY_TOOL_NAMES = {"extract_memory_candidates", "remember", "confirm_memory", "forget_memory"}
_SCHEDULE_TOOL_NAMES = {"create_cron_job", "list_cron_jobs", "delete_cron_job", "toggle_cron_job"}
_MEDIA_TOOL_NAMES = {"generate_image", "text_to_speech"}
_SUBAGENT_TOOL_NAMES = {"run_parallel_subagents"}
_INTEGRATION_TOOL_NAMES = {
    "configure_iot_gateway",
    "call_iot_gateway",
    "get_integration_status",
    "configure_telegram",
    "configure_feishu",
    "configure_discord",
}
_SCHEMA_BY_NAME = {_sch["function"]["name"]: _sch for _sch in TOOL_SCHEMAS}


def tool_schemas_for(user_text: str, extra_system_context: str | None = None) -> list[dict]:
    """Return a smaller tool set for the current task.

    Exposing every tool on every turn makes function selection noisier as the
    product grows. This router keeps common safe tools available and adds
    domain-specific tools from lightweight intent signals and Task Mode plan
    context.
    """
    text = f"{user_text or ''}\n{extra_system_context or ''}".lower()
    names = set(_BASE_TOOL_NAMES)

    if _has_any(text, ("search", "browse", "web", "website", "url", "price", "quote", "insurance", "travel", "news", "latest", "stock", "source", "research", "compare", "购买", "买", "报价", "保险", "旅行", "新闻", "最新", "库存", "来源", "搜索", "网页", "对比", "research_comparison")):
        names |= _RESEARCH_TOOL_NAMES
        if "[subagent]" not in text and _has_any(text, ("compare", "research", "multiple", "several", "各", "多个", "几家", "比较", "对比", "分别", "research_comparison")):
            names |= _SUBAGENT_TOOL_NAMES
    if _has_any(text, ("terminal", "command", "shell", "run ", "execute", "test", "build", "server", "python", "npm", "git", "代码", "命令", "终端", "运行", "执行", "测试", "构建", "启动", "technical")):
        names |= _TERMINAL_TOOL_NAMES
    if _has_any(text, ("email", "mail", "inbox", "imap", "smtp", "reply", "邮件", "邮箱", "收件箱", "回复邮件")):
        names |= _EMAIL_TOOL_NAMES
    if _has_any(text, ("remember", "memory", "forget", "记住", "记忆", "忘记")):
        names |= _MEMORY_TOOL_NAMES
    if _has_any(text, ("cron", "schedule", "every day", "weekly", "remind", "定时", "计划任务", "提醒", "每天", "每周")):
        names |= _SCHEDULE_TOOL_NAMES
    if _has_any(text, ("image", "picture", "draw", "generate image", "tts", "speech", "audio", "voice", "图片", "画", "生成图", "语音", "朗读", "音频", "creation")):
        names |= _MEDIA_TOOL_NAMES
    if _has_any(text, ("telegram", "feishu", "lark", "discord", "iot", "home assistant", "飞书", "机器人", "集成", "智能家居")):
        names |= _INTEGRATION_TOOL_NAMES

    ordered = [_SCHEMA_BY_NAME[name] for name in _schema_order() if name in names and name in _SCHEMA_BY_NAME]
    return ordered or TOOL_SCHEMAS


def _schema_order() -> list[str]:
    return [_sch["function"]["name"] for _sch in TOOL_SCHEMAS]


def _has_any(text: str, signals: tuple[str, ...]) -> bool:
    return any(signal in text for signal in signals)


_DISPATCH = {
    "read_file": read_file,
    "write_file": write_file,
    "analyze_image": analyze_image,
    "run_terminal_command": run_terminal_command,
    "terminal_session_start": terminal_session_start,
    "terminal_session_list": terminal_session_list,
    "terminal_session_tail": terminal_session_tail,
    "terminal_session_send": terminal_session_send,
    "terminal_session_stop": terminal_session_stop,
    "summarize_text": summarize_text,
    "summarize_email_text": summarize_email_text,
    "extract_email_tasks": extract_email_tasks,
    "draft_email_reply": draft_email_reply,
    "list_inbox": list_inbox,
    "search_emails": search_emails,
    "get_email_thread": get_email_thread,
    "configure_email_account": configure_email_account,
    "list_email_accounts_tool": list_email_accounts_tool,
    "send_email_tool": send_email_tool,
    "delete_email_tool": delete_email_tool,
    "archive_email_tool": archive_email_tool,
    "make_markdown_report": make_markdown_report,
    "make_spreadsheet": make_spreadsheet,
    "make_webpage": make_webpage,
    "make_react_prototype": make_react_prototype,
    "extract_memory_candidates": extract_memory_candidates,
    "remember": remember,
    "recall": recall,
    "list_outputs": list_outputs,
    "fetch_webpage": fetch_webpage,
    "fetch_element": fetch_element,
    "track_logistics": track_logistics,
    "search_web": search_web,
    "list_memories": list_memories,
    "confirm_memory": confirm_memory,
    "forget_memory": forget_memory,
    "create_cron_job": create_cron_job,
    "list_cron_jobs": list_cron_jobs,
    "delete_cron_job": delete_cron_job,
    "toggle_cron_job": toggle_cron_job,
    "configure_iot_gateway": configure_iot_gateway,
    "call_iot_gateway": call_iot_gateway,
    "get_integration_status": get_integration_status,
    "configure_telegram": configure_telegram,
    "configure_feishu": configure_feishu,
    "configure_discord": configure_discord,
    "browser_open": lambda url, wait_for="load": _browser_call("browser_open", url=url, wait_for=wait_for),
    "browser_read": lambda selector="", max_chars=8000: _browser_call("browser_read", selector=selector, max_chars=max_chars),
    "browser_click": lambda selector, timeout_ms=5000: _browser_call("browser_click", selector=selector, timeout_ms=timeout_ms),
    "browser_type": lambda selector, text, submit=False: _browser_call("browser_type", selector=selector, text=text, submit=submit),
    "browser_screenshot": lambda filename="": _browser_call("browser_screenshot", filename=filename),
    "browser_close": lambda: _browser_call("browser_close"),
    "browser_wait": lambda selector, timeout_ms=10000, state="visible": _browser_call("browser_wait", selector=selector, timeout_ms=timeout_ms, state=state),
    "browser_back": lambda: _browser_call("browser_back"),
    "browser_forward": lambda: _browser_call("browser_forward"),
    "browser_scroll": lambda direction="down", pixels=600: _browser_call("browser_scroll", direction=direction, pixels=pixels),
    "browser_evaluate": lambda script: _browser_call("browser_evaluate", script=script),
    "generate_image": lambda prompt, size="1024x1024", quality="auto": _image_call("generate_image", prompt=prompt, size=size, quality=quality),
    "text_to_speech": lambda text, voice="", fmt="mp3", speed=1.0: _tts_call("text_to_speech", text=text, voice=voice, fmt=fmt, speed=speed),
    "run_parallel_subagents": lambda tasks, expected_output="", timeout_seconds=60: _subagent_call(tasks=tasks, expected_output=expected_output, timeout_seconds=timeout_seconds),
}


def _image_call(fn_name: str, **kwargs) -> dict:
    """Lazy bridge to tools_image (defers heavy import until first call)."""
    try:
        from . import tools_image
    except Exception as exc:
        return {"error": f"image module unavailable: {type(exc).__name__}: {exc}"}
    fn = getattr(tools_image, fn_name, None)
    if fn is None:
        return {"error": f"unknown image tool: {fn_name}"}
    return fn(**kwargs)


def _tts_call(fn_name: str, **kwargs) -> dict:
    """Lazy bridge to tools_tts."""
    try:
        from . import tools_tts
    except Exception as exc:
        return {"error": f"tts module unavailable: {type(exc).__name__}: {exc}"}
    fn = getattr(tools_tts, fn_name, None)
    if fn is None:
        return {"error": f"unknown tts tool: {fn_name}"}
    return fn(**kwargs)


def _subagent_call(**kwargs) -> dict:
    """Lazy bridge to subagent runner."""
    try:
        from . import subagent
    except Exception as exc:
        return {"error": f"subagent module unavailable: {type(exc).__name__}: {exc}"}
    return subagent.run_parallel_subagents(**kwargs)


def _browser_call(fn_name: str, **kwargs) -> dict:
    """Lazy bridge to tools_browser — avoids importing Playwright until first browser tool is used."""
    try:
        from . import tools_browser
    except Exception as exc:
        return {"error": f"browser module unavailable: {type(exc).__name__}: {exc}"}
    fn = getattr(tools_browser, fn_name, None)
    if fn is None:
        return {"error": f"unknown browser tool: {fn_name}"}
    return fn(**kwargs)


def run_tool(
    name: str,
    arguments: dict | str,
    *,
    task_id: str = "",
    user_input: str = "",
    model: str = "",
    token_usage: Optional[dict] = None,
) -> Any:
    """根据工具名分发执行，自动记录日志。出错也返回 dict。

    新增安全控制：
    - 前置参数校验（按 schema required）
    - 高风险工具默认阻断，需传 confirmed=True
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments else {}
        except json.JSONDecodeError:
            arguments = {}
    if not isinstance(arguments, dict):
        arguments = {}

    fn = _DISPATCH.get(name)
    if fn is None:
        err = f"unknown tool: {name}"
        _write_log(name, arguments, {"error": err}, 0,
                   task_id=task_id, user_input=user_input, model=model,
                   token_usage=token_usage, status="error", error=err)
        return {"error": err}

    # 参数校验
    validation_err = _validate_tool_args(name, arguments)
    if validation_err:
        result = _attach_retry_hint({"error": validation_err}, name, validation_err, user_input)
        _write_log(name, arguments, result, 0,
                   task_id=task_id, user_input=user_input, model=model,
                   token_usage=token_usage, status="error", error=validation_err)
        return result

    # 高风险工具二次确认
    risk = _RISK.get(name, "unknown")
    confirmed = bool(arguments.pop("confirmed", False))
    implicit_safe_file_move = _can_accept_implicit_soft_trash_confirmation(name, arguments, user_input)
    if risk == "high" and not confirmed and not implicit_safe_file_move:
        err = f"高风险工具 '{name}' 需要用户确认。如需执行，请在参数中加入 confirmed: true。"
        _write_log(name, arguments, {"error": err}, 0,
                   task_id=task_id, user_input=user_input, model=model,
                   token_usage=token_usage, status="blocked", error=err)
        return {"error": err}

    # 终端删除安全：默认只允许"移到废纸篓/回收站"，禁止直接 rm（除非用户明确要求永久删除）
    if name == "run_terminal_command":
        cmd = str(arguments.get("command") or "").strip().lower()
        user_text = (user_input or "").lower()
        wants_permanent = any(k in user_text for k in ("永久删除", "彻底删除", "不可恢复", "permanently", "permanent delete"))
        uses_rm = bool(re.search(r"(^|\\s)rm(\\s|$)", cmd))
        if uses_rm and not wants_permanent:
            err = "为防止误删：默认不允许使用 rm 永久删除。请改为“移到废纸篓/回收站（可恢复）”，或在当前消息里明确说明“永久/彻底删除”后再执行。"
            _write_log(name, arguments, {"error": err}, 0,
                       task_id=task_id, user_input=user_input, model=model,
                       token_usage=token_usage, status="blocked", error=err)
            return {"error": err}

    t0 = time.time()
    try:
        with accounting.usage_context(tool_name=name):
            result = fn(**arguments)
        if isinstance(result, dict) and result.get("error"):
            status = "error"
            error = str(result["error"])
        else:
            status = "success"
            error = ""
    except TypeError as e:
        result = {"error": f"bad arguments: {e}"}
        status = "error"
        error = str(e)
    except Exception as e:
        result = {"error": f"{type(e).__name__}: {e}"}
        status = "error"
        error = str(e)

    if status == "error":
        result = _attach_retry_hint(result, name, error, user_input)

    _write_log(name, arguments, result, (time.time() - t0) * 1000,
               task_id=task_id, user_input=user_input, model=model,
               token_usage=token_usage, status=status, error=error)
    return result


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:60] or "untitled"
