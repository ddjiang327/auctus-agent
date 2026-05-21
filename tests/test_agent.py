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

    def test_chat_injects_identity_memory_context_for_who_am_i(self):
        final_response = {
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": "你是 David"}}],
        }
        captured_messages = []

        def fake_completion(*, messages, tools=None, **_kwargs):
            captured_messages.extend(messages)
            return final_response

        facts = [
            {
                "title": "User profile",
                "content": "David，44岁，程序员，住在墨尔本 Oakleigh South。",
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

        self.assertEqual(result["reply"], "你是 David")
        list_memories.assert_called_once_with(confirmed=1, limit=12)
        memory_contexts = [msg["content"] for msg in captured_messages if "[相关长期记忆]" in msg.get("content", "")]
        self.assertEqual(len(memory_contexts), 1)
        self.assertIn("David", memory_contexts[0])
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


if __name__ == "__main__":
    unittest.main()
