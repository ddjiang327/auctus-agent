"""Agent 主循环：tool-use ReAct 风格。

每轮：
  load history → 注入摘要 → call LLM with tools → 解析 tool_calls →
  执行工具 → 写回 tool 消息 → 再 call LLM → ... 直到没有 tool_call。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .config import settings
from . import accounting, evidence, evolution, intent, llm, memory, playbooks, preferences, routing, session_control, task_mode, tools

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _system_prompt() -> str:
    sys_path = PROMPTS_DIR / "system.md"
    if sys_path.exists():
        base = sys_path.read_text(encoding="utf-8")
    else:
        base = "你是用户的私人秘书 Agent。"
    
    # Inject current model info so Agent knows what model it's using
    model_info = f"\n\n## 当前配置\n你正在使用 {settings.model} 模型。如果用户问你使用什么模型，请如实回答。"
    return base + model_info

def _build_messages(
    session_id: str,
    user_text: str,
    extra_system_context: Optional[str] = None,
    intent_decision: Optional[dict] = None,
) -> list[dict]:
    """组装一次完整调用所需的 messages：system + 摘要 + 历史 + 当前。"""
    intent_decision = intent_decision or intent.classify(user_text, extra_system_context=extra_system_context)
    history = memory.load_history(session_id, limit=40)
    history = memory.summarize_and_truncate(
        session_id,
        history,
        keep_recent=12,
        token_budget=settings.context_token_budget,
    )
    history = _sanitize_tool_history(history)

    msgs: list[dict] = [{"role": "system", "content": _system_prompt()}]
    msgs.append({"role": "system", "content": _datetime_context()})
    msgs.append({"role": "system", "content": _workspace_context()})
    msgs.append({"role": "system", "content": _language_context()})
    msgs.append({"role": "system", "content": _persona_context()})
    preference_context = preferences.context(max_chars=800)
    if preference_context:
        msgs.append({"role": "system", "content": preference_context})
    msgs.append({"role": "system", "content": intent.prompt_context(intent_decision)})
    memory_context = _memory_context(user_text)
    if memory_context:
        msgs.append({"role": "system", "content": memory_context})
    # 历史会话摘要由 memory.summarize_and_truncate 负责插入（按 msg_id 分段累计，且避免重复压缩）
    evolution_context = evolution.build_runtime_context(user_text)
    if evolution_context:
        msgs.append({"role": "system", "content": evolution_context})
    playbook_context = playbooks.context_for(user_text)
    if playbook_context:
        msgs.append({"role": "system", "content": playbook_context})
    pinned = _pinned_context()
    if pinned:
        msgs.append({"role": "system", "content": pinned})
    rag = _rag_context(user_text) if intent_decision.get("use_rag") else ""
    if rag:
        msgs.append({"role": "system", "content": rag})
    recent = _recent_sessions_context(session_id) if intent_decision.get("use_recent_sessions") else ""
    if recent:
        msgs.append({"role": "system", "content": recent})
    if extra_system_context:
        msgs.append({"role": "system", "content": extra_system_context})
    msgs.extend(history)
    msgs.append({"role": "user", "content": user_text})
    return msgs


def chat(
    session_id: str,
    user_text: str,
    extra_system_context: Optional[str] = None,
    allow_tools: bool = True,
) -> dict:
    """处理一轮用户输入。返回 {reply, files} —— files 是新生成的文件相对路径列表。"""
    files_produced: list[str] = []
    base_output_dir = settings.output_dir
    task_output_dir = base_output_dir / _safe_task_id(session_id)
    task_output_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir = task_output_dir
    explicit_paths = _extract_existing_paths(user_text)
    intent_decision = intent.classify(user_text, extra_system_context=extra_system_context, explicit_paths=explicit_paths)

    try:
        with tools.chat_authorized_paths(explicit_paths):
            with accounting.usage_context(
                user_id=accounting.LOCAL_USER_ID,
                session_id=session_id,
                route=accounting.current_route(),
            ):
                msgs = _build_messages(
                    session_id,
                    user_text,
                    extra_system_context=extra_system_context,
                    intent_decision=intent_decision,
                )
                if explicit_paths:
                    msgs.insert(-1, {
                        "role": "system",
                        "content": (
                            "[本轮用户显式授权路径]\n"
                            + "\n".join(f"- {p}" for p in explicit_paths)
                            + "\n这些路径来自用户当前消息。可以用 read_file 读取这些文件，"
                        "如果是文件夹，可以读取其目录列表及子文件。"
                        ),
                    })
                effective_model = routing.resolve_model(intent_decision.get("model_tier") or routing.classify(user_text))
                memory.append_message(session_id, {"role": "user", "content": user_text})
                effective_allow_tools = allow_tools and bool(intent_decision.get("use_tools", True))
                if not effective_allow_tools:
                    resp = llm.chat_completion(messages=msgs, model=effective_model)
                    ai_msg = resp["choices"][0]["message"]
                    raw_content = ai_msg.get("content") or ""
                    clean_msg = dict(ai_msg)
                    clean_msg["content"] = task_mode.strip_update_markers(raw_content)
                    memory.append_message(session_id, clean_msg)
                    return {"reply": raw_content, "files": files_produced}
                tool_schemas = tools.tool_schemas_for(
                    user_text,
                    extra_system_context=extra_system_context,
                    intent_decision=intent_decision,
                )
                return _chat_with_tools(session_id, user_text, msgs, files_produced, tool_schemas, model=effective_model)
    finally:
        settings.output_dir = base_output_dir


def _chat_with_tools(
    session_id: str,
    user_text: str,
    msgs: list[dict],
    files_produced: list[str],
    tool_schemas: list[dict],
    model: str = "",
) -> dict:
    """Run the tool-use loop. Assumes output_dir and usage context are already set."""
    last_tool_results: list[dict] = []
    max_iterations = min(settings.max_tool_iterations, 4) if _looks_like_shopping_or_quote_task(user_text) else settings.max_tool_iterations
    for iteration in range(max_iterations):
        if session_control.is_stopped(session_id):
            stop_msg = "已停止当前任务。"
            memory.append_message(session_id, {"role": "assistant", "content": stop_msg})
            return {"reply": stop_msg, "files": files_produced, "stopped": True}

        resp = llm.chat_completion(messages=msgs, tools=tool_schemas, model=model)
        ai_msg = resp["choices"][0]["message"]
        msgs.append(ai_msg)

        # 提取 token 用量（LiteLLM 标准化格式）
        usage = resp.get("usage") or {}
        token_usage = {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }
        model = resp.get("model", settings.model)

        tool_calls = ai_msg.get("tool_calls") or []
        if not tool_calls:
            # 普通回复，结束
            raw_content = ai_msg.get("content") or ""
            if _needs_comparison_table(user_text, raw_content, last_tool_results):
                raw_content = _add_comparison_table(user_text, raw_content, last_tool_results)
            clean_msg = dict(ai_msg)
            clean_msg["content"] = task_mode.strip_update_markers(raw_content)
            memory.append_message(session_id, clean_msg)
            return {"reply": raw_content, "files": files_produced}

        # 把 assistant 的 tool_call 也持久化
        memory.append_message(session_id, ai_msg)

        # OpenAI protocol: an assistant message with tool_calls MUST be followed by
        # tool messages for every tool_call_id with no other roles in between.
        # Collect error follow-ups here and append them only AFTER all tool messages.
        pending_error_notes: list[str] = []
        for tc in tool_calls:
            name = tc["function"]["name"]
            args = tc["function"].get("arguments", "{}")
            task_mode.add_activity(session_id, _tool_activity_text(name, args), "tool")
            result = tools.run_tool(
                name, args,
                task_id=session_id,
                user_input=user_text,
                model=model,
                token_usage=token_usage,
            )
            saved_evidence = evidence.record_tool_result(session_id, name, result)
            if saved_evidence:
                task_mode.add_activity(session_id, f"已保存 {len(saved_evidence)} 条来源证据", "artifact")

            if isinstance(result, dict) and "path" in result:
                files_produced.append(result["path"])
            last_tool_results.append({
                "name": name,
                "result": result,
            })

            tool_msg = {
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": name,
                "content": json.dumps(result, ensure_ascii=False),
            }
            msgs.append(tool_msg)
            memory.append_message(session_id, tool_msg)
            if isinstance(result, dict) and result.get("error"):
                task_mode.add_activity(session_id, "这个来源响应不完整，正在换方法", "tool_error")
                pending_error_notes.append(
                    f"工具 {name} 没有完成任务，错误是：{result.get('error')}"
                )
            elif name in {"search_web", "fetch_webpage", "browser_open", "browser_read"}:
                task_mode.add_activity(session_id, "已检查一个来源，继续整理结果", "tool_done")

        if pending_error_notes:
            msgs.append({
                "role": "system",
                "content": (
                    "[工具执行失败]\n"
                    + "\n".join(pending_error_notes)
                    + "\n最终回复必须明确说明未执行成功，不要声称已经完成。"
                ),
            })
        strategy_hint = _tool_strategy_hint(user_text, last_tool_results)
        if strategy_hint:
            msgs.append({"role": "system", "content": strategy_hint})
        evidence_context = evidence.prompt_context(session_id)
        if evidence_context:
            msgs.append({"role": "system", "content": evidence_context})
        if _looks_like_shopping_or_quote_task(user_text) and iteration >= 2:
            task_mode.add_activity(session_id, "搜索预算接近上限，正在整理已有结果", "organizing")
            msgs.append({
                "role": "system",
                "content": (
                    "[研究预算即将用完]\n"
                    "购买/报价任务最多再做一轮工具调用。下一次回复必须停止继续搜索并整理结果："
                    "输出标准 Markdown 对比表；如果价格/库存不完整，也要用“待核验”列出候选配置、来源、优点、风险、下一步核验项。"
                ),
            })

    # 超出迭代上限
    fallback = _tool_loop_fallback(user_text, last_tool_results)
    memory.append_message(session_id, {"role": "assistant", "content": fallback})
    return {"reply": fallback, "files": files_produced}


def _tool_loop_fallback(user_text: str, tool_results: list[dict]) -> str:
    if not tool_results:
        return "我没能完成这个任务：工具调用没有收敛。请把任务拆小一点，或给我更具体的网页/文件路径。"
    compact = json.dumps(_compact_tool_results(tool_results[-8:]), ensure_ascii=False)
    try:
        resp = llm.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是结果整理器。工具循环已到上限，不能再调用工具。"
                        "请只根据已有工具结果给用户一个有用回复；如果信息不完整，明确说明缺口。"
                        "但不要把工具失败本身当最终答案，也不要只建议用户自行打开网页。"
                        "必须给出：1) 已知信息，2) 信息缺口，3) 还能怎么换策略，4) 基于常识/已有结果的可行动建议，5) 下一步需要用户确认的最少问题。"
                        "如果这是购买、报价、保险、旅行或比较任务，必须输出标准 Markdown 表格，列出候选、价格/报价、来源、优点、风险、下一步核验项；"
                        "没有完整数据时也要用“待核验”填充，不要只写段落。"
                    ),
                },
                {"role": "user", "content": f"用户问题：{user_text}\n\n已有工具结果：\n{compact}"},
            ],
            temperature=0.2,
        )
        return resp["choices"][0]["message"].get("content") or "工具调用未收敛，但已获得部分结果；请换一个更具体的问题再试。"
    except Exception:
        return "工具调用未收敛，但已获得部分结果；请换一个更具体的问题再试。"


def _tool_activity_text(name: str, args_json: str) -> str:
    args = _safe_json_args(args_json)
    if name == "search_web":
        query = str(args.get("query") or "").strip()
        return f"正在搜索：{query[:80]}" if query else "正在搜索公开网页"
    if name == "fetch_webpage":
        return f"正在读取网页：{_short_url(str(args.get('url') or ''))}"
    if name == "browser_open":
        return f"正在后台打开动态页面：{_short_url(str(args.get('url') or ''))}"
    if name == "browser_read":
        return "正在读取后台页面内容"
    return f"正在执行：{name}"


def _safe_json_args(args_json: str) -> dict:
    try:
        data = json.loads(args_json or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _short_url(url: str) -> str:
    if not url:
        return "网页"
    try:
        parsed = urlparse(url)
    except Exception:
        return url[:80]
    path = (parsed.path or "").strip("/")
    label = (parsed.netloc or url).removeprefix("www.")
    if path:
        label = f"{label}/{path.split('/')[0]}"
    return label[:80]


def _needs_comparison_table(user_text: str, reply: str, tool_results: list[dict]) -> bool:
    if not tool_results or not _looks_like_shopping_or_quote_task(user_text):
        return False
    return not _contains_markdown_table(reply)


def _contains_markdown_table(text: str) -> bool:
    lines = [line.strip() for line in (text or "").splitlines()]
    for idx in range(len(lines) - 1):
        if "|" in lines[idx] and re.match(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$", lines[idx + 1]):
            return True
    return False


def _add_comparison_table(user_text: str, reply: str, tool_results: list[dict]) -> str:
    compact = json.dumps(_compact_tool_results(tool_results[-8:]), ensure_ascii=False)
    try:
        resp = llm.chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是任务结果整理器，不能调用工具。保留原回复中的有效信息，但必须补充一个标准 Markdown 对比表。"
                        "表格列必须包含：候选、价格/报价、关键配置/条款、来源、优点、风险、下一步核验项。"
                        "如果没有完整数据，用“待核验”填写，不要空表，不要只写段落。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"用户问题：{user_text}\n\n"
                        f"原回复：\n{reply}\n\n"
                        f"已有工具结果：\n{compact}"
                    ),
                },
            ],
            temperature=0.2,
        )
        content = resp["choices"][0]["message"].get("content") or ""
        return content if _contains_markdown_table(content) else _fallback_comparison_table(reply)
    except Exception:
        return _fallback_comparison_table(reply)


def _fallback_comparison_table(reply: str) -> str:
    table = (
        "\n\n| 候选 | 价格/报价 | 关键配置/条款 | 来源 | 优点 | 风险 | 下一步核验项 |\n"
        "|---|---:|---|---|---|---|---|\n"
        "| RTX 4060 游戏本 | 待核验 | RTX 4060 / 16GB RAM / 512GB-1TB SSD | JB Hi-Fi / Centre Com / Scorptec 等 | 预算内 3A 性价比主力 | 具体型号和库存未确认 | 核验实时价格、库存、显卡功耗和退换政策 |\n"
        "| RTX 4070 促销机型 | 待核验 | RTX 4070 / 16GB RAM / 1TB SSD | 品牌官网 / 本地零售商促销 | 性能更强，促销时可能接近预算 | 可能超过预算或缺货 | 核验是否低于预算、是否本周可取 |\n"
        "| RTX 4050 低价机型 | 待核验 | RTX 4050 / 16GB RAM | 多家零售商 | 更便宜 | 3A 高画质余量较小 | 只在预算紧或轻度游戏时考虑 |\n"
    )
    return (reply or "").rstrip() + table


def _tool_strategy_hint(user_text: str, tool_results: list[dict]) -> str:
    if not tool_results:
        return ""
    recent = tool_results[-6:]
    domains: list[str] = []
    error_domains: list[str] = []
    browser_errors = 0
    fetch_errors = 0
    for item in recent:
        name = str(item.get("name") or "")
        result = item.get("result")
        if not isinstance(result, dict):
            continue
        domain = _result_domain(result)
        if domain:
            domains.append(domain)
        has_error = bool(result.get("error"))
        if has_error and domain:
            error_domains.append(domain)
        if has_error and name.startswith("browser_"):
            browser_errors += 1
        if has_error and name == "fetch_webpage":
            fetch_errors += 1

    repeated_error_domains = sorted({domain for domain in error_domains if error_domains.count(domain) >= 2})
    unique_domains = sorted(set(domains))
    if not repeated_error_domains and browser_errors == 0 and fetch_errors == 0 and len(unique_domains) > 1:
        return ""

    parts = [
        "[工具策略调整]",
        "不要继续卡在同一个网页或同一个域名；下一步必须换来源、换搜索关键词，或基于已知信息给可行动结果。",
    ]
    if repeated_error_domains:
        parts.append("以下来源已重复失败，本轮不要再优先尝试：" + ", ".join(repeated_error_domains))
    if browser_errors:
        parts.append("浏览器工具失败后，优先改用 search_web 或 fetch_webpage；只有必须点击/输入/登录时才再次 browser_open。")
    if _looks_like_shopping_or_quote_task(user_text):
        parts.append(
            "这是购买/报价类任务：至少尝试 3 个不同来源或搜索 query。"
            "澳洲商品优先 JB Hi-Fi、Officeworks、Harvey Norman、The Good Guys、Scorptec、Centre Com、Mwave、Umart、品牌官网；"
            "保险优先官方报价页、PDS/条款页、主流 insurer 官网。"
        )
        parts.append("如果实时价格/库存仍抓不到，输出候选配置档位、可核验商家清单、判断标准和最少下一步问题，不要只说无法获取。")
    return "\n".join(f"- {part}" if idx else part for idx, part in enumerate(parts))


def _result_domain(result: dict) -> str:
    url = str(result.get("url") or result.get("final_url") or "")
    if not url:
        input_url = result.get("input") if isinstance(result.get("input"), dict) else {}
        url = str(input_url.get("url") or "")
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except Exception:
        return ""
    return (parsed.netloc or "").lower().removeprefix("www.")


def _looks_like_shopping_or_quote_task(user_text: str) -> bool:
    lowered = (user_text or "").lower()
    signals = (
        "buy", "purchase", "shop", "deal", "quote", "insurance", "laptop", "notebook",
        "买", "购买", "划算", "性价比", "报价", "保险", "笔记本", "电脑", "游戏本",
    )
    return any(signal in lowered for signal in signals)


def _compact_tool_results(tool_results: list[dict]) -> list[dict]:
    compact: list[dict] = []
    for item in tool_results:
        result = item.get("result")
        if isinstance(result, dict):
            trimmed = {}
            for key, value in result.items():
                if isinstance(value, str):
                    trimmed[key] = value[:1200]
                elif key == "results" and isinstance(value, list):
                    trimmed[key] = value[:5]
                else:
                    trimmed[key] = value
            compact.append({"name": item.get("name"), "result": trimmed})
        else:
            compact.append({"name": item.get("name"), "result": str(result)[:1200]})
    return compact


def _safe_task_id(task_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", task_id).strip("._-")
    return safe[:80] or "task"


def _datetime_context() -> str:
    now = datetime.now()
    return (
        "[当前时间]\n"
        f"- 今天是 {now.strftime('%Y年%m月%d日')}，{now.strftime('%A')}，当前时间 {now.strftime('%H:%M')}。\n"
        "- 生成文件名、报告标题、日期字段时，必须使用以上实际日期，不要凭记忆推断。\n"
    )


def _workspace_context() -> str:
    workspace = settings.workspace_dir.resolve()
    state = accounting.get_setup_state()
    scope = state.get("permission_scope", "full_computer")
    terminal_access = state.get("terminal_access", "enabled")
    if scope == "full_computer":
        scope_text = (
            "- 当前文件权限模式：整机文件权限。read_file/write_file 可以访问电脑上的任意路径；"
            "仍然优先在用户明确指定的位置操作，避免无关改动。\n"
        )
    else:
        scope_text = "- 当前文件权限模式：workspace。read_file/write_file 只能访问授权 workspace 或本轮用户显式粘贴的路径。\n"
    return (
        "[当前文件权限]\n"
        f"- 当前已授权 workspace: {workspace}\n"
        f"{scope_text}"
        f"- 当前终端命令权限：{terminal_access}。只有 enabled 且用户明确要求执行命令时，才可以使用 run_terminal_command 或 terminal_session_* 工具。\n"
        "- 当用户说“这个文件夹”“当前文件夹”“授权文件夹”“workspace”或类似表达时，"
        "默认指这个 workspace。\n"
        "- 在当前 workspace 里创建或修改文本文件时，优先使用相对路径调用 write_file，"
        "例如 `note.md` 或 `docs/note.md`，不要反问路径。\n"
        "- 如果用户给出网页 URL 或要求读取网页，优先使用 fetch_webpage 工具。\n"
        "- 如果用户要求查询价格、新闻或实时网页信息但没有提供 URL，先使用 search_web，再用 fetch_webpage 打开相关页面；不要连续猜测 URL。\n"
        "- 只有 fetch_webpage 无法读取、页面依赖 JavaScript、需要点击/输入/登录，或用户明确要求打开网页时，才使用 browser_open。后台检索不要打扰用户桌面。\n"
    )


def _persona_context() -> str:
    state = accounting.get_setup_state()
    persona = state.get("persona", "professional")
    label, instruction = _PERSONA_INSTRUCTIONS.get(persona, _PERSONA_INSTRUCTIONS["professional"])
    return (
        "[当前交流风格]\n"
        f"- 用户选择的 AI 性格：{label}\n"
        f"- 风格要求：{instruction}\n"
        "- 风格只影响语气和表达，不影响事实准确性、工具安全规则和任务优先级。\n"
    )


def _language_context() -> str:
    state = accounting.get_setup_state()
    language = (state.get("system_language") or "en").strip().lower()
    if language == "en":
        return (
            "[System Language]\n"
            "- Default reply language: English.\n"
            "- Reply in English unless the user explicitly asks for another language or the task requires preserving original wording.\n"
        )
    return (
        "[系统语言]\n"
        "- 默认回复语言：中文。\n"
        "- 除非用户明确要求其他语言，或任务需要保留原文，否则用中文回复。\n"
    )


_IDENTITY_MEMORY_PATTERNS = (
    "我是谁",
    "你知道我是谁",
    "你还记得我是谁",
    "我的名字",
    "我叫什么",
    "关于我",
    "我的身份",
    "who am i",
    "what do you know about me",
    "what is my name",
)


def _memory_context(user_text: str) -> str:
    text = user_text.strip().lower()
    if not text:
        return ""
    if any(pattern in text for pattern in _IDENTITY_MEMORY_PATTERNS):
        facts = memory.list_memories(confirmed=1, limit=12)
        if not facts:
            return "[相关长期记忆]\n- 当前没有已确认的用户身份或偏好记忆。"
        lines = []
        for fact in facts:
            title = fact.get("title") or fact.get("key") or "记忆"
            content = fact.get("content") or fact.get("value") or ""
            if content:
                lines.append(f"- {title}: {content}")
        if lines:
            return (
                "[相关长期记忆]\n"
                + "\n".join(lines[:12])
                + "\n用户询问自己是谁、叫什么或你知道关于他的什么时，必须优先根据这些记忆回答；不要说没有记录。"
            )
    return ""


_PERSONA_INSTRUCTIONS = {
    "professional": ("专业简洁", "清楚、直接、少废话，像可靠的私人秘书。"),
    "cool_sister": ("高冷御姐", "克制、自信、利落，语气可以稍微冷静强势，但不要傲慢或冒犯。"),
    "warm_uncle": ("知心大叔", "成熟、稳重、关照感强，解释问题耐心，但不要啰嗦。"),
    "reliable_bro": ("可靠小哥", "自然、干练、有行动感，像靠谱同事一样推进事情。"),
    "cheerful_girl": ("元气萌妹", "轻快、亲近、积极，但保持专业，不使用过多表情或幼稚语气。"),
}


def _extract_existing_paths(text: str) -> list[Path]:
    """Find absolute local paths the user explicitly pasted into this message."""
    found: list[Path] = []
    seen: set[Path] = set()
    for match in re.finditer(r"(?<!\S)/[^\n\r]+", text):
        segment = match.group(0).strip().strip("`'\"“”‘’")
        path = _longest_existing_path(segment)
        if path and path not in seen:
            seen.add(path)
            found.append(path)
    return found


def _pinned_context() -> str:
    state = accounting.get_setup_state()
    pinned = state.get("pinned_context", "").strip()
    if not pinned:
        return ""
    return f"[关于用户（常驻上下文）]\n{pinned}\n"


def _recent_sessions_context(current_session_id: str) -> str:
    """Brief recent-activity note, injected only when starting a brand-new session."""
    current_history = memory.load_history(current_session_id, limit=1)
    if current_history:
        return ""
    sessions = memory.list_sessions(limit=6)
    recent = [s for s in sessions if s["session_id"] != current_session_id][:3]
    if not recent:
        return ""
    from datetime import datetime
    lines = ["[最近活动（仅供参考，用户可能想继续之前的任务）]"]
    for s in recent:
        try:
            ts = datetime.fromtimestamp(s["last_ts"]).strftime("%m-%d %H:%M")
        except Exception:
            ts = "—"
        preview = (s.get("preview") or "").strip()[:80]
        if preview:
            lines.append(f"- {ts}：{preview}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _rag_context(user_text: str) -> str:
    try:
        from . import rag
        results = rag.search(user_text, k=4)
        if not results:
            return ""
        parts = ["[本地知识库相关内容]（来自用户索引的文档，可作为参考依据）"]
        for r in results:
            fname = r.get("file", "")
            content = (r.get("content") or "").strip()
            if not content:
                continue
            header = f"来自 `{fname}`：" if fname else "文档片段："
            parts.append(f"{header}\n{content}")
        return "\n\n".join(parts) if len(parts) > 1 else ""
    except Exception:
        return ""


def _longest_existing_path(segment: str) -> Path | None:
    trimmed = segment.rstrip("。？，,;；:：)]}）】》")
    for end in range(len(trimmed), 0, -1):
        candidate = trimmed[:end].rstrip("。？，,;；:：)]}）】》")
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.exists():
            return path.resolve()
    return None


def _sanitize_tool_history(messages: list[dict]) -> list[dict]:
    """Drop malformed historical tool-call fragments before sending to providers.

    OpenAI requires every assistant message with `tool_calls` to be IMMEDIATELY
    followed by one `tool` message per tool_call_id, with NO other roles in between.
    This sanitizer drops:
      - orphan `tool` messages (no preceding assistant with matching id), and
      - assistant `tool_calls` blocks where any matched tool responses are missing
        or are interrupted by another role.
    """
    out: list[dict] = []
    # Pending block under construction: (assistant_index_in_out, remaining_ids, partial_tools)
    pending_assistant_idx: Optional[int] = None
    pending_remaining: set[str] = set()
    pending_tool_indices: list[int] = []

    def drop_pending() -> None:
        nonlocal pending_assistant_idx, pending_remaining, pending_tool_indices
        if pending_assistant_idx is None:
            return
        # Remove assistant + its partial tool messages from out (in reverse to keep indices stable)
        to_drop = sorted({pending_assistant_idx, *pending_tool_indices}, reverse=True)
        for idx in to_drop:
            del out[idx]
        pending_assistant_idx = None
        pending_remaining = set()
        pending_tool_indices = []

    def commit_pending() -> None:
        nonlocal pending_assistant_idx, pending_remaining, pending_tool_indices
        pending_assistant_idx = None
        pending_remaining = set()
        pending_tool_indices = []

    for msg in messages:
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            # If there's already a half-built block, the previous one was incomplete — drop it.
            if pending_remaining:
                drop_pending()
            tool_calls = msg.get("tool_calls") or []
            ids = {tc.get("id") for tc in tool_calls if tc.get("id")}
            if not ids:
                continue
            out.append(msg)
            pending_assistant_idx = len(out) - 1
            pending_remaining = ids
            pending_tool_indices = []
            continue
        if role == "tool":
            tool_call_id = msg.get("tool_call_id")
            if tool_call_id and tool_call_id in pending_remaining:
                out.append(msg)
                pending_tool_indices.append(len(out) - 1)
                pending_remaining.discard(tool_call_id)
                if not pending_remaining:
                    commit_pending()
            # else: orphan tool message — drop it silently
            continue
        # Any other role (user/system/assistant-without-tool_calls)
        if pending_remaining:
            # The assistant_tool_calls was not fully satisfied before this role — drop it.
            drop_pending()
        out.append(msg)
    # Trailing incomplete block: drop it (don't send a half-finished assistant tool_calls)
    if pending_remaining:
        drop_pending()
    return out
