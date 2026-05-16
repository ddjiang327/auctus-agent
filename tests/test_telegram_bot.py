from __future__ import annotations

import unittest
from unittest.mock import patch

from app import server, telegram_bot
from app.config import settings


class TelegramBotHelperTests(unittest.TestCase):
    def setUp(self):
        self.old_allowed = settings.telegram_allowed_user_ids
        telegram_bot._PENDING_PERMISSIONS.clear()

    def tearDown(self):
        settings.telegram_allowed_user_ids = self.old_allowed
        telegram_bot._PENDING_PERMISSIONS.clear()

    def test_safe_input_filename_strips_path_and_replaces_unsafe_chars(self):
        self.assertEqual(
            telegram_bot._safe_input_filename("../bad/name?.md"),
            "name_.md",
        )

    def test_task_for_uploaded_file_uses_caption(self):
        prompt = telegram_bot._task_for_uploaded_file("prd.md", "生成总结")

        self.assertIn("prd.md", prompt)
        self.assertIn("生成总结", prompt)

    def test_task_for_uploaded_file_has_default_task(self):
        prompt = telegram_bot._task_for_uploaded_file("prd.md", "")

        self.assertIn("概括主要内容", prompt)

    def test_auth_accepts_configured_user_only(self):
        settings.telegram_allowed_user_ids = "100, 200"

        self.assertTrue(telegram_bot._auth_ok(100))
        self.assertFalse(telegram_bot._auth_ok(300))

    def test_permission_choice_parses_telegram_reply(self):
        self.assertEqual(telegram_bot._permission_choice("允许一次"), "once")
        self.assertEqual(telegram_bot._permission_choice("始终允许"), "always")
        self.assertEqual(telegram_bot._permission_choice("拒绝"), "no")

    def test_run_chat_stores_pending_permission_request(self):
        out = server.ChatOut(
            session_id="tg-100",
            reply="",
            permission_request={"type": "terminal", "message": "需要执行终端命令。"},
        )
        with patch("app.telegram_bot.server.chat", return_value=out):
            result = telegram_bot._run_chat_for_telegram(100, "帮我执行 pwd")

        self.assertIn("允许一次", result["reply"])
        self.assertEqual(telegram_bot._PENDING_PERMISSIONS[100]["type"], "terminal")
        self.assertEqual(telegram_bot._PENDING_PERMISSIONS[100]["message"], "帮我执行 pwd")

    def test_run_chat_replays_pending_task_with_permission_choice(self):
        telegram_bot._PENDING_PERMISSIONS[100] = {"message": "帮我执行 pwd", "type": "terminal"}
        out = server.ChatOut(session_id="tg-100", reply="done", files=[])

        with patch("app.telegram_bot.server.chat", return_value=out) as chat:
            result = telegram_bot._run_chat_for_telegram(100, "允许一次", "once")

        self.assertEqual(result["reply"], "done")
        self.assertNotIn(100, telegram_bot._PENDING_PERMISSIONS)
        payload = chat.call_args.args[0]
        self.assertEqual(payload.message, "帮我执行 pwd")
        self.assertEqual(payload.terminal_permission, "once")


if __name__ == "__main__":
    unittest.main()
