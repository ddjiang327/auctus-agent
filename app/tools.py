"""Agent 可调用的工具。

设计要点：
- 每个工具是一个普通 Python 函数。
- 用 TOOL_SCHEMAS 暴露 OpenAI function-calling 格式给 LLM。
- run_tool() 根据 name 分发，并自动写 logs/tool_calls.jsonl。
"""
from __future__ import annotations

import json
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
from . import evolution
from . import memory
from . import llm


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
    # 文件级操作也算“明确授权”（用于 rm/mv 等）
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
        scope = accounting.get_setup_state().get("permission_scope", "workspace")
    except Exception:
        scope = "workspace"
    return "full_computer" if scope == "full_computer" else "workspace"


def _terminal_access() -> str:
    override = _TERMINAL_ACCESS_OVERRIDE.get()
    if override:
        return "enabled" if override == "enabled" else "disabled"
    try:
        access = accounting.get_setup_state().get("terminal_access", "disabled")
    except Exception:
        access = "disabled"
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


# ---- 记忆 ----

_MEMORY_CANDIDATE_PROMPT = """请从以下文本中提取值得长期记住的信息（用户偏好、项目背景、工作规则、重要决定）。
不要提取临时信息或已知常识。

文本：
{text}

以 JSON 数组返回，每项格式：
{{"key": "短标题", "value": "事实内容", "type": "preference|project|rule|temporary", "importance": 1-5}}

只输出 JSON，不要其他内容。"""


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
        memory_type = _normalize_memory_type(c.get("type", "project"))
        importance = _normalize_importance(c.get("importance", 3))
        fact_id = memory.store_candidate(
            key=c.get("key", "untitled"),
            value=c.get("value", ""),
            tags=[],
            type=memory_type,
            importance=importance,
        )
        stored.append({
            "id": fact_id,
            "key": c.get("key"),
            "value": c.get("value"),
            "type": memory_type,
            "importance": importance,
        })
    return {
        "candidates": stored,
        "count": len(stored),
        "note": "候选记忆已暂存，请用 confirm_memory 确认或 forget_memory 删除",
    }


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
    with urlopen(request, timeout=15) as response:
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
    """Search the public web and return result titles/snippets/URLs."""
    query = (query or "").strip()
    if not query:
        return {"error": "empty search query"}
    max_results = min(max(int(max_results or 5), 1), 10)
    search_url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    request = Request(
        search_url,
        headers={
            "User-Agent": "AuctusAgent/0.1 (+local personal assistant)",
            "Accept": "text/html",
        },
    )
    with urlopen(request, timeout=15) as response:
        raw = response.read(1_000_000)
        content_type = response.headers.get("content-type", "")
    html = raw.decode(_charset_from_content_type(content_type), errors="replace")
    results = _parse_duckduckgo_results(html, max_results)
    return {"query": query, "results": results, "count": len(results)}


def _parse_duckduckgo_results(html: str, max_results: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        re.I | re.S,
    )
    for match in pattern.finditer(html):
        href = unescape(match.group("href"))
        title = _html_to_text(match.group("title"))
        url = _clean_duckduckgo_url(href)
        if not title or not url:
            continue
        snippet = ""
        tail = html[match.end():match.end() + 1500]
        snippet_match = re.search(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', tail, re.I | re.S)
        if snippet_match:
            snippet = _html_to_text(snippet_match.group(1))
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results


def _clean_duckduckgo_url(href: str) -> str:
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target)
    return href


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


# ---------- 工具安全与日志 ----------

_RISK = {
    "read_file": "medium",
    "write_file": "medium",
    "run_terminal_command": "high",
    "summarize_text": "low",
    "summarize_email_text": "low",
    "extract_email_tasks": "low",
    "draft_email_reply": "low",
    "list_inbox": "low",
    "search_emails": "low",
    "get_email_thread": "low",
    "make_markdown_report": "low",
    "make_spreadsheet": "low",
    "make_webpage": "medium",
    "make_react_prototype": "medium",
    "extract_memory_candidates": "medium",
    "remember": "medium",
    "recall": "low",
    "list_outputs": "low",
    "fetch_webpage": "low",
    "search_web": "low",
    "list_memories": "low",
    "confirm_memory": "medium",
    "forget_memory": "high",
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
    if name == "run_terminal_command":
        return any(k.lower() in text for k in _TERMINAL_KEYWORDS)
    return False


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
            "name": "run_terminal_command",
            "description": "执行本机终端命令。只有 Settings 已启用终端权限、且用户当前请求明确要求执行终端/命令时才能使用。默认工作目录为授权 workspace；若文件权限为整台电脑，可指定其他工作目录。会拦截明显危险命令。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的 shell 命令"},
                    "working_directory": {"type": "string", "description": "命令执行目录，可选"},
                    "timeout_seconds": {"type": "integer", "description": "超时时间，默认 30 秒，最大 120 秒"},
                    "confirmed": {"type": "boolean", "description": "必须为 true，表示用户当前请求明确要求执行该命令"},
                },
                "required": ["command"],
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
]

# 从 schema 提取 required 参数，用于前置校验
_TOOL_REQUIRED_PARAMS: dict[str, set[str]] = {}
for _sch in TOOL_SCHEMAS:
    _func = _sch.get("function", {})
    _params = _func.get("parameters", {})
    _TOOL_REQUIRED_PARAMS[_func["name"]] = set(_params.get("required", []))


_DISPATCH = {
    "read_file": read_file,
    "write_file": write_file,
    "run_terminal_command": run_terminal_command,
    "summarize_text": summarize_text,
    "summarize_email_text": summarize_email_text,
    "extract_email_tasks": extract_email_tasks,
    "draft_email_reply": draft_email_reply,
    "list_inbox": list_inbox,
    "search_emails": search_emails,
    "get_email_thread": get_email_thread,
    "make_markdown_report": make_markdown_report,
    "make_spreadsheet": make_spreadsheet,
    "make_webpage": make_webpage,
    "make_react_prototype": make_react_prototype,
    "extract_memory_candidates": extract_memory_candidates,
    "remember": remember,
    "recall": recall,
    "list_outputs": list_outputs,
    "fetch_webpage": fetch_webpage,
    "search_web": search_web,
    "list_memories": list_memories,
    "confirm_memory": confirm_memory,
    "forget_memory": forget_memory,
}


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
    if risk == "high" and not arguments.pop("confirmed", False):
        err = f"高风险工具 '{name}' 需要用户确认。如需执行，请在参数中加入 confirmed: true。"
        _write_log(name, arguments, {"error": err}, 0,
                   task_id=task_id, user_input=user_input, model=model,
                   token_usage=token_usage, status="blocked", error=err)
        return {"error": err}
    if risk == "high" and not _has_explicit_user_approval(name, user_input):
        err = f"高风险工具 '{name}' 需要用户在当前请求中明确授权。"
        _write_log(name, arguments, {"error": err}, 0,
                   task_id=task_id, user_input=user_input, model=model,
                   token_usage=token_usage, status="blocked", error=err)
        return {"error": err}

    # 终端删除安全：默认只允许“移到废纸篓/回收站”，禁止直接 rm（除非用户明确要求永久删除）
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
