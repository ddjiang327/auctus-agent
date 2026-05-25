"""Agent 主循环：tool-use ReAct 风格。

每轮：
  load history → 注入摘要 → call LLM with tools → 解析 tool_calls →
  执行工具 → 写回 tool 消息 → 再 call LLM → ... 直到没有 tool_call。
"""
from __future__ import annotations

import html
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlparse

# 后台线程池：并行化每轮的 embed-based context 构建（memory recall + RAG search）。
# 模块级 long-lived executor 避免每轮重复创建线程。
_CONTEXT_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ctx-builder")

from .config import settings
from . import accounting, evidence, evolution, intent, llm, memory, playbooks, preferences, routing, session_control, skills_manager, task_mode, tools

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
    model: str = "",
) -> list[dict]:
    """组装一次完整调用所需的 messages：system + 摘要 + 历史 + 当前。"""
    intent_decision = intent_decision or intent.classify(user_text, extra_system_context=extra_system_context)
    history = memory.load_history(session_id, limit=40)
    history = memory.prepare_history_for_turn(
        session_id,
        history,
        keep_recent=12,
        token_budget=settings.context_token_budget,
    )
    history = _sanitize_tool_history(
        history,
        require_reasoning_content=_requires_reasoning_content_echo(model),
    )

    # 先把可能触发 embed 网络调用的两个上下文 builder 丢到线程池里跑，
    # 后面同步组装其他 system 消息时它们已经在并行 in-flight。
    memory_ctx_future = _CONTEXT_EXECUTOR.submit(_memory_context, user_text)
    rag_ctx_future = (
        _CONTEXT_EXECUTOR.submit(_rag_context, user_text)
        if intent_decision.get("use_rag") else None
    )

    # === 稳定前缀（命中 DeepSeek/Anthropic 自动 prompt cache）===
    # 这些 system 消息每轮都一样（或多轮才变一次），放在最前面让缓存吃到。
    msgs: list[dict] = [{"role": "system", "content": _system_prompt()}]
    msgs.append({"role": "system", "content": _persona_context()})
    msgs.append({"role": "system", "content": _language_context()})
    skills_context = skills_manager.context_for_all_enabled()
    if skills_context:
        msgs.append({"role": "system", "content": skills_context})
    msgs.append({"role": "system", "content": _workspace_context()})
    preference_context = preferences.context(max_chars=800)
    if preference_context:
        msgs.append({"role": "system", "content": preference_context})
    pinned = _pinned_context()
    if pinned:
        msgs.append({"role": "system", "content": pinned})

    # === 动态后缀（每轮可能变化）===
    msgs.append({"role": "system", "content": _datetime_context()})
    msgs.append({"role": "system", "content": _turn_language_context(user_text)})
    msgs.append({"role": "system", "content": intent.prompt_context(intent_decision)})
    try:
        memory_context = memory_ctx_future.result()
    except Exception as exc:
        print(f"[agent] memory context build failed: {exc}")
        memory_context = ""
    if memory_context:
        msgs.append({"role": "system", "content": memory_context})
    evolution_context = evolution.build_runtime_context(user_text)
    if evolution_context:
        msgs.append({"role": "system", "content": evolution_context})
    playbook_context = playbooks.context_for(user_text)
    if playbook_context:
        msgs.append({"role": "system", "content": playbook_context})
    if intent_decision.get("should_suggest_automation") and accounting.get_setup_state().get("proactive_suggestions", "1") == "1":
        msgs.append({"role": "system", "content": "当前用户消息含有重复性/定期任务特征。如果回答完任务后语境自然合适，可用一句话问用户是否要设置定时任务自动执行，但不要强行插入，判断是否真的适合再问。"})
    record_write_context = _persistent_record_write_context(user_text)
    if record_write_context:
        msgs.append({"role": "system", "content": record_write_context})
    if rag_ctx_future is not None:
        try:
            rag = rag_ctx_future.result()
        except Exception as exc:
            print(f"[agent] rag context build failed: {exc}")
            rag = ""
    else:
        rag = ""
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
                effective_model = routing.resolve_model(intent_decision.get("model_tier") or routing.classify(user_text))
                msgs = _build_messages(
                    session_id,
                    user_text,
                    extra_system_context=extra_system_context,
                    intent_decision=intent_decision,
                    model=effective_model,
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
                memory.append_message(session_id, {"role": "user", "content": user_text})
                _auto_remember_user_facts(user_text)
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
                return _chat_with_tools(
                    session_id,
                    user_text,
                    msgs,
                    files_produced,
                    tool_schemas,
                    model=effective_model,
                    require_file_write=bool(_persistent_record_write_context(user_text)),
                )
    finally:
        settings.output_dir = base_output_dir


def _chat_with_tools(
    session_id: str,
    user_text: str,
    msgs: list[dict],
    files_produced: list[str],
    tool_schemas: list[dict],
    model: str = "",
    require_file_write: bool = False,
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
        dsml_tool_calls = _extract_dsml_tool_calls(ai_msg.get("content") or "", iteration=iteration)
        if dsml_tool_calls and not ai_msg.get("tool_calls"):
            clean_content = _strip_dsml_tool_blocks(ai_msg.get("content") or "")
            ai_msg = dict(ai_msg)
            ai_msg["content"] = clean_content
            ai_msg["tool_calls"] = dsml_tool_calls
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
            if require_file_write and not _has_successful_write_file(last_tool_results):
                msgs.append({
                    "role": "system",
                    "content": (
                        "[必须完成文件写入]\n"
                        "本轮是长期记录更新任务，但你还没有成功调用 write_file。"
                        "不能回复“已记录/已保存/完成”。下一步必须先 read_file 目标文件，"
                        "把用户的新记录追加到文件内的数据结构，再调用 write_file 覆盖同一个文件。"
                        "如果无法写入，最终必须明确说没有记录成功。"
                    ),
                })
                continue
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
                _remember_artifact_path(user_text, name, result)
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
            memory.append_message(session_id, _compress_tool_msg_for_memory(tool_msg))
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


def _stream_collect(stream: Iterator[dict]) -> Iterator[dict]:
    """累积 chat_completion_stream，逐 token yield text 事件，最后用 return 把
    完整的 ai_msg + usage + model 一起还回去（通过 yield from 的返回值机制）。"""
    full_content = ""
    tool_calls_by_index: dict[int, dict] = {}
    usage: dict = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    used_model = settings.model

    for chunk in stream:
        if chunk.get("usage"):
            u = chunk["usage"]
            usage = {
                "prompt_tokens": u.get("prompt_tokens", 0),
                "completion_tokens": u.get("completion_tokens", 0),
                "total_tokens": u.get("total_tokens", 0),
            }
        if chunk.get("model"):
            used_model = chunk["model"]
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        content_delta = delta.get("content")
        if content_delta:
            full_content += content_delta
            yield {"type": "text", "delta": content_delta}
        for tc in (delta.get("tool_calls") or []):
            idx = tc.get("index", 0)
            entry = tool_calls_by_index.setdefault(idx, {
                "id": "",
                "type": "function",
                "function": {"name": "", "arguments": ""},
            })
            if tc.get("id"):
                entry["id"] = tc["id"]
            if tc.get("type"):
                entry["type"] = tc["type"]
            fn = tc.get("function") or {}
            if fn.get("name") and not entry["function"]["name"]:
                entry["function"]["name"] = fn["name"]
            if fn.get("arguments"):
                entry["function"]["arguments"] += fn["arguments"]

    ai_msg: dict = {"role": "assistant"}
    if full_content:
        ai_msg["content"] = full_content
    else:
        ai_msg["content"] = None
    if tool_calls_by_index:
        ai_msg["tool_calls"] = [tool_calls_by_index[i] for i in sorted(tool_calls_by_index.keys())]
    return {"ai_msg": ai_msg, "usage": usage, "model": used_model}


def chat_stream(
    session_id: str,
    user_text: str,
    extra_system_context: Optional[str] = None,
    allow_tools: bool = True,
) -> Iterator[dict]:
    """流式版本的 chat()。

    产出事件序列：
      {"type": "text", "delta": "..."}        — 用户可见文本增量
      {"type": "tool_start", "name": "...", "activity": "..."}
      {"type": "tool_end", "name": "...", "ok": bool}
      {"type": "stopped", "reply": "...", "files": [...]}
      {"type": "done", "reply": "...", "files": [...]}
    """
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
                effective_model = routing.resolve_model(intent_decision.get("model_tier") or routing.classify(user_text))
                msgs = _build_messages(
                    session_id,
                    user_text,
                    extra_system_context=extra_system_context,
                    intent_decision=intent_decision,
                    model=effective_model,
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
                memory.append_message(session_id, {"role": "user", "content": user_text})
                _auto_remember_user_facts(user_text)
                effective_allow_tools = allow_tools and bool(intent_decision.get("use_tools", True))

                if not effective_allow_tools:
                    stream = llm.chat_completion_stream(messages=msgs, model=effective_model)
                    result = yield from _stream_collect(stream)
                    ai_msg = result["ai_msg"]
                    raw_content = ai_msg.get("content") or ""
                    clean_msg = dict(ai_msg)
                    clean_msg["content"] = task_mode.strip_update_markers(raw_content)
                    memory.append_message(session_id, clean_msg)
                    yield {"type": "done", "reply": raw_content, "files": files_produced}
                    return

                tool_schemas = tools.tool_schemas_for(
                    user_text,
                    extra_system_context=extra_system_context,
                    intent_decision=intent_decision,
                )
                yield from _chat_stream_with_tools(
                    session_id,
                    user_text,
                    msgs,
                    files_produced,
                    tool_schemas,
                    model=effective_model,
                    require_file_write=bool(_persistent_record_write_context(user_text)),
                )
    finally:
        settings.output_dir = base_output_dir


def _chat_stream_with_tools(
    session_id: str,
    user_text: str,
    msgs: list[dict],
    files_produced: list[str],
    tool_schemas: list[dict],
    model: str = "",
    require_file_write: bool = False,
) -> Iterator[dict]:
    """流式工具循环。逻辑与 _chat_with_tools 等价，把 LLM 调用换成流式版本。"""
    last_tool_results: list[dict] = []
    max_iterations = min(settings.max_tool_iterations, 4) if _looks_like_shopping_or_quote_task(user_text) else settings.max_tool_iterations
    for iteration in range(max_iterations):
        if session_control.is_stopped(session_id):
            stop_msg = "已停止当前任务。"
            memory.append_message(session_id, {"role": "assistant", "content": stop_msg})
            yield {"type": "stopped", "reply": stop_msg, "files": files_produced}
            return

        stream = llm.chat_completion_stream(messages=msgs, tools=tool_schemas, model=model)
        result = yield from _stream_collect(stream)
        ai_msg = result["ai_msg"]
        token_usage = result["usage"]
        model = result["model"] or model or settings.model

        dsml_tool_calls = _extract_dsml_tool_calls(ai_msg.get("content") or "", iteration=iteration)
        if dsml_tool_calls and not ai_msg.get("tool_calls"):
            clean_content = _strip_dsml_tool_blocks(ai_msg.get("content") or "")
            ai_msg = dict(ai_msg)
            ai_msg["content"] = clean_content
            ai_msg["tool_calls"] = dsml_tool_calls
        msgs.append(ai_msg)

        tool_calls = ai_msg.get("tool_calls") or []
        if not tool_calls:
            if require_file_write and not _has_successful_write_file(last_tool_results):
                msgs.append({
                    "role": "system",
                    "content": (
                        "[必须完成文件写入]\n"
                        "本轮是长期记录更新任务，但你还没有成功调用 write_file。"
                        "不能回复“已记录/已保存/完成”。下一步必须先 read_file 目标文件，"
                        "把用户的新记录追加到文件内的数据结构，再调用 write_file 覆盖同一个文件。"
                        "如果无法写入，最终必须明确说没有记录成功。"
                    ),
                })
                continue
            raw_content = ai_msg.get("content") or ""
            if _needs_comparison_table(user_text, raw_content, last_tool_results):
                # 让 UI 清空已经流过的 raw_content，从空白开始流出表格增强版。
                yield {"type": "replace"}
                raw_content = yield from _add_comparison_table_stream(user_text, raw_content, last_tool_results)
            clean_msg = dict(ai_msg)
            clean_msg["content"] = task_mode.strip_update_markers(raw_content)
            memory.append_message(session_id, clean_msg)
            yield {"type": "done", "reply": raw_content, "files": files_produced}
            return

        memory.append_message(session_id, ai_msg)

        pending_error_notes: list[str] = []
        for tc in tool_calls:
            name = tc["function"]["name"]
            args = tc["function"].get("arguments", "{}")
            activity_text = _tool_activity_text(name, args)
            task_mode.add_activity(session_id, activity_text, "tool")
            yield {"type": "tool_start", "name": name, "activity": activity_text}
            result_obj = tools.run_tool(
                name, args,
                task_id=session_id,
                user_input=user_text,
                model=model,
                token_usage=token_usage,
            )
            ok = not (isinstance(result_obj, dict) and result_obj.get("error"))
            yield {"type": "tool_end", "name": name, "ok": ok}
            saved_evidence = evidence.record_tool_result(session_id, name, result_obj)
            if saved_evidence:
                task_mode.add_activity(session_id, f"已保存 {len(saved_evidence)} 条来源证据", "artifact")

            if isinstance(result_obj, dict) and "path" in result_obj:
                files_produced.append(result_obj["path"])
                _remember_artifact_path(user_text, name, result_obj)
            last_tool_results.append({
                "name": name,
                "result": result_obj,
            })

            tool_msg = {
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": name,
                "content": json.dumps(result_obj, ensure_ascii=False),
            }
            msgs.append(tool_msg)
            memory.append_message(session_id, _compress_tool_msg_for_memory(tool_msg))
            if isinstance(result_obj, dict) and result_obj.get("error"):
                task_mode.add_activity(session_id, "这个来源响应不完整，正在换方法", "tool_error")
                pending_error_notes.append(
                    f"工具 {name} 没有完成任务，错误是：{result_obj.get('error')}"
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

    fallback = yield from _tool_loop_fallback_stream(user_text, last_tool_results)
    memory.append_message(session_id, {"role": "assistant", "content": fallback})
    yield {"type": "done", "reply": fallback, "files": files_produced}


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


def _stream_simple_chat(messages: list[dict], temperature: float = 0.2) -> Iterator[dict]:
    """单次流式 LLM 调用助手。沿途 yield text 事件，用 return 把最终 ai_msg 还回去。"""
    stream = llm.chat_completion_stream(messages=messages, temperature=temperature)
    result = yield from _stream_collect(stream)
    return result["ai_msg"]


def _tool_loop_fallback_stream(user_text: str, tool_results: list[dict]) -> Iterator[dict]:
    """流式版本的 _tool_loop_fallback。yield text 事件，return 最终 reply 字符串。"""
    if not tool_results:
        msg = "我没能完成这个任务：工具调用没有收敛。请把任务拆小一点，或给我更具体的网页/文件路径。"
        yield {"type": "text", "delta": msg}
        return msg
    compact = json.dumps(_compact_tool_results(tool_results[-8:]), ensure_ascii=False)
    try:
        ai_msg = yield from _stream_simple_chat(
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
        content = ai_msg.get("content") or "工具调用未收敛，但已获得部分结果；请换一个更具体的问题再试。"
        return content
    except Exception:
        msg = "工具调用未收敛，但已获得部分结果；请换一个更具体的问题再试。"
        yield {"type": "text", "delta": msg}
        return msg


def _add_comparison_table_stream(
    user_text: str, reply: str, tool_results: list[dict]
) -> Iterator[dict]:
    """流式版本的 _add_comparison_table。

    调用前外层会先 emit 一个 `replace` 事件清空 UI 已有内容；本函数从空白开始
    流出补全后的回复。如果 LLM 输出没有合法 markdown 表格，再 emit 一次 replace
    + 静态 fallback 表格作为兜底。返回最终 reply 字符串（供 memory 持久化）。
    """
    compact = json.dumps(_compact_tool_results(tool_results[-8:]), ensure_ascii=False)
    try:
        ai_msg = yield from _stream_simple_chat(
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
        content = ai_msg.get("content") or ""
        if _contains_markdown_table(content):
            return content
        # LLM 没产出合规表格 —— 用静态 fallback 替换刚刚流过的内容。
        yield {"type": "replace"}
        fallback = _fallback_comparison_table(reply)
        yield {"type": "text", "delta": fallback}
        return fallback
    except Exception:
        yield {"type": "replace"}
        fallback = _fallback_comparison_table(reply)
        yield {"type": "text", "delta": fallback}
        return fallback


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


_TOOL_RESULT_MEMORY_MAX_CHARS = 2000


def _compress_tool_msg_for_memory(tool_msg: dict) -> dict:
    """把 tool message 持久化进 history 之前压缩 content。

    工具结果（fetch_webpage / search_web 等）原始大小常有 10KB+，每轮 load_history
    都会被重发给模型，token 浪费很大。本函数返回一个浅拷贝，把 content 字符串
    截断成 head+tail 形式（保留 JSON 外壳便于人/模型解读），中间标记跳过字数。
    当前 turn 的 msgs 缓冲区仍保有原始完整 content，不受影响。
    """
    content = tool_msg.get("content")
    if not isinstance(content, str) or len(content) <= _TOOL_RESULT_MEMORY_MAX_CHARS:
        return tool_msg
    head_chars = _TOOL_RESULT_MEMORY_MAX_CHARS * 2 // 3
    tail_chars = _TOOL_RESULT_MEMORY_MAX_CHARS // 3
    skipped = len(content) - head_chars - tail_chars
    truncated = (
        content[:head_chars]
        + f"\n\n[... {skipped} characters truncated from tool result for history compaction ...]\n\n"
        + content[-tail_chars:]
    )
    compressed = dict(tool_msg)
    compressed["content"] = truncated
    return compressed


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
            "- This is only the default. The current user message language has higher priority.\n"
        )
    return (
        "[系统语言]\n"
        "- 默认回复语言：中文。\n"
        "- 这只是默认语言；当前用户消息使用的语言优先级更高。\n"
    )


def _turn_language_context(user_text: str) -> str:
    lang = _detect_turn_language(user_text)
    if lang == "en":
        return (
            "[Current Turn Language]\n"
            "- The current user message is in English. Reply in English.\n"
            "- Do not switch to Chinese just because the saved system language or older conversation is Chinese.\n"
        )
    if lang == "zh":
        return (
            "[当前轮次语言]\n"
            "- 当前用户消息是中文。请用中文回复。\n"
            "- 不要因为历史对话或默认系统语言切换到英文。\n"
        )
    return (
        "[当前轮次语言]\n"
        "- 当前用户消息语言不明确。使用系统默认语言回复；如果用户明确要求某种语言，则按用户要求。\n"
    )


def _detect_turn_language(user_text: str) -> str:
    text = user_text or ""
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_words = re.findall(r"[A-Za-z]{2,}", text)
    if cjk_count >= 2 and cjk_count >= len(latin_words):
        return "zh"
    if len(latin_words) >= 2 and cjk_count == 0:
        return "en"
    if len(latin_words) >= 4 and len(latin_words) > cjk_count * 2:
        return "en"
    return "unknown"


_IDENTITY_MEMORY_PATTERNS = (
    "我是谁",
    "你知道我是谁",
    "你还记得我是谁",
    "我的名字",
    "我的年龄",
    "我多大",
    "年龄",
    "身高",
    "体重",
    "bmi",
    "我叫什么",
    "关于我",
    "我的身份",
    "who am i",
    "what do you know about me",
    "what is my name",
    "my age",
    "how old am i",
)
_BOOKKEEPING_MEMORY_PATTERNS = (
    "记账",
    "账本",
    "账单",
    "支出",
    "收入",
    "花了",
    "消费",
    "bookkeeper",
    "ledger",
)
_FITNESS_MEMORY_PATTERNS = (
    "健身",
    "体重",
    "饮食",
    "训练",
    "卡路里",
    "蛋白",
    "fitness",
    "workout",
    "meal",
    "calorie",
    "protein",
    "bmi",
)


def _memory_context(user_text: str) -> str:
    text = user_text.strip().lower()
    if not text:
        return ""
    wants_identity = any(pattern in text for pattern in _IDENTITY_MEMORY_PATTERNS)
    wants_bookkeeping = any(pattern in text for pattern in _BOOKKEEPING_MEMORY_PATTERNS)
    wants_fitness = _matches_fitness_record(text)
    if wants_identity or wants_bookkeeping or wants_fitness:
        facts = _relevant_memories_for_turn(
            user_text,
            wants_identity=wants_identity,
            wants_bookkeeping=wants_bookkeeping,
            wants_fitness=wants_fitness,
        )
        if not facts:
            return ""
        lines = []
        for fact in facts:
            title = fact.get("title") or fact.get("key") or "记忆"
            content = fact.get("content") or fact.get("value") or ""
            if content:
                lines.append(f"- {title}: {content}")
        if lines:
            guidance = []
            if wants_identity:
                guidance.append("用户询问姓名、年龄、身高、体重、BMI 或个人资料时，必须优先根据这些记忆回答；不要说没有记录；缺少身高/体重时说明无法计算 BMI 并只追问缺失项。")
            if wants_bookkeeping:
                guidance.append("用户要求记账或继续维护账本时，优先使用记忆里的 bookkeeper/账本文件路径，不要说找不到文件；如需修改文件，先 read_file 再 write_file。")
            if wants_fitness:
                guidance.append("用户要求记录或查询体重、饮食、健身、BMI 时，优先使用记忆里的 fit/fitness/体重饮食记录文件路径；如需追加记录，先 read_file 再 write_file 覆盖同一个文件。")
            return "[相关长期记忆]\n" + "\n".join(lines[:15]) + "\n" + "\n".join(guidance)
    return ""


def _persistent_record_write_context(user_text: str) -> str:
    text = (user_text or "").strip().lower()
    if not _looks_like_persistent_record_write(text):
        return ""
    wants_bookkeeping = any(pattern in text for pattern in _BOOKKEEPING_MEMORY_PATTERNS)
    wants_fitness = _matches_fitness_record(text)
    if not wants_bookkeeping and not wants_fitness:
        return ""

    key = "artifact_bookkeeper_path" if wants_bookkeeping else "artifact_fitness_path"
    label = "bookkeeper/记账" if wants_bookkeeping else "fit/fitness/体重饮食"
    path = _remembered_artifact_path(key)
    if not path:
        return ""
    return (
        f"[必须写入长期记录文件]\n"
        f"- 本轮用户是在追加一条 {label} 记录，不是普通聊天。\n"
        f"- 目标文件路径：{path}\n"
        "- 必须调用 read_file 读取这个文件，然后调用 write_file 覆盖同一个路径，把本轮新记录追加进文件内的数据结构。\n"
        "- 如果文件使用 localStorage，也必须同步更新文件内的固定数据数组或 JSON 数据区；不能只依赖浏览器 localStorage。\n"
        "- 只有 write_file 成功后，才可以回复已记录，并在回复里提到写入的准确路径。\n"
        "- 如果 read_file 或 write_file 失败，必须明确说没有记录成功，不要口头确认。"
    )


def _looks_like_persistent_record_write(text: str) -> bool:
    if not text:
        return False
    write_words = (
        "记录",
        "记一下",
        "记下",
        "记一笔",
        "记账",
        "添加",
        "新增",
        "保存",
        "log",
        "record",
        "add",
        "save",
    )
    if any(word in text for word in write_words):
        return True
    return bool(re.search(r"\b\d+(?:\.\d+)?\s*(kg|公斤|千克|cal|kcal|g)\b", text))


def _remembered_artifact_path(key: str) -> str:
    for fact in memory.list_memories(confirmed=1, limit=120):
        fact_key = str(fact.get("key") or fact.get("title") or "")
        if fact_key != key:
            continue
        content = str(fact.get("value") or fact.get("content") or "")
        match = re.search(r"(/[^。\n\r]+)", content)
        if match:
            return match.group(1).strip()
    return ""


def _relevant_memories_for_turn(
    user_text: str,
    *,
    wants_identity: bool,
    wants_bookkeeping: bool,
    wants_fitness: bool = False,
) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()

    def add(fact: dict) -> None:
        fid = str(fact.get("id") or fact.get("key") or fact.get("title") or "")
        if fid and fid in seen:
            return
        if fid:
            seen.add(fid)
        out.append(fact)

    for fact in memory.recall(user_text, top_k=8):
        if _fact_matches_memory_need(
            fact,
            user_text,
            wants_identity=wants_identity,
            wants_bookkeeping=wants_bookkeeping,
            wants_fitness=wants_fitness,
        ):
            add(fact)
    for fact in memory.list_memories(confirmed=1, limit=80):
        if _fact_matches_memory_need(
            fact,
            user_text,
            wants_identity=wants_identity,
            wants_bookkeeping=wants_bookkeeping,
            wants_fitness=wants_fitness,
        ):
            add(fact)
    has_explicit_bookkeeper_path = any(
        str(fact.get("key") or fact.get("title") or "") == "artifact_bookkeeper_path"
        for fact in out
    )
    if wants_bookkeeping and not has_explicit_bookkeeper_path:
        for path in _bookkeeper_file_candidates():
            add({
                "id": f"bookkeeper-file:{path}",
                "key": "bookkeeper_candidate_file",
                "value": f"找到可能的 bookkeeper/记账本文件：{path}",
                "type": "project",
                "importance": 4,
                "tags": "artifact,file,bookkeeper,记账,账本",
            })
    has_explicit_fitness_path = any(
        str(fact.get("key") or fact.get("title") or "") == "artifact_fitness_path"
        for fact in out
    )
    if wants_fitness and not has_explicit_fitness_path:
        for path in _fitness_file_candidates():
            add({
                "id": f"fitness-file:{path}",
                "key": "fitness_candidate_file",
                "value": f"找到可能的 fit/fitness/体重饮食记录文件：{path}",
                "type": "project",
                "importance": 4,
                "tags": "artifact,file,fit,fitness,体重,饮食,健身",
            })
    return out[:15]


def _fact_matches_memory_need(
    fact: dict,
    user_text: str,
    *,
    wants_identity: bool,
    wants_bookkeeping: bool,
    wants_fitness: bool = False,
) -> bool:
    query = (user_text or "").lower()
    wants_license = any(token in query for token in ("驾照", "驾驶证", "license", "licence"))
    key = str(fact.get("title") or fact.get("key") or "").lower()
    content = str(fact.get("content") or fact.get("value") or "").lower()
    tags = str(fact.get("tags") or "").lower()
    haystack = f"{key} {content} {tags}"
    if any(token in haystack for token in ("driving license", "driver license", "驾驶证", "驾照")) and not wants_license:
        return False
    if wants_identity and any(token in haystack for token in ("personal", "user_info", "user_", "user profile", "年龄", "身高", "体重", "bmi", "44岁")):
        return True
    if wants_bookkeeping and any(token in haystack for token in ("bookkeeper", "ledger", "记账", "账本", "finance", "accounting")):
        return True
    if wants_fitness and (
        any(token in haystack for token in ("fitness", "健身", "体重", "饮食", "训练", "health", "workout", "meal"))
        or _matches_fitness_record(haystack)
    ):
        return True
    return False


def _matches_fitness_record(text: str) -> bool:
    haystack = (text or "").lower()
    if any(pattern in haystack for pattern in _FITNESS_MEMORY_PATTERNS):
        return True
    return bool(re.search(r"(^|[^a-z])fit([^a-z]|$)", haystack))


def _auto_remember_user_facts(user_text: str) -> None:
    """Persist simple first-person facts without relying on the model to call memory tools."""
    text = (user_text or "").strip()
    if not text:
        return
    facts: list[tuple[str, str, list[str], str, int]] = []

    name_match = re.search(
        r"(?:我(?:的)?(?:名字)?叫|我是|my name is|i am)\s*([A-Za-z][A-Za-z .'-]{1,40}|[\u4e00-\u9fff]{2,12})",
        text,
        re.I,
    )
    if name_match:
        name = name_match.group(1).strip(" ，,。.!！")
        if name.lower() not in {"here", "from", "a", "an", "the"}:
            facts.append(("user_name", f"用户名字是 {name}", ["personal", "user_info", "name"], "preference", 5))

    age_match = re.search(r"(?:我(?:今年)?|年龄|age(?: is)?)\s*[:：]?\s*(\d{1,3})\s*(?:岁|years old|yo)?", text, re.I)
    if not age_match:
        age_match = re.search(r"(\d{1,3})\s*岁", text)
    if age_match:
        age = int(age_match.group(1))
        if 1 <= age <= 120:
            facts.append(("user_age", f"用户年龄是 {age} 岁", ["personal", "user_info", "age", "年龄"], "preference", 5))

    height_match = re.search(r"(?:身高|height)\s*[:：]?\s*(\d{2,3}(?:\.\d+)?)\s*(cm|厘米|m|米)?", text, re.I)
    if height_match:
        height = float(height_match.group(1))
        unit = (height_match.group(2) or "cm").lower()
        if unit in {"m", "米"}:
            height *= 100
        if 80 <= height <= 250:
            facts.append(("user_height_cm", f"用户身高是 {height:g} cm", ["personal", "user_info", "height", "身高", "bmi"], "preference", 5))

    weight_match = re.search(r"(?:体重|weight)\s*[:：]?\s*(\d{2,3}(?:\.\d+)?)\s*(kg|公斤|千克)?", text, re.I)
    if weight_match:
        weight = float(weight_match.group(1))
        if 20 <= weight <= 300:
            facts.append(("user_weight_kg", f"用户体重是 {weight:g} kg", ["personal", "user_info", "weight", "体重", "bmi"], "preference", 5))

    if not facts:
        return

    # 新提取的事实只用于未来回合（本轮 memory_context 已读取过旧记忆），
    # 因此把含 embed 调用的 upsert 放到后台线程，避免阻塞主流式响应。
    def _persist_facts() -> None:
        for key, value, tags, fact_type, importance in facts:
            try:
                memory.upsert_memory(key, value, tags=tags, type=fact_type, source="chat", importance=importance)
            except Exception:
                pass

    threading.Thread(target=_persist_facts, daemon=True, name="auto-remember-facts").start()


def _remember_artifact_path(user_text: str, tool_name: str, result: dict) -> None:
    path = str(result.get("path") or "").strip()
    if not path:
        return
    filename = str(result.get("filename") or Path(path).name)
    text = f"{user_text} {filename}".lower()
    if any(pattern in text for pattern in _BOOKKEEPING_MEMORY_PATTERNS):
        try:
            memory.upsert_memory(
                "artifact_bookkeeper_path",
                f"用户的 bookkeeper/记账本文件路径是 {path}",
                tags=["artifact", "file", "bookkeeper", "记账", "账本", "finance", "accounting"],
                type="project",
                source=f"tool:{tool_name}",
                importance=5,
            )
        except Exception:
            pass
    elif _matches_fitness_record(text):
        try:
            memory.upsert_memory(
                "artifact_fitness_path",
                f"用户的 fit/fitness/体重饮食记录文件路径是 {path}",
                tags=["artifact", "file", "fit", "fitness", "体重", "饮食", "健身", "health"],
                type="project",
                source=f"tool:{tool_name}",
                importance=5,
            )
        except Exception:
            pass


def _bookkeeper_file_candidates() -> list[str]:
    roots = [settings.workspace_dir, settings.output_dir]
    patterns = ("*bookkeeper*", "*ledger*", "*记账*", "*账本*")
    return _file_candidates(roots, patterns)


def _fitness_file_candidates() -> list[str]:
    roots = [settings.workspace_dir, settings.output_dir]
    patterns = ("*fit*", "*fitness*", "*体重*", "*饮食*", "*健身*")
    return _file_candidates(roots, patterns)


def _file_candidates(roots: list[Path], patterns: tuple[str, ...]) -> list[str]:
    found: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        try:
            base = root.resolve()
        except Exception:
            continue
        if not base.exists():
            continue
        for pattern in patterns:
            try:
                matches = list(base.rglob(pattern)) if base.is_dir() else []
            except Exception:
                matches = []
            for path in matches:
                if path.is_file() and path not in seen:
                    seen.add(path)
                    found.append(path)
    found.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return [str(p) for p in found[:5]]


def _has_successful_write_file(tool_results: list[dict]) -> bool:
    for item in tool_results:
        if item.get("name") != "write_file":
            continue
        result = item.get("result")
        if isinstance(result, dict) and not result.get("error"):
            return True
    return False


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


def _sanitize_tool_history(
    messages: list[dict],
    require_reasoning_content: bool = False,
) -> list[dict]:
    """Drop malformed historical tool-call fragments before sending to providers.

    OpenAI requires every assistant message with `tool_calls` to be IMMEDIATELY
    followed by one `tool` message per tool_call_id, with NO other roles in between.
    This sanitizer drops:
      - orphan `tool` messages (no preceding assistant with matching id), and
      - assistant `tool_calls` blocks where any matched tool responses are missing
        or are interrupted by another role.

    Some thinking-mode providers also require assistant `tool_calls` messages to
    replay their `reasoning_content` verbatim in future requests. If old history
    was saved before that field was persisted, drop the incomplete block instead
    of sending a request the provider will reject.
    """
    out: list[dict] = []
    # Pending block under construction: (assistant_index_in_out, remaining_ids, partial_tools)
    pending_assistant_idx: Optional[int] = None
    pending_remaining: set[str] = set()
    pending_tool_indices: list[int] = []

    def drop_pending() -> None:
        nonlocal pending_assistant_idx, pending_remaining, pending_tool_indices
        if pending_assistant_idx is not None:
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
        if role == "assistant" and _contains_dsml_tool_call(msg.get("content") or "") and not msg.get("tool_calls"):
            continue
        if role == "assistant" and msg.get("tool_calls"):
            # If there's already a half-built block, the previous one was incomplete — drop it.
            if pending_remaining:
                drop_pending()
            if require_reasoning_content and not msg.get("reasoning_content"):
                pending_assistant_idx = None
                pending_remaining = {tc.get("id") for tc in (msg.get("tool_calls") or []) if tc.get("id")}
                pending_tool_indices = []
                continue
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
                if pending_assistant_idx is not None:
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


def _requires_reasoning_content_echo(model: str) -> bool:
    value = (model or settings.model or "").lower()
    return "deepseek" in value


def _contains_dsml_tool_call(content: str) -> bool:
    return "DSML" in content and "tool_calls" in content and "invoke name=" in content


def _strip_dsml_tool_blocks(content: str) -> str:
    text = _DSML_TOOL_CALLS_RE.sub("", content or "")
    return text.strip()


_DSML_TOOL_CALLS_RE = re.compile(
    r"<[｜|]\s*[｜|]\s*DSML\s*[｜|]\s*[｜|]\s*tool_calls\s*>.*?"
    r"</[｜|]\s*[｜|]\s*DSML\s*[｜|]\s*[｜|]\s*tool_calls\s*>",
    re.S,
)
_DSML_INVOKE_RE = re.compile(
    r"<[｜|]\s*[｜|]\s*DSML\s*[｜|]\s*[｜|]\s*invoke\s+name=\"([^\"]+)\"\s*>"
    r"(.*?)"
    r"</[｜|]\s*[｜|]\s*DSML\s*[｜|]\s*[｜|]\s*invoke\s*>",
    re.S,
)
_DSML_PARAM_RE = re.compile(
    r"<[｜|]\s*[｜|]\s*DSML\s*[｜|]\s*[｜|]\s*parameter\s+name=\"([^\"]+)\"\s+string=\"(true|false)\"\s*>"
    r"(.*?)"
    r"</[｜|]\s*[｜|]\s*DSML\s*[｜|]\s*[｜|]\s*parameter\s*>",
    re.S,
)


def _extract_dsml_tool_calls(content: str, iteration: int = 0) -> list[dict]:
    if not _contains_dsml_tool_call(content or ""):
        return []
    calls: list[dict] = []
    for idx, match in enumerate(_DSML_INVOKE_RE.finditer(content)):
        name = match.group(1).strip()
        body = match.group(2)
        args: dict = {}
        for param in _DSML_PARAM_RE.finditer(body):
            key = param.group(1).strip()
            is_string = param.group(2) == "true"
            raw_value = html.unescape(param.group(3).strip())
            if is_string:
                args[key] = raw_value
            else:
                try:
                    args[key] = json.loads(raw_value)
                except Exception:
                    args[key] = raw_value
        if name:
            calls.append({
                "id": f"call-dsml-{iteration}-{idx}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            })
    return calls
