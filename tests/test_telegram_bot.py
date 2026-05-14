from __future__ import annotations

import unittest

from app import telegram_bot
from app.config import settings


class TelegramBotHelperTests(unittest.TestCase):
    def setUp(self):
        self.old_allowed = settings.telegram_allowed_user_ids

    def tearDown(self):
        settings.telegram_allowed_user_ids = self.old_allowed

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


if __name__ == "__main__":
    unittest.main()
