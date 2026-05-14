from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import settings
from app import accounting, relay
from app.server import app


class RelayApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_token = settings.relay_shared_token
        self.old_data_dir = settings.data_dir
        self.old_limit = settings.relay_monthly_token_limit
        self.old_rate_limit = settings.relay_rate_limit_per_minute
        self.old_daily_cap = settings.relay_daily_cost_cap
        self.old_spike_threshold = settings.relay_spike_token_threshold
        self.old_desktop_id = settings.relay_desktop_id

        settings.relay_shared_token = "relay-test-token"
        settings.data_dir = self.root / "data"
        settings.data_dir.mkdir()
        settings.relay_monthly_token_limit = 100
        settings.relay_rate_limit_per_minute = 90
        settings.relay_daily_cost_cap = 1.0
        settings.relay_spike_token_threshold = 80
        settings.relay_desktop_id = "desktop-1"
        relay.hub.reset()
        relay._risk_alerts.clear()
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer relay-test-token"}

    def tearDown(self):
        settings.relay_shared_token = self.old_token
        settings.data_dir = self.old_data_dir
        settings.relay_monthly_token_limit = self.old_limit
        settings.relay_rate_limit_per_minute = self.old_rate_limit
        settings.relay_daily_cost_cap = self.old_daily_cap
        settings.relay_spike_token_threshold = self.old_spike_threshold
        settings.relay_desktop_id = self.old_desktop_id
        relay.hub.reset()
        relay._risk_alerts.clear()
        self.tmp.cleanup()

    def test_auth_verify_and_quota(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            auth = self.client.post("/relay/auth/verify", json={"token": "relay-test-token"})
            quota = self.client.get("/relay/quota", headers=self.headers)

        self.assertEqual(auth.status_code, 200)
        self.assertEqual(auth.json(), {"ok": True})
        self.assertEqual(quota.status_code, 200)
        self.assertEqual(quota.json()["limit_tokens"], 100)
        self.assertTrue(quota.json()["allowed"])

    def test_monthly_quota_exceeded_guides_byo_key(self):
        db_path = settings.data_dir / "secretary.db"
        with patch("app.accounting.db_path", return_value=db_path):
            accounting.record_model_call(
                model="test-model",
                usage={"prompt_tokens": 60, "completion_tokens": 50, "total_tokens": 110},
                path=db_path,
            )
            response = self.client.post(
                "/relay/llm/chat",
                headers=self.headers,
                json={"messages": [{"role": "user", "content": "hi"}]},
            )

        self.assertEqual(response.status_code, 429)
        detail = response.json()["detail"]
        self.assertIn("monthly_quota_exceeded", detail["reasons"])
        self.assertIn("agent.py route byo", detail["byo_guidance"])

    def test_rate_limit_blocks_recent_usage(self):
        db_path = settings.data_dir / "secretary.db"
        with patch("app.accounting.db_path", return_value=db_path):
            accounting.record_model_call(
                model="test-model",
                usage={"prompt_tokens": 50, "completion_tokens": 45, "total_tokens": 95},
                path=db_path,
            )
            response = self.client.get("/relay/quota", headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["allowed"])
        self.assertIn("rate_limit_exceeded", response.json()["reasons"])

    def test_daily_cost_cap_blocks_usage(self):
        db_path = settings.data_dir / "secretary.db"
        with patch("app.accounting.db_path", return_value=db_path):
            accounting.record_model_call(
                model="test-model",
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                cost=1.25,
                path=db_path,
            )
            response = self.client.get("/relay/quota", headers=self.headers)

        self.assertFalse(response.json()["allowed"])
        self.assertIn("daily_cost_cap_exceeded", response.json()["reasons"])

    def test_spike_detection_adds_alert(self):
        db_path = settings.data_dir / "secretary.db"
        with patch("app.accounting.db_path", return_value=db_path):
            accounting.record_model_call(
                model="test-model",
                usage={"prompt_tokens": 70, "completion_tokens": 15, "total_tokens": 85},
                path=db_path,
            )
            response = self.client.get("/relay/risk", headers=self.headers)

        self.assertFalse(response.json()["quota"]["allowed"])
        self.assertIn("spike_detected", response.json()["quota"]["reasons"])
        self.assertEqual(response.json()["alerts"][-1]["kind"], "spike_detected")

    def test_relay_rejects_bad_token(self):
        response = self.client.get("/relay/quota", headers={"Authorization": "Bearer wrong"})

        self.assertEqual(response.status_code, 401)

    def test_llm_proxy_calls_model_layer(self):
        fake = {"model": "test-model", "choices": [{"message": {"role": "assistant", "content": "ok"}}], "usage": {}}
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            with patch("app.relay.llm.chat_completion", return_value=fake) as chat:
                response = self.client.post(
                    "/relay/llm/chat",
                    headers=self.headers,
                    json={"messages": [{"role": "user", "content": "hi"}], "model": "test-model"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "ok")
        chat.assert_called_once()

    def test_tunnel_hub_delivers_command_to_connected_desktop(self):
        with self.client.websocket_connect("/relay/tunnel/desktop-1?token=relay-test-token") as ws:
            response = self.client.post(
                "/relay/tunnel/desktop-1/commands",
                headers=self.headers,
                json={"type": "run_task", "payload": {"message": "hello"}},
            )
            command = ws.receive_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["delivered"], 1)
        self.assertEqual(command["type"], "run_task")
        self.assertEqual(command["payload"]["message"], "hello")

    def test_telegram_webhook_queues_for_desktop(self):
        response = self.client.post(
            "/relay/telegram/webhook",
            headers=self.headers,
            json={"message": {"text": "hello"}},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertIn("command_id", response.json())


if __name__ == "__main__":
    unittest.main()
