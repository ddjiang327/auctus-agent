from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app import agent, evidence, task_mode
from app.config import settings


class AgentOutputIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_output = settings.output_dir
        self.old_logs = settings.logs_dir
        self.old_workspace = settings.workspace_dir
        self.old_iterations = settings.max_tool_iterations
        self.old_data_dir = settings.data_dir

        settings.output_dir = self.root / "outputs"
        settings.logs_dir = self.root / "logs"
        settings.workspace_dir = self.root / "inputs"
        settings.data_dir = self.root / "data"
        settings.output_dir.mkdir()
        settings.logs_dir.mkdir()
        settings.workspace_dir.mkdir()
        settings.data_dir.mkdir()
        settings.max_tool_iterations = 3
        task_mode._TASKS.clear()

    def tearDown(self):
        task_mode._TASKS.clear()
        settings.output_dir = self.old_output
        settings.logs_dir = self.old_logs
        settings.workspace_dir = self.old_workspace
        settings.max_tool_iterations = self.old_iterations
        settings.data_dir = self.old_data_dir
        self.tmp.cleanup()

    def test_chat_writes_generated_files_under_task_directory(self):
        tool_call_response = {
            "model": "test-model",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "make_markdown_report",
                            "arguments": json.dumps({
                                "title": "Report",
                                "sections": [{"heading": "A", "content": "B"}],
                            }),
                        },
                    }],
                }
            }],
        }
        final_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "done"}}],
        }

        session_id = f"task-{uuid.uuid4().hex}"
        with patch("app.agent.llm.chat_completion", side_effect=[tool_call_response, final_response]):
            result = agent.chat(session_id, "make report")

        self.assertEqual(result["reply"], "done")
        self.assertEqual(len(result["files"]), 1)
        generated = Path(result["files"][0])
        self.assertEqual(generated.parent.name, session_id)
        self.assertTrue(generated.exists())
        self.assertEqual(settings.output_dir, self.root / "outputs")

    def test_stream_collect_accepts_message_content_chunks(self):
        stream = iter([{"choices": [{"message": {"content": "hello"}}]}])

        events = list(agent._stream_collect(stream))

        self.assertEqual(events, [{"type": "text", "delta": "hello"}])

    def test_stream_collect_rejects_empty_model_output(self):
        stream = iter([{"choices": [{"delta": {}}]}])

        with self.assertRaisesRegex(RuntimeError, "no model output"):
            list(agent._stream_collect(stream))

    def test_chat_injects_evolution_runtime_context(self):
        final_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "done"}}],
        }
        captured_messages = []

        def fake_completion(*, messages, tools=None, **_kwargs):
            captured_messages.extend(messages)
            return final_response

        with patch("app.agent.evolution.build_runtime_context", return_value="[自我学习规则]\n- 输出路径：列出文件路径"):
            with patch("app.agent.llm.chat_completion", side_effect=fake_completion):
                result = agent.chat("session-runtime", "make report")

        self.assertEqual(result["reply"], "done")
        self.assertTrue(any(msg["role"] == "system" and "[自我学习规则]" in msg["content"] for msg in captured_messages))

    def test_chat_injects_current_workspace_context(self):
        final_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "done"}}],
        }
        captured_messages = []

        def fake_completion(*, messages, tools=None, **_kwargs):
            captured_messages.extend(messages)
            return final_response

        with patch("app.agent.llm.chat_completion", side_effect=fake_completion):
            result = agent.chat("session-workspace-context", "在这个文件夹里建一个 md 文件")

        self.assertEqual(result["reply"], "done")
        workspace = str(settings.workspace_dir.resolve())
        self.assertTrue(
            any(msg["role"] == "system" and workspace in msg["content"] for msg in captured_messages)
        )
        self.assertTrue(any("search_web" in msg.get("content", "") for msg in captured_messages))

    def test_chat_injects_shopping_playbook_for_laptop_request(self):
        final_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "done"}}],
        }
        captured_messages = []

        def fake_completion(*, messages, tools=None, **_kwargs):
            captured_messages.extend(messages)
            return final_response

        with patch("app.agent.llm.chat_completion", side_effect=fake_completion):
            result = agent.chat("session-shopping-playbook", "我想买个可以玩3A游戏的游戏本，在墨尔本")

        self.assertEqual(result["reply"], "done")
        joined = "\n".join(msg.get("content", "") for msg in captured_messages if msg.get("role") == "system")
        self.assertIn("[购买/报价任务策略]", joined)
        self.assertIn("[笔记本购买 playbook]", joined)
        self.assertIn("RTX 4060", joined)

    def test_plain_chat_does_not_run_rag_search(self):
        final_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "你好"}}],
        }

        with patch("app.agent.llm.chat_completion", return_value=final_response):
            with patch("app.rag.search", return_value=[]) as rag_search:
                result = agent.chat("session-no-rag", "你好")

        self.assertEqual(result["reply"], "你好")
        rag_search.assert_not_called()

    def test_document_request_runs_rag_search(self):
        final_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "done"}}],
        }

        with patch("app.agent.llm.chat_completion", return_value=final_response):
            with patch("app.rag.search", return_value=[{"file": "a.md", "content": "context"}]) as rag_search:
                result = agent.chat("session-with-rag", "查一下本地知识库里的项目文档")

        self.assertEqual(result["reply"], "done")
        rag_search.assert_called_once()

    def test_chat_injects_lightweight_preference_summary(self):
        final_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "done"}}],
        }
        captured_messages = []

        def fake_completion(*, messages, tools=None, **_kwargs):
            captured_messages.extend(messages)
            return final_response

        with patch("app.agent.preferences.get_summary", return_value="- 默认用中文简洁回答"):
            with patch("app.agent.llm.chat_completion", side_effect=fake_completion):
                result = agent.chat("session-pref-summary", "帮我总结一下")

        self.assertEqual(result["reply"], "done")
        self.assertTrue(any("[用户偏好摘要]" in msg.get("content", "") for msg in captured_messages))

    def test_chat_keeps_task_update_marker_out_of_history(self):
        marker = '<!--TASK_UPDATE {"current_step":2,"status":"in_progress","activity_key":"advanced"}-->'
        final_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": f"进入比较标准。\n{marker}"}}],
        }

        with patch("app.agent.llm.chat_completion", return_value=final_response):
            result = agent.chat("session-marker-history", "买游戏本")

        self.assertIn("TASK_UPDATE", result["reply"])
        history = agent.memory.load_history("session-marker-history", limit=5)
        assistant_messages = [m for m in history if m["role"] == "assistant"]
        self.assertEqual(assistant_messages[-1]["content"], "进入比较标准。")

    def test_tool_loop_fallback_summarizes_partial_results(self):
        tool_call_response = {
            "model": "test-model",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "fetch_webpage",
                            "arguments": json.dumps({"url": "https://example.com"}),
                        },
                    }],
                }
            }],
        }
        summary_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "已整理部分网页结果"}}],
        }

        captured_fallback_messages = []

        def fake_completion(*, messages, tools=None, **_kwargs):
            if tools is None:
                captured_fallback_messages.extend(messages)
                return summary_response
            return tool_call_response

        settings.max_tool_iterations = 1
        with patch("app.agent.llm.chat_completion", side_effect=fake_completion):
            with patch("app.agent.tools.run_tool", return_value={"url": "https://example.com", "content": "partial"}):
                result = agent.chat("session-fallback", "查网页")

        self.assertEqual(result["reply"], "已整理部分网页结果")
        fallback_prompt = "\n".join(msg.get("content", "") for msg in captured_fallback_messages)
        self.assertIn("不要把工具失败本身当最终答案", fallback_prompt)
        self.assertIn("可行动建议", fallback_prompt)

    def test_repeated_tool_failure_adds_source_switch_hint(self):
        tool_call_response = {
            "model": "test-model",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "fetch_webpage",
                            "arguments": json.dumps({"url": "https://www.jbhifi.com.au/collections/computers-tablets/gaming-laptops"}),
                        },
                    }],
                }
            }],
        }
        final_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "我会换来源继续查"}}],
        }
        captured_messages = []

        def fake_completion(*, messages, tools=None, **_kwargs):
            captured_messages.append([m.get("content", "") for m in messages if m.get("role") == "system"])
            if len(captured_messages) <= 2:
                return tool_call_response
            return final_response

        settings.max_tool_iterations = 3
        with patch("app.agent.llm.chat_completion", side_effect=fake_completion):
            with patch("app.agent.tools.run_tool", return_value={"url": "https://www.jbhifi.com.au/collections/computers-tablets/gaming-laptops", "error": "page closed"}):
                result = agent.chat("session-source-switch", "我想买个可以玩3A游戏的游戏本，在墨尔本")

        self.assertIn("我会换来源继续查", result["reply"])
        self.assertIn("| 候选 |", result["reply"])
        joined = "\n".join("\n".join(batch) for batch in captured_messages)
        self.assertIn("[工具策略调整]", joined)
        self.assertIn("不要继续卡在同一个网页或同一个域名", joined)
        self.assertIn("至少尝试 3 个不同来源", joined)

    def test_shopping_tool_loop_has_smaller_budget_and_table_fallback(self):
        tool_call_response = {
            "model": "test-model",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "search_web",
                            "arguments": json.dumps({"query": "RTX 4060 gaming laptop Australia"}),
                        },
                    }],
                }
            }],
        }
        summary_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "| 候选 | 来源 |\\n|---|---|\\n| 待核验 | 待核验 |"}}],
        }
        captured_fallback_messages = []

        def fake_completion(*, messages, tools=None, **_kwargs):
            if tools is None:
                captured_fallback_messages.extend(messages)
                return summary_response
            return tool_call_response

        settings.max_tool_iterations = 8
        with patch("app.agent.llm.chat_completion", side_effect=fake_completion):
            with patch("app.agent.tools.run_tool", return_value={"results": []}) as run_tool:
                result = agent.chat("session-shopping-budget", "我想买个游戏本，预算2000澳币")

        self.assertEqual(run_tool.call_count, 4)
        self.assertIn("| 候选 |", result["reply"])
        fallback_prompt = "\n".join(msg.get("content", "") for msg in captured_fallback_messages)
        self.assertIn("必须输出标准 Markdown 表格", fallback_prompt)

    def test_shopping_reply_after_tools_gets_table_postprocessed(self):
        tool_call_response = {
            "model": "test-model",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "search_web",
                            "arguments": json.dumps({"query": "RTX 4060 laptop Australia"}),
                        },
                    }],
                }
            }],
        }
        no_table_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "找到一些方向，但还没有完整价格。"}}],
        }
        table_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "| 候选 | 价格/报价 | 关键配置/条款 | 来源 | 优点 | 风险 | 下一步核验项 |\n|---|---:|---|---|---|---|---|\n| RTX 4060 | 待核验 | 16GB | JB Hi-Fi | 性价比 | 库存未知 | 查库存 |"}}],
        }

        with patch("app.agent.llm.chat_completion", side_effect=[tool_call_response, no_table_response, table_response]):
            with patch("app.agent.tools.run_tool", return_value={"results": [{"title": "RTX 4060 laptop"}]}):
                result = agent.chat(f"session-shopping-table-postprocess-{uuid.uuid4().hex}", "我想买个游戏本，预算2000澳币")

        self.assertIn("| 候选 |", result["reply"])
        self.assertIn("RTX 4060", result["reply"])

    def test_tool_calls_append_task_activity_when_task_exists(self):
        session_id = f"session-tool-activity-{uuid.uuid4().hex}"
        task_mode.create_or_resume(session_id, "买游戏本")
        tool_call_response = {
            "model": "test-model",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "search_web",
                            "arguments": json.dumps({"query": "RTX 4060 gaming laptop Australia"}),
                        },
                    }],
                }
            }],
        }
        final_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "| 候选 | 来源 |\n|---|---|\n| RTX 4060 | 搜索 |"}}],
        }

        with patch("app.agent.llm.chat_completion", side_effect=[tool_call_response, final_response]):
            with patch("app.agent.tools.run_tool", return_value={"results": [{"title": "RTX 4060"}]}):
                agent.chat(session_id, "我想买个游戏本，预算2000澳币")

        task = task_mode.get(session_id)
        texts = [item["text"] for item in task["activity"]]
        self.assertTrue(any("正在搜索" in text for text in texts), texts)
        self.assertTrue(any("已检查一个来源" in text for text in texts), texts)

    def test_search_tool_results_are_saved_as_evidence(self):
        session_id = f"session-evidence-{uuid.uuid4().hex}"
        tool_call_response = {
            "model": "test-model",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "search_web",
                            "arguments": json.dumps({"query": "RTX 4060 laptop Australia"}),
                        },
                    }],
                }
            }],
        }
        final_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "| 候选 | 来源 |\n|---|---|\n| RTX 4060 | Example |"}}],
        }

        with patch("app.agent.llm.chat_completion", side_effect=[tool_call_response, final_response]):
            with patch("app.agent.tools.run_tool", return_value={
                "results": [{"title": "RTX 4060 Deal", "url": "https://example.com/deal", "snippet": "AU laptop deal"}]
            }):
                agent.chat(session_id, "帮我查 RTX 4060 游戏本")

        items = evidence.list_evidence(session_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "RTX 4060 Deal")
        self.assertEqual(items[0]["source_domain"], "example.com")

    def test_chat_injects_persona_context(self):
        final_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "done"}}],
        }
        captured_messages = []

        def fake_completion(*, messages, tools=None, **_kwargs):
            captured_messages.extend(messages)
            return final_response

        with patch("app.agent.accounting.get_setup_state", return_value={"persona": "warm_uncle"}):
            with patch("app.agent.llm.chat_completion", side_effect=fake_completion):
                result = agent.chat("session-persona-context", "你好")

        self.assertEqual(result["reply"], "done")
        self.assertTrue(any("知心大叔" in msg.get("content", "") for msg in captured_messages))

    def test_chat_injects_system_language_context(self):
        final_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "done"}}],
        }
        captured_messages = []

        def fake_completion(*, messages, tools=None, **_kwargs):
            captured_messages.extend(messages)
            return final_response

        with patch("app.agent.accounting.get_setup_state", return_value={"system_language": "en"}):
            with patch("app.agent.llm.chat_completion", side_effect=fake_completion):
                result = agent.chat("session-language-context", "hello")

        self.assertEqual(result["reply"], "done")
        self.assertTrue(any("Default reply language: English" in msg.get("content", "") for msg in captured_messages))

    def test_english_user_message_overrides_chinese_system_language(self):
        final_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "done"}}],
        }
        captured_messages = []

        def fake_completion(*, messages, tools=None, **_kwargs):
            captured_messages.extend(messages)
            return final_response

        with patch("app.agent.accounting.get_setup_state", return_value={"system_language": "zh"}):
            with patch("app.agent.llm.chat_completion", side_effect=fake_completion):
                result = agent.chat("session-english-turn-language", "Please help me record 70kg today")

        self.assertEqual(result["reply"], "done")
        joined = "\n".join(msg.get("content", "") for msg in captured_messages)
        self.assertIn("The current user message is in English. Reply in English.", joined)
        self.assertIn("这只是默认语言；当前用户消息使用的语言优先级更高", joined)

    def test_chat_injects_identity_memory_context_for_who_am_i(self):
        final_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "你是 Alex"}}],
        }
        captured_messages = []

        def fake_completion(*, messages, tools=None, **_kwargs):
            captured_messages.extend(messages)
            return final_response

        facts = [
            {
                "title": "User profile",
                "content": "Alex，44岁，程序员，住在 Example City。",
                "confirmed": True,
            },
            {
                "title": "Agent name",
                "content": "用户叫我 auc。",
                "confirmed": True,
            },
        ]
        with patch("app.agent.memory.list_memories", return_value=facts) as list_memories:
            with patch("app.agent.llm.chat_completion", side_effect=fake_completion):
                result = agent.chat("session-identity-context", "我是谁")

        self.assertEqual(result["reply"], "你是 Alex")
        list_memories.assert_called_once_with(confirmed=1, limit=80)
        memory_contexts = [msg["content"] for msg in captured_messages if "[相关长期记忆]" in msg.get("content", "")]
        self.assertEqual(len(memory_contexts), 1)
        self.assertIn("Alex", memory_contexts[0])
        self.assertIn("不要说没有记录", memory_contexts[0])

    def test_chat_does_not_duplicate_current_user_message(self):
        final_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "done"}}],
        }
        captured_messages = []

        def fake_completion(*, messages, tools=None, **_kwargs):
            captured_messages.extend(messages)
            return final_response

        session_id = f"session-no-duplicate-{uuid.uuid4().hex}"
        with patch("app.agent.llm.chat_completion", side_effect=fake_completion):
            agent.chat(session_id, "only once")

        user_messages = [m for m in captured_messages if m["role"] == "user" and m["content"] == "only once"]
        self.assertEqual(len(user_messages), 1)

    def test_chat_authorizes_absolute_path_from_current_user_message(self):
        outside_dir = self.root / "Desktop"
        outside_dir.mkdir()
        outside_file = outside_dir / "Screen Shot 2026-05-13 at 23.48.08 PM.md"
        outside_file.write_text("outside visible", encoding="utf-8")
        tool_call_response = {
            "model": "test-model",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": str(outside_file)}),
                        },
                    }],
                }
            }],
        }
        final_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "read it"}}],
        }
        captured_messages = []

        def fake_completion(*, messages, tools=None, **_kwargs):
            captured_messages.extend(messages)
            return tool_call_response if not any(m.get("role") == "tool" for m in messages) else final_response

        with patch("app.agent.llm.chat_completion", side_effect=fake_completion):
            result = agent.chat("session-path-auth", f"帮我看这个文件 {outside_file}")

        self.assertEqual(result["reply"], "read it")
        self.assertTrue(any("[本轮用户显式授权路径]" in m.get("content", "") for m in captured_messages))

    def test_sanitize_tool_history_drops_orphan_tool_messages(self):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "missing", "name": "remember", "content": "{}"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "list_memories", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call-1", "name": "list_memories", "content": "{}"},
            {"role": "assistant", "content": "done"},
        ]

        sanitized = agent._sanitize_tool_history(history)

        self.assertEqual([m["role"] for m in sanitized], ["user", "assistant", "tool", "assistant"])
        self.assertNotIn("missing", [m.get("tool_call_id") for m in sanitized])

    def test_sanitize_tool_history_drops_tool_block_without_required_reasoning_content(self):
        history = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "fetch_webpage", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call-1", "name": "fetch_webpage", "content": "{}"},
            {"role": "assistant", "content": "done"},
        ]

        sanitized = agent._sanitize_tool_history(history, require_reasoning_content=True)

        self.assertEqual(sanitized, [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "done"},
        ])

    def test_sanitize_tool_history_keeps_tool_block_with_reasoning_content(self):
        history = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "thinking",
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "fetch_webpage", "arguments": "{}"},
                }],
            },
            {"role": "tool", "tool_call_id": "call-1", "name": "fetch_webpage", "content": "{}"},
            {"role": "assistant", "content": "done"},
        ]

        sanitized = agent._sanitize_tool_history(history, require_reasoning_content=True)

        self.assertEqual([m["role"] for m in sanitized], ["user", "assistant", "tool", "assistant"])
        self.assertEqual(sanitized[1]["reasoning_content"], "thinking")

    def test_extracts_dsml_tool_calls_from_text_response(self):
        content = (
            "好的，我来查一下。\n\n"
            "<｜｜DSML｜｜tool_calls>\n"
            "<｜｜DSML｜｜invoke name=\"fetch_webpage\">\n"
            "<｜｜DSML｜｜parameter name=\"url\" string=\"true\">https://petrolmate.com.au/city/vic/melbourne</｜｜DSML｜｜parameter>\n"
            "<｜｜DSML｜｜parameter name=\"max_chars\" string=\"false\">5000</｜｜DSML｜｜parameter>\n"
            "</｜｜DSML｜｜invoke>\n"
            "</｜｜DSML｜｜tool_calls>"
        )

        calls = agent._extract_dsml_tool_calls(content)

        self.assertEqual(calls[0]["function"]["name"], "fetch_webpage")
        self.assertEqual(
            json.loads(calls[0]["function"]["arguments"]),
            {"url": "https://petrolmate.com.au/city/vic/melbourne", "max_chars": 5000},
        )
        self.assertEqual(agent._strip_dsml_tool_blocks(content), "好的，我来查一下。")

    def test_sanitize_tool_history_drops_plain_dsml_text_messages(self):
        history = [
            {"role": "user", "content": "对比墨尔本汽车保险"},
            {
                "role": "assistant",
                "content": (
                    "<｜｜DSML｜｜tool_calls>"
                    "<｜｜DSML｜｜invoke name=\"fetch_webpage\"></｜｜DSML｜｜invoke>"
                    "</｜｜DSML｜｜tool_calls>"
                ),
            },
            {"role": "user", "content": "hi"},
        ]

        sanitized = agent._sanitize_tool_history(history)

        self.assertEqual(sanitized, [
            {"role": "user", "content": "对比墨尔本汽车保险"},
            {"role": "user", "content": "hi"},
        ])

    def test_auto_remember_user_profile_facts(self):
        captured = []

        def fake_upsert(*args, **kwargs):
            captured.append((args, kwargs))
            return "fact-1"

        with patch("app.agent.memory.upsert_memory", side_effect=fake_upsert):
            agent._auto_remember_user_facts("我叫Alex，44岁，身高175cm，体重82kg")

        keys = [args[0] for args, _kwargs in captured]
        self.assertIn("user_name", keys)
        self.assertIn("user_age", keys)
        self.assertIn("user_height_cm", keys)
        self.assertIn("user_weight_kg", keys)

    def test_memory_context_includes_personal_facts_for_bmi_question(self):
        with patch("app.agent.memory.recall", return_value=[]):
            with patch("app.agent.memory.list_memories", return_value=[
                {"id": "1", "title": "user_age", "content": "用户年龄是 44 岁", "tags": "personal,user_info,age"},
                {"id": "2", "title": "user_height_cm", "content": "用户身高是 175 cm", "tags": "personal,user_info,bmi"},
            ]):
                context = agent._memory_context("我的年龄和 BMI 是多少？")

        self.assertIn("用户年龄是 44 岁", context)
        self.assertIn("用户身高是 175 cm", context)
        self.assertIn("BMI", context)

    def test_remember_artifact_path_for_bookkeeper_file(self):
        with patch("app.agent.memory.upsert_memory", return_value="fact-1") as upsert:
            agent._remember_artifact_path(
                "帮我创建一个 bookkeeper 文件帮我记账",
                "write_file",
                {"path": "/tmp/bookkeeper.html", "filename": "bookkeeper.html"},
            )

        self.assertEqual(upsert.call_args.args[0], "artifact_bookkeeper_path")
        self.assertIn("/tmp/bookkeeper.html", upsert.call_args.args[1])

    def test_remember_artifact_path_for_fitness_file(self):
        with patch("app.agent.memory.upsert_memory", return_value="fact-1") as upsert:
            agent._remember_artifact_path(
                "帮我创建一个 fit 文件记录体重和饮食",
                "write_file",
                {"path": "/tmp/fit/index.html", "filename": "index.html"},
            )

        self.assertEqual(upsert.call_args.args[0], "artifact_fitness_path")
        self.assertIn("/tmp/fit/index.html", upsert.call_args.args[1])

    def test_memory_context_includes_fitness_artifact_for_weight_log(self):
        with patch("app.agent.memory.recall", return_value=[]):
            with patch("app.agent.memory.list_memories", return_value=[
                {
                    "id": "fit-1",
                    "title": "artifact_fitness_path",
                    "content": "用户的 fit/fitness/体重饮食记录文件路径是 /tmp/fit/index.html",
                    "tags": "artifact,file,fit,fitness,体重,饮食,健身",
                },
            ]):
                context = agent._memory_context("帮我记录今天体重 70kg")

        self.assertIn("/tmp/fit/index.html", context)
        self.assertIn("先 read_file 再 write_file", context)

    def test_persistent_record_write_context_requires_target_file_update(self):
        with patch("app.agent.memory.list_memories", return_value=[
            {
                "id": "fit-1",
                "title": "artifact_fitness_path",
                "content": "用户的 fit/fitness/体重饮食记录文件路径是 /tmp/fit/fit.html",
                "tags": "artifact,file,fit,fitness,体重",
            },
        ]):
            context = agent._persistent_record_write_context("帮我记录今天体重70kg")

        self.assertIn("/tmp/fit/fit.html", context)
        self.assertIn("必须调用 read_file", context)
        self.assertIn("唯一可信数据源", context)
        self.assertIn("禁止用旧/空 localStorage 覆盖文件内数据", context)
        self.assertIn("write_file 成功后", context)

    def test_file_destination_context_requires_desktop_folder_question(self):
        context = agent._file_destination_context("帮我建立一个文件", explicit_paths=[])

        self.assertIn("必须先确认文件保存位置", context)
        self.assertIn("要不要在桌面新建一个文件夹", context)
        self.assertIn("不要调用 write_file", context)

    def test_file_destination_context_skips_explicit_workspace(self):
        context = agent._file_destination_context("帮我在当前文件夹建立一个文件", explicit_paths=[])

        self.assertEqual(context, "")

    def test_record_write_task_does_not_accept_reply_without_write_file(self):
        first_reply_without_tools = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "已记录。"}}],
        }
        write_call = {
            "model": "test-model",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call-write",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({
                                "path": "/tmp/fit/fit.html",
                                "content": "<html>70kg</html>",
                                "overwrite": True,
                            }),
                        },
                    }],
                }
            }],
        }
        final_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "已写入 /tmp/fit/fit.html"}}],
        }
        captured_messages = []

        def fake_completion(*, messages, tools=None, **_kwargs):
            captured_messages.extend(messages)
            has_tool_result = any(m.get("role") == "tool" for m in messages)
            if has_tool_result:
                return final_response
            if any("[必须完成文件写入]" in m.get("content", "") for m in messages):
                return write_call
            return first_reply_without_tools

        with patch("app.agent.memory.recall", return_value=[]):
            with patch("app.agent.memory.list_memories", return_value=[
                {
                    "id": "fit-1",
                    "title": "artifact_fitness_path",
                    "content": "用户的 fit/fitness/体重饮食记录文件路径是 /tmp/fit/fit.html",
                    "tags": "artifact,file,fit,fitness,体重",
                },
            ]):
                with patch("app.agent.llm.chat_completion", side_effect=fake_completion):
                    with patch("app.agent.tools.run_tool", return_value={"path": "/tmp/fit/fit.html", "ok": True}) as run_tool:
                        result = agent.chat("session-force-write", "帮我记录今天体重70kg")

        self.assertEqual(result["reply"], "已写入 /tmp/fit/fit.html")
        self.assertEqual(run_tool.call_args.args[0], "write_file")
        self.assertTrue(any("[必须完成文件写入]" in m.get("content", "") for m in captured_messages))


class MaxIterationsForTests(unittest.TestCase):
    """Unit tests for agent._max_iterations_for — the Task Mode soft-cap helper."""

    def setUp(self):
        self.old_normal = settings.max_tool_iterations
        self.old_task = settings.max_task_tool_iterations
        settings.max_tool_iterations = 8
        settings.max_task_tool_iterations = 25

    def tearDown(self):
        settings.max_tool_iterations = self.old_normal
        settings.max_task_tool_iterations = self.old_task

    def test_normal_chat_uses_normal_cap(self):
        self.assertEqual(agent._max_iterations_for("解释一下这段代码", False), 8)

    def test_task_mode_raises_cap(self):
        self.assertEqual(agent._max_iterations_for("帮我研究并对比三个方案", True), 25)

    def test_shopping_keeps_small_budget_outside_task_mode(self):
        self.assertEqual(agent._max_iterations_for("帮我买一个便宜的显示器报价", False), 4)

    def test_shopping_keeps_small_budget_inside_task_mode(self):
        # Shopping budget is a deliberate cost guard and must not be expanded
        # by Task Mode, otherwise a single shopping research could blow up cost.
        self.assertEqual(agent._max_iterations_for("帮我买一个便宜的显示器报价", True), 4)

    def test_task_cap_never_below_normal_cap(self):
        # Misconfigured: task cap accidentally lower than normal cap.
        # Helper guards with max(...) so Task Mode never gets fewer iterations than normal.
        settings.max_task_tool_iterations = 3
        self.assertEqual(agent._max_iterations_for("研究一下", True), 8)


if __name__ == "__main__":
    unittest.main()
