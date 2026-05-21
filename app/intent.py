"""Fast local intent decisions for prompt/context/tool routing.

This module is intentionally rule-only. It must not call an LLM, embeddings,
network, or disk-heavy APIs because it runs on the foreground chat path.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable


_RESEARCH = (
    "search", "browse", "web", "website", "url", "latest", "news", "price",
    "quote", "compare", "research", "source", "stock",
    "搜索", "网页", "浏览", "最新", "新闻", "价格", "报价", "对比", "比较", "来源", "库存",
)
_TECHNICAL = (
    "code", "script", "function", "class", "debug", "bug", "test", "build",
    "server", "python", "npm", "git", "api", "repo",
    "代码", "脚本", "函数", "报错", "错误", "测试", "构建", "启动", "仓库", "接口",
)
_RAG = (
    "rag", "knowledge", "knowledge base", "document", "docs", "note", "notes",
    "file", "folder", "workspace", "context", "project context",
    "知识库", "文档", "资料", "笔记", "文件", "文件夹", "目录", "上下文", "项目资料", "本地资料",
)
_AUTOMATION = (
    "cron", "schedule", "every day", "weekly", "remind", "watch", "monitor",
    "trigger", "url_watch", "email_match",
    "定时", "计划任务", "提醒", "每天", "每周", "监听", "监控", "触发器", "事件触发",
)
_MEMORY = (
    "remember", "memory", "forget", "who am i", "what do you know about me",
    "记住", "记忆", "忘记", "我是谁", "我叫什么", "关于我",
)
_CALENDAR = (
    "calendar", "event", "meeting", "appointment",
    "日历", "日程", "会议", "预约", "安排",
)
_EMAIL = ("email", "mail", "inbox", "imap", "smtp", "邮件", "邮箱", "收件箱")
_GITHUB = ("github", "issue", "pull request", " pr ", " gh ", "repo", "repository")
_SMALLTALK = (
    "hello", "hi", "hey", "thanks", "thank you", "ok", "okay",
    "你好", "谢谢", "好的", "收到", "明白", "了解",
)
_DEEP = (
    "architecture", "system design", "root cause", "tradeoff", "trade-off",
    "threat model", "security audit", "migration", "refactor",
    "架构", "系统设计", "根本原因", "权衡", "安全审计", "迁移", "重构", "深入分析", "全面分析",
)


def classify(
    user_text: str,
    *,
    extra_system_context: str | None = None,
    explicit_paths: Iterable[Path] | None = None,
) -> dict[str, Any]:
    """Return a small local decision object for the current turn."""
    raw = user_text or ""
    text = f"{raw}\n{extra_system_context or ''}".lower()
    explicit_paths = list(explicit_paths or [])

    domains: set[str] = set()
    if _has_any(text, _RESEARCH):
        domains.add("research")
    if _has_any(text, _TECHNICAL):
        domains.add("technical")
    if _has_any(text, _RAG) or explicit_paths:
        domains.add("local_knowledge")
    if _has_any(text, _AUTOMATION):
        domains.add("automation")
    if _has_any(text, _MEMORY):
        domains.add("memory")
    if _has_any(text, _CALENDAR):
        domains.add("calendar")
    if _has_any(text, _EMAIL):
        domains.add("email")
    if _has_any(text, _GITHUB):
        domains.add("github")

    if not domains and len(raw.strip()) <= 80 and _has_any(text, _SMALLTALK):
        intent = "smalltalk"
    elif "automation" in domains:
        intent = "automation"
    elif {"research", "local_knowledge"} & domains:
        intent = "information_task"
    elif "technical" in domains:
        intent = "technical_task"
    elif domains:
        intent = "action_task"
    else:
        intent = "general"

    model_tier = "standard"
    if intent == "smalltalk" or (len(raw.strip()) <= 50 and not domains):
        model_tier = "fast"
    if _has_any(text, _DEEP):
        model_tier = "deep"

    return {
        "intent": intent,
        "domains": sorted(domains),
        "model_tier": model_tier,
        "use_rag": "local_knowledge" in domains,
        "use_recent_sessions": intent not in {"smalltalk"} and bool(domains),
        "use_tools": intent not in {"smalltalk"},
        "risk_level": _risk_level(domains),
        "should_suggest_automation": _should_suggest_automation(text, domains),
    }


def prompt_context(decision: dict[str, Any]) -> str:
    """Compact system hint for behavior, not a verbose plan."""
    intent = decision.get("intent", "general")
    domains = ", ".join(decision.get("domains") or []) or "none"
    risk = decision.get("risk_level", "low")
    lines = [
        "[快速本地意图判断]",
        f"- intent: {intent}",
        f"- domains: {domains}",
        f"- risk: {risk}",
    ]
    if intent == "smalltalk":
        lines.append("- guidance: 直接简短回答，不要主动使用工具或展开任务清单。")
    elif risk == "high":
        lines.append("- guidance: 可先做只读/可逆步骤；写入、发送、删除、执行命令前明确确认。")
    else:
        lines.append("- guidance: 信息足够时直接推进；只问最关键的阻塞问题。")
    return "\n".join(lines)


def review_summary(session_id: str, user_text: str, reply: str, files: list[str] | None = None) -> dict[str, Any]:
    """Cheap post-turn review for logs/UI experiments. No LLM calls."""
    decision = classify(user_text)
    reply_text = reply or ""
    status = "completed"
    if any(token in reply_text.lower() for token in ("error", "failed", "无法", "失败", "需要你", "请确认")):
        status = "waiting_or_blocked"
    suggestions: list[str] = []
    if decision.get("should_suggest_automation"):
        suggestions.append("automation_candidate")
    if "local_knowledge" in decision.get("domains", []) and files:
        suggestions.append("index_outputs_candidate")
    return {
        "session_id": session_id,
        "intent": decision["intent"],
        "domains": decision["domains"],
        "status": status,
        "suggestions": suggestions,
        "files": files or [],
    }


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _risk_level(domains: set[str]) -> str:
    if domains & {"technical", "email", "calendar", "automation"}:
        return "medium"
    return "low"


def _should_suggest_automation(text: str, domains: set[str]) -> bool:
    if "automation" in domains:
        return True
    return bool(re.search(r"(每天|每周|定期|一旦| whenever | every | when .* changes)", text))
