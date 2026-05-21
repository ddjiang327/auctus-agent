from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.config import settings
from app import runtime_state
from app.server import app
from app.version import APP_VERSION


class ServerApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_workspace = settings.workspace_dir
        self.old_logs = settings.logs_dir
        self.old_model = settings.model
        self.old_data_dir = settings.data_dir
        self.old_route = settings.llm_route

        settings.data_dir = self.root / "data"
        settings.workspace_dir = self.root / "inputs"
        settings.logs_dir = self.root / "logs"
        settings.data_dir.mkdir()
        settings.workspace_dir.mkdir()
        settings.logs_dir.mkdir()
        settings.llm_route = "local"
        self.client = TestClient(app)

    def tearDown(self):
        runtime_state._STATE.clear()
        settings.workspace_dir = self.old_workspace
        settings.logs_dir = self.old_logs
        settings.model = self.old_model
        settings.data_dir = self.old_data_dir
        settings.llm_route = self.old_route
        self.tmp.cleanup()

    def test_upload_file_saves_to_inputs(self):
        response = self.client.post("/api/upload?filename=../notes.md", content=b"hello")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["filename"], "notes.md")
        self.assertEqual((settings.workspace_dir / "notes.md").read_bytes(), b"hello")

    def test_version_endpoint_returns_release_metadata(self):
        response = self.client.get("/api/version")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["name"], "Auctus Agent")
        self.assertEqual(data["version"], APP_VERSION)
        self.assertEqual(data["api_compat"], "v1")
        self.assertIn("download_url", data)

    def test_version_check_uses_local_metadata_without_update_url(self):
        with patch("app.server.settings.update_check_url", None):
            response = self.client.get("/api/version/check")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["version"], APP_VERSION)
        self.assertEqual(data["latest_version"], APP_VERSION)
        self.assertFalse(data["update_available"])
        self.assertEqual(data["source"], "local")

    def test_version_check_reads_remote_agent_release_metadata(self):
        remote_response = httpx.Response(
            200,
            json={
                "latest_agent_version": "99.0.0",
                "agent_download_url": "https://example.com/auctus",
            },
            request=httpx.Request("GET", "https://api.example/version"),
        )
        with patch("app.server.settings.update_check_url", "https://api.example/version"):
            with patch("app.server.httpx.get", return_value=remote_response) as get_mock:
                response = self.client.get("/api/version/check")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["latest_version"], "99.0.0")
        self.assertEqual(data["download_url"], "https://example.com/auctus")
        self.assertTrue(data["update_available"])
        self.assertEqual(data["source"], "remote")
        get_mock.assert_called_once_with("https://api.example/version", timeout=5)

    def test_upload_rejects_empty_file(self):
        response = self.client.post("/api/upload?filename=empty.md", content=b"")

        self.assertEqual(response.status_code, 400)

    def test_logs_returns_recent_json_entries(self):
        entries = [
            {"tool_name": "read_file", "status": "success"},
            {"tool_name": "remember", "status": "blocked"},
        ]
        log_path = settings.logs_dir / "tool_calls.jsonl"
        log_path.write_text("\n".join(json.dumps(x) for x in entries), encoding="utf-8")

        response = self.client.get("/api/logs?tail=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [entries[-1]])

    def test_runtime_state_tracks_permission_wait(self):
        with patch("app.server._should_request_file_permission", return_value=True):
            response = self.client.post(
                "/api/chat",
                json={"session_id": "state-session", "message": "读取 /tmp/example.md"},
            )

        self.assertEqual(response.status_code, 200)
        state = self.client.get("/api/runtime-state/state-session").json()
        self.assertEqual(state["status"], "waiting_for_user")
        self.assertEqual(state["reason"], "waiting_for_file_permission")

    def test_post_turn_review_refreshes_preferences_without_blocking_contract(self):
        class ImmediateThread:
            def __init__(self, target, *_, **__):
                self.target = target

            def start(self):
                self.target()

        with patch("app.server.threading.Thread", ImmediateThread):
            with patch("app.server.preferences.maybe_store_preference_candidate", return_value={"ok": True, "created": True}) as candidate:
                with patch("app.server.preferences.refresh_summary", return_value={"ok": True, "items": 2}) as refresh:
                    from app.server import _schedule_post_turn_review
                    _schedule_post_turn_review("review-session", "以后默认用中文", "好的", [])

        candidate.assert_called_once()
        refresh.assert_called_once()
        log_path = settings.logs_dir / "turn_reviews.jsonl"
        self.assertTrue(log_path.exists())
        item = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(item["preference_summary_items"], 2)

    def test_task_mode_strips_hidden_update_marker_from_reply(self):
        marker = '<!--TASK_UPDATE {"current_step":3,"status":"in_progress","activity_key":"advanced"}-->'
        with patch("app.server.agent.chat", return_value={"reply": f"正在收集候选方案。\n{marker}", "files": []}):
            response = self.client.post(
                "/api/chat",
                json={
                    "session_id": "task-marker-session",
                    "message": "帮我买游戏本",
                    "task_mode": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn("TASK_UPDATE", body["reply"])
        self.assertEqual(body["task"]["current_step"], 2)
        self.assertEqual(body["task"]["steps"][2]["status"], "in_progress")

    def test_tasks_endpoint_returns_task_history(self):
        with patch("app.server.task_mode.list_tasks", return_value=[{"session_id": "task-1", "goal": "买游戏本"}]) as list_tasks:
            response = self.client.get("/api/tasks?limit=5")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["session_id"], "task-1")
        list_tasks.assert_called_once_with(limit=5)

    def test_evidence_endpoint_returns_session_sources(self):
        with patch("app.server.evidence.list_evidence", return_value=[{"title": "Source"}]) as list_evidence:
            response = self.client.get("/api/evidence/task-1?limit=3")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["title"], "Source")
        list_evidence.assert_called_once_with("task-1", limit=3)

    def test_new_task_mode_turn_disables_tools_for_planning(self):
        with patch("app.server.agent.chat", return_value={"reply": "先列清单", "files": []}) as chat:
            response = self.client.post(
                "/api/chat",
                json={
                    "session_id": "task-plan-only-session",
                    "message": "我想买个游戏本，在墨尔本",
                    "task_mode": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(chat.call_args.kwargs["allow_tools"])
        self.assertIn("新任务的第一轮", chat.call_args.kwargs["extra_system_context"])

    def test_quick_shopping_question_disables_tools_unless_live_lookup_requested(self):
        with patch("app.server.agent.chat", return_value={"reply": "快速建议", "files": []}) as chat:
            response = self.client.post(
                "/api/chat",
                json={
                    "session_id": "quick-shopping-session",
                    "message": "我想买个游戏本，预算2000澳币以内",
                    "task_mode": False,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(chat.call_args.kwargs["allow_tools"])

        with patch("app.server.agent.chat", return_value={"reply": "实时结果", "files": []}) as chat_live:
            response = self.client.post(
                "/api/chat",
                json={
                    "session_id": "quick-shopping-live-session",
                    "message": "帮我搜索最新价格，游戏本预算2000澳币以内",
                    "task_mode": False,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(chat_live.call_args.kwargs["allow_tools"])

    def test_model_can_be_changed_at_runtime(self):
        response = self.client.post("/api/model", json={"model": "deepseek/deepseek-chat"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["model"], "deepseek/deepseek-chat")
        self.assertEqual(settings.model, "deepseek/deepseek-chat")

    def test_memory_confirm_endpoint_maps_missing_to_404(self):
        with patch("app.server.memory.confirm_memory", return_value={"ok": False, "error": "memory not found"}):
            response = self.client.post("/api/memories/missing/confirm")

        self.assertEqual(response.status_code, 404)

    def test_route_and_api_key_endpoints(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            route_response = self.client.post("/api/route", json={"route": "byo"})
            key_response = self.client.post(
                "/api/api-keys",
                json={"provider": "deepseek", "api_key": "sk-server-secret"},
            )
            list_response = self.client.get("/api/api-keys")

        self.assertEqual(route_response.status_code, 200)
        self.assertEqual(route_response.json()["route"], "byo")
        self.assertEqual(key_response.status_code, 200)
        self.assertEqual(key_response.json()["key_hint"], "sk-s...cret")
        self.assertEqual(key_response.json()["route"], "byo")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["items"][0]["provider"], "deepseek")
        self.assertEqual(self.client.get("/api/onboarding").json()["mode"], "own_api")

    def test_api_key_validation_endpoint_checks_live_key(self):
        with patch("app.server._validate_api_key_live") as validate:
            response = self.client.post(
                "/api/api-keys/validate",
                json={
                    "provider": "deepseek",
                    "api_key": "sk-test",
                    "model": "deepseek/deepseek-chat",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        validate.assert_called_once_with(model="deepseek/deepseek-chat", api_key="sk-test")

    def test_api_key_validation_rejects_provider_model_mismatch(self):
        with patch("app.server._validate_api_key_live") as validate:
            response = self.client.post(
                "/api/api-keys/validate",
                json={
                    "provider": "anthropic",
                    "api_key": "sk-test",
                    "model": "deepseek/deepseek-chat",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("deepseek", response.json()["detail"])
        validate.assert_not_called()

    def test_api_key_validation_maps_auth_failure_to_friendly_error(self):
        with patch("app.server._validate_api_key_live", side_effect=Exception("401 Unauthorized")):
            response = self.client.post(
                "/api/api-keys/validate",
                json={
                    "provider": "deepseek",
                    "api_key": "bad-key",
                    "model": "deepseek/deepseek-chat",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("API key", response.json()["detail"])

    def test_onboarding_starts_incomplete(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            response = self.client.get("/api/onboarding")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["completed"])

    def test_onboarding_own_api_saves_key_and_candidate_memories(self):
        workspace = self.root / "workspace"
        workspace.mkdir()
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            with patch("app.server.memory.remember", side_effect=["memory-agent", "memory-user"]) as store:
                response = self.client.post(
                    "/api/onboarding",
                    json={
                        "mode": "own_api",
                        "model": "deepseek/deepseek-chat",
                        "provider": "deepseek",
                        "api_key": "sk-onboarding-secret",
                        "agent_name": "小奥",
                        "system_language": "en",
                        "user_intro": "我主要做 AI 产品。",
                        "workspace_path": str(workspace),
                        "permission_scope": "full_computer",
                        "save_profile": True,
                    },
                )
                keys = self.client.get("/api/api-keys")
                workspace_state = self.client.get("/api/workspace")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["completed"])
        self.assertEqual(body["route"], "byo")
        self.assertEqual(body["system_language"], "en")
        self.assertEqual(body["permission_scope"], "full_computer")
        self.assertEqual(body["workspace"], str(workspace.resolve()))
        self.assertEqual(workspace_state.json()["workspace"], str(workspace.resolve()))
        self.assertFalse(workspace_state.json()["uses_default"])
        self.assertEqual(body["candidate_memory_ids"], ["memory-agent", "memory-user"])
        self.assertEqual(keys.json()["items"][0]["provider"], "deepseek")
        self.assertEqual(store.call_count, 2)

    def test_onboarding_saves_persona_and_memory_preference(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            with patch("app.server.memory.remember", return_value="memory-persona") as remember:
                response = self.client.post(
                    "/api/onboarding",
                    json={
                        "mode": "own_api",
                        "model": "deepseek/deepseek-chat",
                        "provider": "deepseek",
                        "api_key": "sk-onboarding-secret",
                        "persona": "cool_sister",
                        "save_profile": True,
                    },
                )
                state = self.client.get("/api/onboarding")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["persona"], "cool_sister")
        self.assertEqual(state.json()["persona"], "cool_sister")
        remember.assert_called_once()
        _, kwargs = remember.call_args
        self.assertEqual(kwargs["key"], "Agent persona")
        self.assertIn("高冷御姐", kwargs["value"])

    def test_language_can_be_changed_from_settings(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            response = self.client.post("/api/language", json={"language": "en"})
            state = self.client.get("/api/language")
            onboarding = self.client.get("/api/onboarding")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["language"], "en")
        self.assertEqual(state.json()["language"], "en")
        self.assertEqual(onboarding.json()["system_language"], "en")

    def test_language_rejects_unknown_by_falling_back_to_english(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            response = self.client.post("/api/language", json={"language": "fr"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["language"], "en")

    def test_hosted_region_can_be_changed_from_settings(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            response = self.client.post("/api/hosted-region", json={"region": "cn"})
            state = self.client.get("/api/hosted-region")
            onboarding = self.client.get("/api/onboarding")
            route = self.client.get("/api/route")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["region"], "cn")
        self.assertEqual(state.json()["region"], "cn")
        self.assertEqual(onboarding.json()["hosted_region"], "cn")
        self.assertEqual(route.json()["hosted_region"], "cn")

    def test_hosted_region_unknown_falls_back_to_auto(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            response = self.client.post("/api/hosted-region", json={"region": "mars"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["region"], "auto")

    def test_hosted_region_detects_china_from_timezone(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            response = self.client.post(
                "/api/hosted-region/detect",
                json={"timezone": "Asia/Shanghai", "locale": "zh-CN", "languages": ["zh-CN"]},
            )
            state = self.client.get("/api/hosted-region")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["region"], "cn")
        self.assertEqual(state.json()["region"], "cn")

    def test_hosted_region_detects_global_by_default(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            response = self.client.post(
                "/api/hosted-region/detect",
                json={"timezone": "America/Los_Angeles", "locale": "en-US", "languages": ["en-US"]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["region"], "global")

    def test_permission_scope_can_be_changed_from_settings(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            response = self.client.post("/api/permission-scope", json={"scope": "full_computer"})
            state = self.client.get("/api/permission-scope")
            onboarding = self.client.get("/api/onboarding")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scope"], "full_computer")
        self.assertEqual(state.json()["scope"], "full_computer")
        self.assertEqual(onboarding.json()["permission_scope"], "full_computer")

    def test_fresh_install_defaults_to_full_computer_and_terminal_enabled(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            onboarding = self.client.get("/api/onboarding")
            permission = self.client.get("/api/permission-scope")
            terminal = self.client.get("/api/terminal-access")

        self.assertEqual(onboarding.json()["permission_scope"], "full_computer")
        self.assertEqual(onboarding.json()["terminal_access"], "enabled")
        self.assertEqual(permission.json()["scope"], "full_computer")
        self.assertEqual(terminal.json()["access"], "enabled")

    def test_permission_scope_unknown_falls_back_to_default_full_computer(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            response = self.client.post("/api/permission-scope", json={"scope": "root"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scope"], "full_computer")

    def test_terminal_access_can_be_changed_from_settings(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            response = self.client.post("/api/terminal-access", json={"access": "enabled"})
            state = self.client.get("/api/terminal-access")
            onboarding = self.client.get("/api/onboarding")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["access"], "enabled")
        self.assertEqual(state.json()["access"], "enabled")
        self.assertEqual(onboarding.json()["terminal_access"], "enabled")

    def test_terminal_access_unknown_falls_back_to_default_enabled(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            response = self.client.post("/api/terminal-access", json={"access": "root"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["access"], "enabled")

    def test_workspace_can_be_authorized(self):
        target = self.root / "authorized"
        target.mkdir()
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            response = self.client.post("/api/workspace", json={"path": str(target)})
            state = self.client.get("/api/workspace")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["workspace"], str(target.resolve()))
        self.assertEqual(state.json()["workspace"], str(target.resolve()))
        self.assertEqual(settings.workspace_dir, target.resolve())

    def test_workspace_rejects_missing_folder(self):
        response = self.client.post("/api/workspace", json={"path": str(self.root / "missing")})

        self.assertEqual(response.status_code, 400)

    def test_folder_browser_lists_child_folders(self):
        child = self.root / "Documents"
        child.mkdir()
        (self.root / "file.txt").write_text("not a folder", encoding="utf-8")

        response = self.client.get(f"/api/folders?path={self.root}")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["path"], str(self.root.resolve()))
        self.assertIn({"name": "Documents", "path": str(child.resolve()), "readable": True}, body["items"])
        self.assertNotIn("file.txt", [item["name"] for item in body["items"]])

    def test_folder_browser_rejects_missing_folder(self):
        response = self.client.get(f"/api/folders?path={self.root / 'missing'}")

        self.assertEqual(response.status_code, 400)

    def test_onboarding_hosted_api_sets_proxy_route_and_login(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            with patch("app.server.settings.proxy_base_url", "https://relay.example"):
                response = self.client.post(
                    "/api/onboarding",
                    json={
                        "mode": "hosted_api",
                        "model": "claude-sonnet-4-5",
                        "hosted_email": "user@example.com",
                        "hosted_region": "global",
                    },
                )
                state = self.client.get("/api/onboarding")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["route"], "proxy")
        self.assertEqual(response.json()["hosted_region"], "global")
        self.assertTrue(state.json()["completed"])
        self.assertEqual(state.json()["hosted_account"]["email"], "user@example.com")
        self.assertEqual(state.json()["hosted_account"]["region"], "global")

    def test_onboarding_hosted_api_without_proxy_uses_local_preview_route(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            with patch("app.server.settings.proxy_base_url", None):
                response = self.client.post(
                    "/api/onboarding",
                    json={
                        "mode": "hosted_api",
                        "model": "deepseek/deepseek-chat",
                        "hosted_email": "user@example.com",
                        "hosted_region": "cn",
                    },
                )
                state = self.client.get("/api/onboarding")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["route"], "local")
        self.assertEqual(response.json()["hosted_region"], "cn")
        self.assertEqual(state.json()["route"], "local")
        self.assertEqual(state.json()["hosted_account"]["email"], "user@example.com")
        self.assertEqual(state.json()["hosted_account"]["region"], "cn")

    def test_chat_runtime_error_returns_json_detail(self):
        with patch("app.server.agent.chat", side_effect=RuntimeError("BYO route requires a saved deepseek API key.")):
            response = self.client.post(
                "/api/chat",
                json={"session_id": "s1", "message": "hi"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("BYO Key", response.json()["detail"])

    def test_chat_auth_error_returns_json_detail(self):
        with patch("app.server.agent.chat", side_effect=Exception("401 Unauthorized")):
            response = self.client.post(
                "/api/chat",
                json={"session_id": "s1", "message": "hi"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("API key", response.json()["detail"])

    def test_chat_auth_error_uses_english_when_system_language_is_english(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            self.client.post("/api/language", json={"language": "en"})
            with patch("app.server.agent.chat", side_effect=Exception("401 Unauthorized")):
                response = self.client.post(
                    "/api/chat",
                    json={"session_id": "s1", "message": "hi"},
                )

        self.assertEqual(response.status_code, 503)
        self.assertIn("Model API authentication failed", response.json()["detail"])

    def test_chat_requests_terminal_permission_when_disabled(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            self.client.post("/api/terminal-access", json={"access": "disabled"})
            with patch("app.server.agent.chat") as chat_mock:
                response = self.client.post(
                    "/api/chat",
                    json={"session_id": "s1", "message": "帮我执行 npm test"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["permission_request"]["type"], "terminal")
        chat_mock.assert_not_called()

    def test_chat_requests_terminal_permission_for_opening_local_html(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            self.client.post("/api/terminal-access", json={"access": "disabled"})
            with patch("app.server.agent.chat") as chat_mock:
                response = self.client.post(
                    "/api/chat",
                    json={"session_id": "s1", "message": "帮我打开物流追踪.html"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["permission_request"]["type"], "terminal")
        chat_mock.assert_not_called()

    def test_chat_terminal_permission_once_does_not_save_setting(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            self.client.post("/api/terminal-access", json={"access": "disabled"})
            with patch("app.server.agent.chat", return_value={"reply": "done", "files": []}) as chat_mock:
                response = self.client.post(
                    "/api/chat",
                    json={"session_id": "s1", "message": "帮我执行 pwd", "terminal_permission": "once"},
                )
            state = self.client.get("/api/terminal-access")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reply"], "done")
        self.assertEqual(state.json()["access"], "disabled")
        chat_mock.assert_called_once()

    def test_chat_terminal_permission_always_saves_setting(self):
        with patch("app.accounting.db_path", return_value=settings.data_dir / "secretary.db"):
            with patch("app.server.agent.chat", return_value={"reply": "done", "files": []}):
                response = self.client.post(
                    "/api/chat",
                    json={"session_id": "s1", "message": "帮我执行 pwd", "terminal_permission": "always"},
                )
            state = self.client.get("/api/terminal-access")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reply"], "done")
        self.assertEqual(state.json()["access"], "enabled")

    def test_public_pages_render(self):
        landing = self.client.get("/landing")
        privacy = self.client.get("/privacy")
        terms = self.client.get("/terms")
        billing = self.client.get("/billing")
        app_page = self.client.get("/")

        self.assertEqual(landing.status_code, 200)
        self.assertIn("Auctus Agent", landing.text)
        self.assertIn("本地优先", landing.text)
        self.assertEqual(privacy.status_code, 200)
        self.assertIn("隐私政策", privacy.text)
        self.assertEqual(terms.status_code, 200)
        self.assertIn("用户协议", terms.text)
        self.assertEqual(billing.status_code, 200)
        self.assertIn("充值与计费", billing.text)
        self.assertEqual(app_page.status_code, 200)
        self.assertIn("System Language", app_page.text)
        self.assertIn("国内阿里云", app_page.text)
        self.assertIn("整台电脑", app_page.text)
        self.assertIn("终端命令权限", app_page.text)
        self.assertIn("验证", app_page.text)
        self.assertIn("你好，我在这里", app_page.text)


if __name__ == "__main__":
    unittest.main()
