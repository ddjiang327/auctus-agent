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

from .config import settings
from . import accounting, evolution, llm, memory, tools

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

def _build_messages(session_id: str, user_text: str) -> list[dict]:
    """组装一次完整调用所需的 messages：system + 摘要 + 历史 + 当前。"""
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
    memory_context = _memory_context(user_text)
    if memory_context:
        msgs.append({"role": "system", "content": memory_context})
    # 历史会话摘要由 memory.summarize_and_truncate 负责插入（按 msg_id 分段累计，且避免重复压缩）
    evolution_context = evolution.build_runtime_context(user_text)
    if evolution_context:
        msgs.append({"role": "system", "content": evolution_context})
    msgs.extend(history)
    msgs.append({"role": "user", "content": user_text})
    return msgs


def chat(session_id: str, user_text: str) -> dict:
    """处理一轮用户输入。返回 {reply, files} —— files 是新生成的文件相对路径列表。"""
    files_produced: list[str] = []
    base_output_dir = settings.output_dir
    task_output_dir = base_output_dir / _safe_task_id(session_id)
    task_output_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir = task_output_dir
    explicit_paths = _extract_existing_paths(user_text)

    try:
        with tools.chat_authorized_paths(explicit_paths):
            with accounting.usage_context(
                user_id=accounting.LOCAL_USER_ID,
                session_id=session_id,
                route=accounting.current_route(),
            ):
                msgs = _build_messages(session_id, user_text)
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
                memory.append_message(session_id, {"role": "user", "content": user_text})
                return _chat_with_tools(session_id, user_text, msgs, files_produced)
    finally:
        settings.output_dir = base_output_dir


def _chat_with_tools(session_id: str, user_text: str, msgs: list[dict], files_produced: list[str]) -> dict:
    """Run the tool-use loop. Assumes output_dir and usage context are already set."""
    last_tool_results: list[dict] = []
    for _ in range(settings.max_tool_iterations):
        resp = llm.chat_completion(messages=msgs, tools=tools.TOOL_SCHEMAS)
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
            memory.append_message(session_id, ai_msg)
            return {"reply": ai_msg.get("content") or "", "files": files_produced}

        # 把 assistant 的 tool_call 也持久化
        memory.append_message(session_id, ai_msg)

        # 逐个执行工具，把结果作为 tool 消息塞回去
        for tc in tool_calls:
            name = tc["function"]["name"]
            args = tc["function"].get("arguments", "{}")
            result = tools.run_tool(
                name, args,
                task_id=session_id,
                user_input=user_text,
                model=model,
                token_usage=token_usage,
            )

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
                msgs.append({
                    "role": "system",
                    "content": (
                        "[工具执行失败]\n"
                        f"工具 {name} 没有完成任务，错误是：{result.get('error')}\n"
                        "最终回复必须明确说明未执行成功，不要声称已经完成。"
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
                    ),
                },
                {"role": "user", "content": f"用户问题：{user_text}\n\n已有工具结果：\n{compact}"},
            ],
            temperature=0.2,
        )
        return resp["choices"][0]["message"].get("content") or "工具调用未收敛，但已获得部分结果；请换一个更具体的问题再试。"
    except Exception:
        return "工具调用未收敛，但已获得部分结果；请换一个更具体的问题再试。"


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
        f"- 当前终端命令权限：{terminal_access}。只有 enabled 且用户明确要求执行命令时，才可以使用 run_terminal_command。\n"
        "- 当用户说“这个文件夹”“当前文件夹”“授权文件夹”“workspace”或类似表达时，"
        "默认指这个 workspace。\n"
        "- 在当前 workspace 里创建或修改文本文件时，优先使用相对路径调用 write_file，"
        "例如 `note.md` 或 `docs/note.md`，不要反问路径。\n"
        "- 如果用户给出网页 URL 或要求读取网页，使用 fetch_webpage 工具。\n"
        "- 如果用户要求查询价格、新闻或实时网页信息但没有提供 URL，先使用 search_web，再用 fetch_webpage 打开相关页面；不要连续猜测 URL。\n"
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

    Some providers reject any orphan `tool` message. This can happen when the
    rolling history window cuts off the assistant message that originally
    contained `tool_calls`, or when an older failed run left partial messages.
    """
    out: list[dict] = []
    pending_tool_call_ids: set[str] = set()
    for msg in messages:
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            tool_calls = msg.get("tool_calls") or []
            ids = {tc.get("id") for tc in tool_calls if tc.get("id")}
            if not ids:
                continue
            out.append(msg)
            pending_tool_call_ids = ids
            continue
        if role == "tool":
            tool_call_id = msg.get("tool_call_id")
            if tool_call_id and tool_call_id in pending_tool_call_ids:
                out.append(msg)
                pending_tool_call_ids.discard(tool_call_id)
            continue
        pending_tool_call_ids = set()
        out.append(msg)
    return out
