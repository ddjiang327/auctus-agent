from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config import settings
from app import task_mode


class TaskModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_data_dir = settings.data_dir
        settings.data_dir = Path(self.tmp.name) / "data"
        task_mode._TASKS.clear()

    def tearDown(self):
        task_mode._TASKS.clear()
        settings.data_dir = self.old_data_dir
        self.tmp.cleanup()

    def test_task_persists_and_loads_from_sqlite(self):
        task = task_mode.create_or_resume("session-task", "买游戏本")
        self.assertEqual(task["current_step"], 0)
        self.assertEqual(task["plan"]["task_type"], "research_comparison")
        self.assertIn("预算或价格范围", task["plan"]["missing_requirements"])

        task_mode._TASKS.clear()
        loaded = task_mode.get("session-task")

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["goal"], "买游戏本")
        self.assertEqual(loaded["steps"][0]["status"], "in_progress")

    def test_list_tasks_returns_recent_summaries(self):
        task_mode.create_or_resume("older-task", "买保险")
        task_mode.create_or_resume("newer-task", "买游戏本")
        task_mode.mark_after_reply("newer-task", "| 型号 | 价格 |\n|---|---:|\n| A | $1 |")

        items = task_mode.list_tasks(limit=10)

        self.assertEqual(items[0]["session_id"], "newer-task")
        self.assertEqual(items[0]["goal"], "买游戏本")
        self.assertEqual(items[0]["artifact_count"], 1)
        self.assertIn("updated_ts", items[0])

    def test_add_activity_persists_timeline_item(self):
        task_mode.create_or_resume("session-task", "买游戏本")

        updated = task_mode.add_activity("session-task", "正在搜索：RTX 4060", "tool")
        task_mode._TASKS.clear()
        loaded = task_mode.get("session-task")

        self.assertEqual(updated["activity"][-1]["key"], "tool")
        self.assertEqual(loaded["activity"][-1]["text"], "正在搜索：RTX 4060")

    def test_hidden_update_marker_updates_step_and_is_stripped(self):
        task_mode.create_or_resume("session-task", "买游戏本")
        reply = (
            "当前步骤：收集候选方案。\n"
            "<!--TASK_UPDATE {\"current_step\":3,\"status\":\"in_progress\",\"activity_key\":\"advanced\"}-->"
        )

        updated = task_mode.mark_after_reply("session-task", reply)

        self.assertEqual(updated["current_step"], 2)
        self.assertEqual(updated["steps"][0]["status"], "completed")
        self.assertEqual(updated["steps"][2]["status"], "in_progress")
        self.assertNotIn("TASK_UPDATE", task_mode.strip_update_markers(reply))

    def test_invalid_update_marker_falls_back_to_text_inference(self):
        task_mode.create_or_resume("session-task", "买游戏本")
        reply = (
            "当前步骤：建立比较标准。\n"
            "<!--TASK_UPDATE {\"current_step\":99,\"status\":\"bad\",\"activity_key\":\"bad\"}-->"
        )

        updated = task_mode.mark_after_reply("session-task", reply)

        self.assertEqual(updated["current_step"], 1)
        self.assertEqual(updated["steps"][1]["status"], "in_progress")

    def test_update_marker_normalizes_invalid_optional_fields(self):
        task_mode.create_or_resume("session-task", "买游戏本")
        reply = (
            "我会继续收集候选。\n"
            "<!--TASK_UPDATE {\"current_step\":3,\"status\":\"waiting\",\"activity_key\":\"waiting\","
            "\"steps\":[\"completed\",\"bad\",\"in_progress\"],"
            "\"artifacts\":[{\"type\":\"comparison_table\",\"content\":\"| A | B |\\n|---|---|\\n| 1 | 2 |\"},{\"type\":\"\",\"content\":\"bad\"}]}-->"
        )

        update = task_mode.extract_update(reply)
        updated = task_mode.mark_after_reply("session-task", reply)

        self.assertEqual(update["steps"], ["completed", "in_progress"])
        self.assertEqual(len(update["artifacts"]), 1)
        self.assertEqual(updated["status"], "in_progress")
        self.assertEqual(updated["current_step"], 1)
        self.assertEqual(len(updated["artifacts"]["items"]), 1)

    def test_update_marker_can_refine_structured_plan(self):
        task_mode.create_or_resume("session-task", "买保险")
        reply = (
            "我会先确认保额和垫底费。\n"
            "<!--TASK_UPDATE {\"current_step\":1,\"status\":\"waiting\","
            "\"plan\":{\"task_type\":\"insurance_research\","
            "\"missing_requirements\":[\"房屋地址\",\"保额\",\"excess\"],"
            "\"success_criteria\":[\"至少比较三家保险公司\"],"
            "\"research_plan\":[\"确认资料\",\"搜索官方报价页\"]}}-->"
        )

        updated = task_mode.mark_after_reply("session-task", reply)

        self.assertEqual(updated["plan"]["task_type"], "insurance_research")
        self.assertEqual(updated["plan"]["missing_requirements"], ["房屋地址", "保额", "excess"])
        self.assertIn("至少比较三家保险公司", updated["plan"]["success_criteria"])

    def test_final_recommendation_inference_marks_task_completed(self):
        task_mode.create_or_resume("session-task", "买游戏本")
        reply = "最终建议：首选 RTX 4060、16GB RAM、1TB SSD 的机型。"

        updated = task_mode.mark_after_reply("session-task", reply)

        self.assertEqual(updated["status"], "completed")
        self.assertTrue(all(step["status"] == "completed" for step in updated["steps"]))

    def test_completed_task_starts_fresh_when_goal_changes(self):
        original = task_mode.create_or_resume("session-task", "买游戏本")
        task_mode.mark_after_reply("session-task", "最终建议：首选 RTX 4060 游戏本。")

        fresh = task_mode.create_or_resume("session-task", "规划东京旅行")

        self.assertNotEqual(fresh["id"], original["id"])
        self.assertEqual(fresh["goal"], "规划东京旅行")
        self.assertEqual(fresh["status"], "in_progress")
        self.assertEqual(fresh["current_step"], 0)
        self.assertEqual(fresh["steps"][0]["status"], "in_progress")

    def test_markdown_table_is_saved_as_task_artifact(self):
        task_mode.create_or_resume("session-task", "买游戏本")
        reply = """
当前步骤：整理结构化对比

| 型号 | 价格 | 显卡 | 风险 |
|---|---:|---|---|
| A | $1499 | RTX 4060 | 需确认库存 |
"""

        updated = task_mode.mark_after_reply("session-task", reply)
        artifacts = updated["artifacts"]["items"]

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["type"], "comparison_table")
        self.assertIn("RTX 4060", artifacts[0]["content"])

        updated_again = task_mode.mark_after_reply("session-task", reply)
        self.assertEqual(len(updated_again["artifacts"]["items"]), 1)

    def test_live_view_returns_none_when_no_task(self):
        self.assertIsNone(task_mode.live_view("missing-session"))

    def test_live_view_returns_structured_trace(self):
        task_mode.create_or_resume("live-session", "研究并对比三个云服务商")
        task_mode.add_activity("live-session", "已检查一个来源，继续整理结果", "tool_done")

        view = task_mode.live_view("live-session")

        self.assertIsNotNone(view)
        self.assertEqual(view["goal"], "研究并对比三个云服务商")
        self.assertEqual(view["status"], "in_progress")
        self.assertEqual(len(view["steps"]), len(task_mode.DEFAULT_STEPS))
        self.assertEqual(view["steps"][0]["status"], "in_progress")
        # activity timeline carries the observable work note we just added
        self.assertEqual(view["activity"][-1]["text"], "已检查一个来源，继续整理结果")
        self.assertEqual(view["activity"][-1]["key"], "tool_done")
        self.assertIn("time", view["activity"][-1])
        self.assertEqual(view["artifacts"], [])

    def test_live_view_surfaces_artifacts(self):
        task_mode.create_or_resume("live-artifact", "对比显卡")
        task_mode.mark_after_reply(
            "live-artifact", "| 型号 | 价格 |\n|---|---:|\n| RTX 4060 | $299 |"
        )

        view = task_mode.live_view("live-artifact")

        self.assertEqual(len(view["artifacts"]), 1)
        self.assertEqual(view["artifacts"][0]["type"], "comparison_table")


if __name__ == "__main__":
    unittest.main()
