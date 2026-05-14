from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app import agent
from app.config import settings


class AgentOutputIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_output = settings.output_dir
        self.old_logs = settings.logs_dir
        self.old_workspace = settings.workspace_dir
        self.old_iterations = settings.max_tool_iterations

        settings.output_dir = self.root / "outputs"
        settings.logs_dir = self.root / "logs"
        settings.workspace_dir = self.root / "inputs"
        settings.output_dir.mkdir()
        settings.logs_dir.mkdir()
        settings.workspace_dir.mkdir()
        settings.max_tool_iterations = 3

    def tearDown(self):
        settings.output_dir = self.old_output
        settings.logs_dir = self.old_logs
        settings.workspace_dir = self.old_workspace
        settings.max_tool_iterations = self.old_iterations
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

        def fake_completion(*, tools=None, **_kwargs):
            if tools is None:
                return summary_response
            return tool_call_response

        settings.max_tool_iterations = 1
        with patch("app.agent.llm.chat_completion", side_effect=fake_completion):
            with patch("app.agent.tools.run_tool", return_value={"url": "https://example.com", "content": "partial"}):
                result = agent.chat("session-fallback", "查网页")

        self.assertEqual(result["reply"], "已整理部分网页结果")

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
