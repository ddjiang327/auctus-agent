from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import tools
from app.config import settings


class ToolSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_workspace = settings.workspace_dir
        self.old_logs = settings.logs_dir
        self.old_output = settings.output_dir
        self.old_data = settings.data_dir

        settings.workspace_dir = self.root / "inputs"
        settings.logs_dir = self.root / "logs"
        settings.output_dir = self.root / "outputs"
        settings.data_dir = self.root / "data"
        settings.workspace_dir.mkdir()
        settings.logs_dir.mkdir()
        settings.output_dir.mkdir()
        settings.data_dir.mkdir()

    def tearDown(self):
        settings.workspace_dir = self.old_workspace
        settings.logs_dir = self.old_logs
        settings.output_dir = self.old_output
        settings.data_dir = self.old_data
        self.tmp.cleanup()

    def test_read_file_rejects_prefix_escape(self):
        evil_dir = self.root / "inputs_evil"
        evil_dir.mkdir()
        evil_file = evil_dir / "secret.md"
        evil_file.write_text("secret", encoding="utf-8")

        with patch("app.tools.accounting.get_setup_state", return_value={"permission_scope": "workspace"}):
            result = tools.read_file(str(evil_file))

        self.assertIn("error", result)
        self.assertIn("outside authorized workspace", result["error"])

    def test_tool_router_limits_plain_chat_tools(self):
        names = {schema["function"]["name"] for schema in tools.tool_schemas_for("你好，帮我写一段总结")}

        self.assertIn("read_file", names)
        self.assertIn("make_markdown_report", names)
        self.assertNotIn("search_web", names)
        self.assertNotIn("run_terminal_command", names)

    def test_tool_router_adds_research_tools_from_task_plan(self):
        names = {
            schema["function"]["name"]
            for schema in tools.tool_schemas_for(
                "帮我买游戏本",
                extra_system_context="结构化计划：\n- task_type: research_comparison",
            )
        }

        self.assertIn("search_web", names)
        self.assertIn("fetch_webpage", names)
        self.assertIn("browser_open", names)

    def test_tool_router_adds_subagent_for_parallel_comparison(self):
        names = {schema["function"]["name"] for schema in tools.tool_schemas_for("分别比较三家保险公司的报价和风险")}

        self.assertIn("run_parallel_subagents", names)

        sub_names = {schema["function"]["name"] for schema in tools.tool_schemas_for("比较三家保险", extra_system_context="[SUBAGENT]")}
        self.assertNotIn("run_parallel_subagents", sub_names)

    def test_tool_router_adds_event_trigger_from_intent(self):
        names = {schema["function"]["name"] for schema in tools.tool_schemas_for("监听这个网页变化 https://example.com")}

        self.assertIn("create_event_trigger", names)

    def test_run_parallel_subagents_dispatches_to_module(self):
        with patch("app.subagent.run_parallel_subagents", return_value={"ok": True, "count": 1}) as runner:
            result = tools.run_tool(
                "run_parallel_subagents",
                {"tasks": ["查 A"], "expected_output": "来源", "timeout_seconds": 20},
                user_input="分别比较三家保险公司的报价和风险",
            )

        self.assertEqual(result["count"], 1)
        runner.assert_called_once_with(tasks=["查 A"], expected_output="来源", timeout_seconds=20)

    def test_read_file_allows_chat_authorized_file_outside_workspace(self):
        outside = self.root / "Desktop" / "Screen Shot 2026-05-13 at 23.48.08 PM.md"
        outside.parent.mkdir()
        outside.write_text("visible", encoding="utf-8")

        with tools.chat_authorized_paths([outside]):
            result = tools.read_file(str(outside))

        self.assertEqual(result["filename"], outside.name)
        self.assertEqual(result["content"], "visible")

    def test_read_file_lists_chat_authorized_folder(self):
        folder = self.root / "Desktop"
        folder.mkdir(exist_ok=True)
        (folder / "note.md").write_text("hello", encoding="utf-8")

        with tools.chat_authorized_paths([folder]):
            result = tools.read_file(str(folder))

        self.assertEqual(result["type"], "directory")
        self.assertEqual(result["items"][0]["name"], "note.md")

    def test_read_file_allows_workspace_file(self):
        good_file = settings.workspace_dir / "note.md"
        good_file.write_text("hello", encoding="utf-8")

        result = tools.read_file("note.md")

        self.assertEqual(result["filename"], "note.md")
        self.assertEqual(result["content"], "hello")

    def test_read_file_allows_local_web_app_text_files(self):
        cases = {
            "data.json": '{"updated":"today"}',
            "app.js": "console.log('ok')",
            "index.html": "<html><body>物流追踪</body></html>",
            "style.css": "body { color: black; }",
        }
        for filename, content in cases.items():
            with self.subTest(filename=filename):
                target = settings.workspace_dir / filename
                target.write_text(content, encoding="utf-8")

                result = tools.read_file(filename)

                self.assertEqual(result["filename"], filename)
                self.assertIn(content[:12], result["content"])

    def test_run_tool_logs_tool_returned_errors_as_error_status(self):
        result = tools.run_tool("read_file", {"path": "missing.md"})

        self.assertIn("error", result)
        log_line = (settings.logs_dir / "tool_calls.jsonl").read_text(encoding="utf-8").strip()
        entry = json.loads(log_line)
        self.assertEqual(entry["status"], "error")

    def test_fetch_webpage_uses_fast_timeout(self):
        class FakeResponse:
            headers = {"content-type": "text/plain; charset=utf-8"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b"hello"

        with patch("app.tools.urlopen", return_value=FakeResponse()) as open_mock:
            result = tools.fetch_webpage("https://example.com")

        self.assertEqual(result["content"], "hello")
        self.assertEqual(open_mock.call_args.kwargs["timeout"], 8)

    def test_browser_open_uses_fast_domcontentloaded_default(self):
        from app import tools_browser

        class FakePage:
            url = "https://example.com"

            def goto(self, url, wait_until, timeout):
                self.goto_args = {"url": url, "wait_until": wait_until, "timeout": timeout}

            def title(self):
                return "Example"

        page = FakePage()
        old_state = dict(tools_browser._STATE)
        try:
            tools_browser._STATE.update({"ready": True, "page": page})
            result = tools_browser.browser_open("https://example.com")
        finally:
            tools_browser._STATE.clear()
            tools_browser._STATE.update(old_state)

        self.assertTrue(result["ok"])
        self.assertEqual(page.goto_args["wait_until"], "domcontentloaded")
        self.assertEqual(page.goto_args["timeout"], 12_000)

    def test_paid_search_providers_use_fast_timeout(self):
        from app import search_providers

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"results": [], "web": {"results": []}}

        with patch("app.search_providers.httpx.post", return_value=FakeResponse()) as post_mock:
            search_providers._tavily_search("laptop", 3, "key")
        with patch("app.search_providers.httpx.get", return_value=FakeResponse()) as get_mock:
            search_providers._brave_search("laptop", 3, "key")

        self.assertEqual(post_mock.call_args.kwargs["timeout"], 8)
        self.assertEqual(get_mock.call_args.kwargs["timeout"], 8)

    def test_run_tool_attaches_retry_hint_on_errors(self):
        with patch("app.tools.evolution.build_tool_retry_hint", return_value="[工具重试建议]\n- 先确认文件名"):
            result = tools.run_tool("read_file", {"path": "missing.md"}, user_input="读取文件")

        self.assertIn("error", result)
        self.assertIn("retry_hint", result)
        log_line = (settings.logs_dir / "tool_calls.jsonl").read_text(encoding="utf-8").strip()
        entry = json.loads(log_line)
        self.assertIn("retry_hint", entry["tool_output"])

    def test_remember_runs_without_extra_confirmation(self):
        with patch("app.tools.memory.remember", return_value="fact-1") as remember_mock:
            result = tools.run_tool(
                "remember",
                {"key": "k", "value": "v"},
                user_input="please proceed",
            )

        self.assertEqual(result, {"ok": True, "id": "fact-1"})
        remember_mock.assert_called_once()

    def test_high_risk_tool_runs_when_user_explicitly_asks(self):
        with patch("app.tools.memory.remember", return_value="fact-1") as remember_mock:
            result = tools.run_tool(
                "remember",
                {"key": "k", "value": "v", "type": "other", "importance": 99},
                user_input="请记住这条信息",
            )

        self.assertEqual(result, {"ok": True, "id": "fact-1"})
        remember_mock.assert_called_once()
        _, kwargs = remember_mock.call_args
        self.assertEqual(kwargs["type"], "project")
        self.assertEqual(kwargs["importance"], 5)

    def test_ui_confirmed_soft_trash_command_can_omit_confirmed_flag(self):
        original = tools._DISPATCH["run_terminal_command"]
        try:
            with tools.terminal_access_override("enabled"):
                tools._DISPATCH["run_terminal_command"] = lambda **kwargs: {"returncode": 0, "command": kwargs["command"]}
                result = tools.run_tool(
                    "run_terminal_command",
                    {"command": "mv /Users/example/Desktop/1.md ~/.Trash/"},
                    user_input="把 1.md 移到废纸篓/回收站（不要永久删除）。",
                )
        finally:
            tools._DISPATCH["run_terminal_command"] = original

        self.assertEqual(result["returncode"], 0)

    def test_unconfirmed_permanent_delete_stays_blocked(self):
        original = tools._DISPATCH["run_terminal_command"]
        try:
            with tools.terminal_access_override("enabled"):
                tools._DISPATCH["run_terminal_command"] = lambda **kwargs: {"returncode": 0}
                result = tools.run_tool(
                    "run_terminal_command",
                    {"command": "rm /Users/example/Desktop/1.md"},
                    user_input="把 1.md 移到废纸篓/回收站（不要永久删除）。",
                )
        finally:
            tools._DISPATCH["run_terminal_command"] = original

        self.assertIn("error", result)
        self.assertIn("confirmed", result["error"])

    def test_affirmative_reply_allows_safe_restore_from_trash(self):
        original = tools._DISPATCH["run_terminal_command"]
        try:
            with tools.terminal_access_override("enabled"):
                tools._DISPATCH["run_terminal_command"] = lambda **kwargs: {"returncode": 0, "command": kwargs["command"]}
                result = tools.run_tool(
                    "run_terminal_command",
                    {"command": "mv ~/.Trash/1.md ~/Desktop/"},
                    user_input="是",
                )
        finally:
            tools._DISPATCH["run_terminal_command"] = original

        self.assertEqual(result["returncode"], 0)

    def test_extract_memory_candidates_keeps_profile_and_drops_ephemeral_paths(self):
        llm_payload = [
            {
                "key": "User profile",
                "value": "Alex，44岁，程序员，住在 Example City",
                "type": "preference",
                "importance": 4,
            },
            {
                "key": "Working directory",
                "value": "工作目录是桌面上的 test/ 文件夹",
                "type": "project",
                "importance": 3,
            },
            {
                "key": "Desktop files",
                "value": "桌面上有 个人/ 文件夹和 1.md（刚删了）",
                "type": "project",
                "importance": 3,
            },
        ]
        with patch("app.tools.llm.chat_completion", return_value={"choices": [{"message": {"content": json.dumps(llm_payload)}}]}):
            with patch("app.tools.memory.store_candidate", return_value="memory-profile") as store:
                result = tools.extract_memory_candidates("profile and paths")

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["candidates"][0]["key"], "User profile")
        self.assertEqual(result["candidates"][0]["importance"], 4)
        store.assert_called_once()
        _, kwargs = store.call_args
        self.assertIn("Alex", kwargs["value"])

    def test_write_file_allows_workspace_text_file(self):
        result = tools.write_file("notes/todo.md", "hello")

        self.assertEqual(result["filename"], "todo.md")
        self.assertEqual((settings.workspace_dir / "notes" / "todo.md").read_text(encoding="utf-8"), "hello")

    def test_write_file_rejects_path_outside_workspace(self):
        with patch("app.tools.accounting.get_setup_state", return_value={"permission_scope": "workspace"}):
            result = tools.write_file(str(self.root / "outside.md"), "no")

        self.assertIn("error", result)
        self.assertIn("outside authorized workspace", result["error"])

    def test_terminal_command_can_be_disabled_in_settings(self):
        with patch("app.tools.accounting.get_setup_state", return_value={"terminal_access": "disabled"}):
            result = tools.run_terminal_command("pwd")

        self.assertIn("error", result)
        self.assertIn("terminal access is disabled", result["error"])

    def test_terminal_command_runs_with_override_inside_workspace(self):
        with tools.terminal_access_override("enabled"):
            result = tools.run_terminal_command("pwd", working_directory=str(settings.workspace_dir))

        self.assertEqual(result["returncode"], 0)
        self.assertIn(str(settings.workspace_dir), result["stdout"])

    def test_terminal_command_blocks_dangerous_patterns(self):
        with tools.terminal_access_override("enabled"):
            result = tools.run_terminal_command("sudo ls")

        self.assertIn("error", result)
        self.assertIn("blocked", result["error"])

    def test_terminal_command_rejects_working_directory_outside_workspace(self):
        outside = self.root / "Desktop"
        outside.mkdir()
        with patch("app.tools.accounting.get_setup_state", return_value={"permission_scope": "workspace"}):
            with tools.terminal_access_override("enabled"):
                result = tools.run_terminal_command("pwd", working_directory=str(outside))

        self.assertIn("error", result)
        self.assertIn("outside allowed scope", result["error"])

    def test_run_tool_terminal_command_requires_confirmed(self):
        with tools.terminal_access_override("enabled"):
            result = tools.run_tool(
                "run_terminal_command",
                {"command": "pwd", "working_directory": str(settings.workspace_dir)},
                user_input="请执行终端命令 pwd",
            )

        self.assertIn("error", result)
        self.assertIn("confirmed", result["error"])

    def test_full_computer_permission_allows_reading_outside_workspace(self):
        outside = self.root / "Desktop" / "note.md"
        outside.parent.mkdir()
        outside.write_text("visible", encoding="utf-8")
        with patch("app.tools.accounting.get_setup_state", return_value={"permission_scope": "full_computer"}):
            result = tools.read_file(str(outside))

        self.assertEqual(result["content"], "visible")

    def test_full_computer_permission_allows_writing_outside_workspace(self):
        outside = self.root / "Desktop" / "note.md"
        with patch("app.tools.accounting.get_setup_state", return_value={"permission_scope": "full_computer"}):
            result = tools.write_file(str(outside), "created")

        self.assertEqual(result["filename"], "note.md")
        self.assertEqual(outside.read_text(encoding="utf-8"), "created")

    def test_fetch_webpage_extracts_readable_html(self):
        class FakeResponse:
            headers = {"content-type": "text/html; charset=utf-8"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return b"<html><head><style>x{}</style></head><body><h1>Hello</h1><script>bad()</script><p>World</p></body></html>"

        with patch("app.tools.urlopen", return_value=FakeResponse()):
            result = tools.fetch_webpage("https://example.com")

        self.assertIn("Hello", result["content"])
        self.assertIn("World", result["content"])
        self.assertNotIn("bad", result["content"])

    def test_search_web_parses_duckduckgo_results(self):
        class FakeResponse:
            headers = {"content-type": "text/html; charset=utf-8"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return (
                    b'<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fp">'
                    b"Example Product</a>"
                    b'<a class="result__snippet">A short snippet</a>'
                )

        with patch("app.search_providers.urlopen", return_value=FakeResponse()):
            result = tools.search_web("example product")

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["title"], "Example Product")
        self.assertEqual(result["results"][0]["url"], "https://example.com/p")
        self.assertEqual(result["results"][0]["snippet"], "A short snippet")


class EmailPromptTests(unittest.TestCase):
    def test_email_tasks_prompt_includes_current_date_context(self):
        fake_response = {"choices": [{"message": {"content": "无待办事项"}}]}
        with patch("app.tools.llm.chat_completion", return_value=fake_response) as chat_mock:
            result = tools.extract_email_tasks("明天请提交报告")

        self.assertEqual(result["tasks"], "无待办事项")
        prompt = chat_mock.call_args.kwargs["messages"][0]["content"]
        self.assertIn("当前本地时间", prompt)
        self.assertIn("相对日期", prompt)


if __name__ == "__main__":
    unittest.main()
