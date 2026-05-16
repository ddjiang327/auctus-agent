from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import httpx

from app import accounting, llm
from app.config import settings


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.old_route = settings.llm_route
        self.old_data_dir = settings.data_dir

    def tearDown(self):
        settings.llm_route = self.old_route
        settings.data_dir = self.old_data_dir

    def test_accounting_creates_phase_6_tables_and_records_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "secretary.db"

            with accounting.usage_context(user_id="local", session_id="s1", route="local", tool_name="summarize_text"):
                row_id = accounting.record_model_call(
                    model="test-model",
                    usage={"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
                    cost=0.12,
                    path=db_path,
                )
            result = accounting.usage_summary(path=db_path)

        self.assertIsNotNone(row_id)
        self.assertEqual(result["calls"], 1)
        self.assertEqual(result["total_tokens"], 7)
        self.assertAlmostEqual(result["cost"], 0.12)
        self.assertEqual(result["by_model"]["test-model"]["calls"], 1)
        self.assertEqual(result["by_tool"]["summarize_text"]["total_tokens"], 7)
        self.assertEqual(result["by_route"]["local"]["total_tokens"], 7)

    def test_llm_chat_completion_records_usage(self):
        class FakeResponse:
            def model_dump(self):
                return {
                    "model": "test-model",
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                }

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "secretary.db"
            with patch("app.accounting.db_path", return_value=db_path):
                with patch("app.llm.litellm.completion", return_value=FakeResponse()):
                    with accounting.usage_context(user_id="local", session_id="s2", route="local"):
                        resp = llm.chat_completion(messages=[{"role": "user", "content": "hi"}])
                result = accounting.usage_summary(path=db_path)

        self.assertEqual(resp["choices"][0]["message"]["content"], "ok")
        self.assertEqual(result["calls"], 1)
        self.assertEqual(result["prompt_tokens"], 1)
        self.assertEqual(result["completion_tokens"], 2)

    def test_api_key_is_encrypted_and_decrypted(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings.data_dir = Path(tmp)
            db_path = Path(tmp) / "secretary.db"
            item = accounting.set_api_key("deepseek", "sk-test-secret", path=db_path)

            saved = accounting.get_api_key("deepseek", path=db_path)
            raw_db = db_path.read_text(encoding="latin1", errors="ignore")

        self.assertEqual(saved, "sk-test-secret")
        self.assertEqual(item["key_hint"], "sk-t...cret")
        self.assertNotIn("sk-test-secret", raw_db)

    def test_byo_route_passes_saved_key_to_litellm(self):
        class FakeResponse:
            def model_dump(self):
                return {
                    "model": "deepseek/deepseek-chat",
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                }

        with tempfile.TemporaryDirectory() as tmp:
            settings.data_dir = Path(tmp)
            db_path = Path(tmp) / "secretary.db"
            settings.llm_route = "byo"
            accounting.set_api_key("deepseek", "sk-byo-secret", path=db_path)
            with patch("app.accounting.db_path", return_value=db_path):
                with patch("app.llm.settings.model", "deepseek/deepseek-chat"):
                    with patch("app.llm.litellm.completion", return_value=FakeResponse()) as completion:
                        llm.chat_completion(messages=[{"role": "user", "content": "hi"}])

        self.assertEqual(completion.call_args.kwargs["api_key"], "sk-byo-secret")

    def test_proxy_route_uses_openai_compatible_model_for_relay(self):
        fake_response = {
            "model": "deepseek-chat",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            settings.data_dir = Path(tmp)
            db_path = Path(tmp) / "secretary.db"
            settings.llm_route = "proxy"
            accounting.set_api_key("auctus_hosted", "au_test", base_url="http://relay.test", path=db_path)
            with patch("app.accounting.db_path", return_value=db_path):
                with patch("app.llm.settings.model", "deepseek/deepseek-chat"):
                    with patch("app.llm.httpx.post", return_value=httpx.Response(200, json=fake_response)) as post:
                        result = llm.chat_completion(messages=[{"role": "user", "content": "hi"}])

        self.assertEqual(result["choices"][0]["message"]["content"], "ok")
        self.assertEqual(post.call_args.args[0], "http://relay.test/v1/chat/completions")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "deepseek-chat")
        self.assertEqual(post.call_args.kwargs["headers"]["x-api-key"], "au_test")

    def test_daily_cost_report_aggregates_one_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "secretary.db"
            with accounting.usage_context(user_id="local", session_id="s1", route="local", tool_name="summarize_text"):
                accounting.record_model_call(
                    model="test-model",
                    usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                    cost=0.25,
                    path=db_path,
                )

            report = accounting.daily_cost_report(day=date.today(), path=db_path)

        self.assertEqual(report["calls"], 1)
        self.assertEqual(report["total_tokens"], 15)
        self.assertAlmostEqual(report["cost"], 0.25)
        self.assertEqual(report["by_model"]["test-model"]["calls"], 1)
        self.assertEqual(report["by_tool"]["summarize_text"]["cost"], 0.25)

    def test_write_daily_cost_report_creates_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "secretary.db"
            output_dir = root / "outputs"
            with accounting.usage_context(user_id="local", session_id="s1", route="byo"):
                accounting.record_model_call(
                    model="test-model",
                    usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                    cost=0.01,
                    path=db_path,
                )

            result = accounting.write_daily_cost_report(day=date.today(), output_dir=output_dir, path=db_path)
            content = Path(result["path"]).read_text(encoding="utf-8")

        self.assertTrue(result["ok"])
        self.assertIn("Auctus Agent Cost Report", content)
        self.assertIn("| test-model | 1 | 3 | 0.010000 |", content)

    def test_setup_state_and_hosted_account_are_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "secretary.db"

            self.assertFalse(accounting.setup_completed(path=db_path))
            account = accounting.save_hosted_account("User@Example.com", path=db_path)
            state = accounting.complete_setup(path=db_path)

        self.assertTrue(account["logged_in"])
        self.assertEqual(account["email"], "user@example.com")
        self.assertEqual(account["free_tokens"], 50000)
        self.assertEqual(state["onboarding_completed"], "1")


if __name__ == "__main__":
    unittest.main()
