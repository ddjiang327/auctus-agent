from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import evolution
from app.config import settings


class EvolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old_data_dir = settings.data_dir
        self.old_logs_dir = settings.logs_dir
        settings.data_dir = self.root / "data"
        settings.logs_dir = self.root / "logs"
        settings.data_dir.mkdir()
        settings.logs_dir.mkdir()

    def tearDown(self):
        settings.data_dir = self.old_data_dir
        settings.logs_dir = self.old_logs_dir
        self.tmp.cleanup()

    def test_store_list_and_reject_candidate(self):
        db_path = settings.data_dir / "secretary.db"
        candidate_id = evolution.store_candidate(
            type="preference",
            title="中文简洁回复",
            content="用户偏好中文、简洁、直接的工程汇报。",
            confidence=0.8,
            path=db_path,
        )

        items = evolution.list_candidates(path=db_path)
        rejected = evolution.reject_candidate(candidate_id, path=db_path)
        pending = evolution.list_candidates(path=db_path)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "中文简洁回复")
        self.assertTrue(rejected["ok"])
        self.assertEqual(pending, [])

    def test_scan_parses_llm_candidates(self):
        db_path = settings.data_dir / "secretary.db"
        _insert_message(db_path, "user", "以后回答请简洁一点")
        fake_response = {
            "choices": [{
                "message": {
                    "content": json.dumps([
                        {
                            "type": "preference",
                            "title": "简洁回复",
                            "content": "用户偏好简洁、直接的回复。",
                            "evidence": "用户明确要求以后回答简洁一点。",
                            "confidence": 0.9,
                        }
                    ], ensure_ascii=False)
                }
            }]
        }

        with patch("app.evolution.llm.chat_completion", return_value=fake_response):
            result = evolution.scan(path=db_path)
        items = evolution.list_candidates(path=db_path)

        self.assertEqual(result["candidates_added"], 1)
        self.assertEqual(items[0]["type"], "preference")
        self.assertEqual(items[0]["confidence"], 0.9)

    def test_apply_candidate_writes_memory_and_marks_applied(self):
        db_path = settings.data_dir / "secretary.db"
        candidate_id = evolution.store_candidate(
            type="prompt_rule",
            title="报告输出路径",
            content="生成文件后要清楚列出输出路径。",
            confidence=0.7,
            path=db_path,
        )

        with patch("app.evolution.memory.remember", return_value="memory-1") as remember:
            result = evolution.apply_candidate(candidate_id, path=db_path)
        items = evolution.list_candidates(status="applied", path=db_path)

        self.assertTrue(result["ok"])
        self.assertEqual(result["memory_id"], "memory-1")
        self.assertEqual(items[0]["memory_id"], "memory-1")
        _, kwargs = remember.call_args
        self.assertEqual(kwargs["type"], "rule")
        self.assertEqual(kwargs["source"], "evolution")

    def test_build_runtime_context_includes_rules_and_matching_workflows(self):
        db_path = settings.data_dir / "secretary.db"
        rule_id = evolution.store_candidate(
            type="prompt_rule",
            title="输出路径",
            content="生成文件后要列出用户可打开的路径。",
            confidence=0.8,
            path=db_path,
        )
        workflow_id = evolution.store_candidate(
            type="workflow",
            title="PRD 分析流程",
            content="触发条件：用户要求分析 PRD。步骤：先总结，再提功能表，最后生成测试用例。",
            confidence=0.9,
            path=db_path,
        )
        ignored_id = evolution.store_candidate(
            type="workflow",
            title="发票归档流程",
            content="触发条件：用户上传发票。步骤：提取金额并归档。",
            confidence=0.9,
            path=db_path,
        )

        with patch("app.evolution.memory.remember", return_value="memory-rule"):
            evolution.apply_candidate(rule_id, path=db_path)
        with patch("app.evolution.memory.remember", return_value="memory-workflow"):
            evolution.apply_candidate(workflow_id, path=db_path)
        with patch("app.evolution.memory.remember", return_value="memory-ignored"):
            evolution.apply_candidate(ignored_id, path=db_path)

        context = evolution.build_runtime_context("请分析这个 PRD 并生成测试用例", path=db_path)

        self.assertIn("[自我学习规则]", context)
        self.assertIn("输出路径", context)
        self.assertIn("PRD 分析流程", context)
        self.assertNotIn("发票归档流程", context)

    def test_build_tool_retry_hint_matches_error_patterns(self):
        db_path = settings.data_dir / "secretary.db"
        pattern_id = evolution.store_candidate(
            type="error_pattern",
            title="read_file 缺文件",
            content="read_file 返回 file not found 时，先调用 list_outputs 或确认 inputs 文件名。",
            evidence="多次读取不存在文件失败。",
            confidence=0.85,
            path=db_path,
        )
        unrelated_id = evolution.store_candidate(
            type="tool_strategy",
            title="邮件回复策略",
            content="处理邮件时先总结再起草回复。",
            confidence=0.8,
            path=db_path,
        )

        with patch("app.evolution.memory.remember", return_value="memory-pattern"):
            evolution.apply_candidate(pattern_id, path=db_path)
        with patch("app.evolution.memory.remember", return_value="memory-unrelated"):
            evolution.apply_candidate(unrelated_id, path=db_path)

        hint = evolution.build_tool_retry_hint(
            tool_name="read_file",
            error="file not found: prd.md",
            user_input="读取 PRD 文件",
            path=db_path,
        )

        self.assertIn("[工具重试建议]", hint)
        self.assertIn("read_file 缺文件", hint)
        self.assertNotIn("邮件回复策略", hint)

    def test_disable_candidate_removes_learning_from_runtime_context(self):
        db_path = settings.data_dir / "secretary.db"
        candidate_id = evolution.store_candidate(
            type="prompt_rule",
            title="输出路径",
            content="生成文件后要列出输出路径。",
            confidence=0.8,
            path=db_path,
        )

        with patch("app.evolution.memory.remember", return_value="memory-1"):
            evolution.apply_candidate(candidate_id, path=db_path)
        before = evolution.build_runtime_context("生成报告", path=db_path)
        disabled = evolution.disable_candidate(candidate_id, path=db_path)
        after = evolution.build_runtime_context("生成报告", path=db_path)
        items = evolution.list_candidates(status="disabled", path=db_path)

        self.assertIn("输出路径", before)
        self.assertTrue(disabled["ok"])
        self.assertEqual(items[0]["id"], candidate_id)
        self.assertNotIn("输出路径", after)

    def test_rollback_candidate_restores_pending_and_removes_linked_memory(self):
        db_path = settings.data_dir / "secretary.db"
        candidate_id = evolution.store_candidate(
            type="prompt_rule",
            title="输出路径",
            content="生成文件后要列出输出路径。",
            confidence=0.8,
            path=db_path,
        )

        with patch("app.evolution.memory.remember", return_value="memory-1"):
            evolution.apply_candidate(candidate_id, path=db_path)
        with patch("app.evolution.memory.forget", return_value={"ok": True, "id": "memory-1"}) as forget:
            result = evolution.rollback_candidate(candidate_id, path=db_path)
        pending = evolution.list_candidates(status="pending", path=db_path)

        self.assertTrue(result["ok"])
        self.assertEqual(pending[0]["id"], candidate_id)
        self.assertIsNone(pending[0]["memory_id"])
        forget.assert_called_once_with("memory-1")


def _insert_message(db_path: Path, role: str, content: str) -> None:
    evolution.init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                content TEXT,
                tool_calls TEXT,
                tool_call_id TEXT,
                name TEXT,
                ts REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, ts) VALUES ('s1', ?, ?, 1)",
            (role, content),
        )
        conn.commit()


if __name__ == "__main__":
    unittest.main()
