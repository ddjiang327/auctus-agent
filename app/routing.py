"""Intelligent model routing: classify task complexity → pick the right model.

Three tiers:
  fast     — simple Q&A, translation, clipboard ops  → cheapest/fastest model
  standard — coding, writing, research, most tasks   → default model
  deep     — architecture, root-cause, complex plans  → best reasoning model

Classification is pure regex, zero latency, no LLM call.
Config is stored in data/model_routing.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import settings

_CONFIG_PATH: Path = settings.data_dir / "model_routing.json"

_FAST_SIGNALS = (
    r"\b(hello|hi|hey|thanks|thank you|ok|okay|sure|yes|no)\b",
    r"(你好|谢谢|好的|嗯|对|是的|不用|没事|收到|明白|了解)",
    r"\b(translate|spell.?check|fix.?typo|grammar|indent|reformat|format this)\b",
    r"(翻译一下|帮我翻译|拼写|语法|格式化|整理格式)",
    r"\b(clipboard|paste|copy to clipboard)\b",
    r"(剪贴板|复制到|粘贴)",
    r"\bwhat (is|are|does|do) .{1,40}\?$",
    r"(是什么|是干什么的|是做什么的)\s*[？?]?\s*$",
)

_DEEP_SIGNALS = (
    r"\b(architect|system design|design pattern|scalab|distributed|microservice)\b",
    r"(架构|系统设计|设计模式|高并发|分布式|微服务)",
    r"\b(root cause|why (does|is|did|do)|diagnose|investigate deeply)\b",
    r"(根本原因|为什么会|排查原因|深入分析|仔细分析|深度分析)",
    r"\b(pros and cons|trade.?off|long.term strategy|roadmap)\b",
    r"(利弊|权衡|长期规划|战略|路线图|多方案对比)",
    r"\b(security audit|threat model|vulnerabilit|pentest)\b",
    r"(安全审计|威胁模型|漏洞分析)",
    r"\b(refactor the entire|rewrite from scratch|full migration)\b",
    r"(整体重构|从零重写|全量迁移)",
    r"\b(think (carefully|step.by.step|through)|analyze (deeply|thoroughly|in.depth))\b",
    r"(认真想|仔细考虑|逐步分析|全面分析)",
)

# Presence of these signals prevents fast-tier classification
_TOOL_SIGNALS = (
    r"\b(search|browse|web|fetch|url|email|file|run|execute|schedule|cron|remind|image|draw)\b",
    r"(搜索|浏览|网页|邮件|文件|运行|执行|定时|提醒|生成图|画图|语音)",
    # Coding / implementation tasks → standard or deep
    r"\b(code|script|function|class|implement|write a|build a|create a|python|javascript|typescript|sql|api|爬虫|程序|脚本|代码|函数|接口)\b",
    r"(帮我写|帮我做|帮我实现|帮我创建|帮我建|写一个|做一个|实现一个|创建一个)",
)


def classify(user_text: str) -> str:
    """Return 'fast', 'standard', or 'deep' based on message content."""
    text = (user_text or "").strip()
    if not text:
        return "standard"

    lower = text.lower()

    if any(re.search(pat, lower, re.IGNORECASE) for pat in _DEEP_SIGNALS):
        return "deep"

    if len(text) <= 120 and any(re.search(pat, lower, re.IGNORECASE) for pat in _FAST_SIGNALS):
        if not any(re.search(pat, lower, re.IGNORECASE) for pat in _TOOL_SIGNALS):
            return "fast"

    if len(text) <= 50 and not any(re.search(pat, lower, re.IGNORECASE) for pat in _TOOL_SIGNALS):
        return "fast"

    return "standard"


def get_config() -> dict:
    if not _CONFIG_PATH.exists():
        return {"enabled": False, "fast": "", "standard": "", "deep": ""}
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"enabled": False, "fast": "", "standard": "", "deep": ""}


def set_config(enabled: bool, fast: str = "", standard: str = "", deep: str = "") -> dict:
    cfg = {
        "enabled": enabled,
        "fast": fast.strip(),
        "standard": standard.strip(),
        "deep": deep.strip(),
    }
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def resolve_model(tier: str) -> str:
    """Return the model string for this tier, falling back to settings.model."""
    cfg = get_config()
    if not cfg.get("enabled"):
        return settings.model
    model = cfg.get(tier, "").strip()
    return model or settings.model
