from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import settings
from app import background_tasks as bt
from app import notify
from app import task_mode


class NotifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_data_dir = settings.data_dir
        settings.data_dir = Path(self.tmp.name) / "data"

    def tearDown(self):
        settings.data_dir = self.old_data_dir
        self.tmp.cleanup()

    def test_add_and_list_and_unread(self):
        notify.add_notification("task_done", "T1", "body1", "bg-1")
        notify.add_notification("task_failed", "T2", "body2", "bg-2")

        items = notify.list_notifications()

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "T2")  # newest first
        self.assertEqual(notify.unread_count(), 2)

    def test_mark_read_decrements_unread(self):
        note = notify.add_notification("task_done", "T", "b", "bg-1")

        self.assertTrue(notify.mark_read(note["id"]))
        self.assertEqual(notify.unread_count(), 0)
        # second mark on an already-read item is a no-op
        self.assertFalse(notify.mark_read(note["id"]))

    def test_unread_only_filter(self):
        a = notify.add_notification("task_done", "A")
        notify.add_notification("task_done", "B")
        notify.mark_read(a["id"])

        unread = notify.list_notifications(unread_only=True)

        self.assertEqual([n["title"] for n in unread], ["B"])

    def test_dispatch_task_done_writes_in_app_notification(self):
        # Telegram not configured in tests → external push is a no-op, in-app still recorded.
        note = notify.dispatch_task_done("bg-x", "研究云厂商", "结论：A 最便宜")

        self.assertEqual(note["kind"], "task_done")
        self.assertIn("研究云厂商", note["title"])
        self.assertEqual(notify.unread_count(), 1)


class BackgroundTaskQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_data_dir = settings.data_dir
        settings.data_dir = Path(self.tmp.name) / "data"
        task_mode._TASKS.clear()

    def tearDown(self):
        task_mode._TASKS.clear()
        settings.data_dir = self.old_data_dir
        self.tmp.cleanup()

    def test_enqueue_rejects_empty_prompt(self):
        with self.assertRaises(ValueError):
            bt.enqueue("   ")

    def test_enqueue_creates_queued_job_with_bg_session(self):
        job = bt.enqueue("研究三家云厂商")

        self.assertEqual(job["status"], bt.STATUS_QUEUED)
        self.assertTrue(job["session_id"].startswith("bg-"))
        self.assertEqual(job["prompt"], "研究三家云厂商")
        self.assertIsNone(job["finished_at"])

    def test_enqueue_respects_explicit_session_id(self):
        job = bt.enqueue("任务", session_id="custom-session")
        self.assertEqual(job["session_id"], "custom-session")

    def test_claim_next_job_marks_oldest_running(self):
        first = bt.enqueue("first")
        bt.enqueue("second")

        claimed = bt._claim_next_job()

        self.assertEqual(claimed["id"], first["id"])
        self.assertEqual(bt.get_job(first["id"])["status"], bt.STATUS_RUNNING)
        # claiming again returns the second (oldest remaining queued)
        second_claim = bt._claim_next_job()
        self.assertEqual(second_claim["prompt"], "second")

    def test_run_job_marks_done_and_pushes_notification(self):
        job = bt.enqueue("研究并对比")
        claimed = bt._claim_next_job()

        with patch("app.agent.chat", return_value={"reply": "对比表已生成", "files": []}) as mock_chat:
            bt._run_job(claimed)

        # agent.chat is invoked in Task Mode
        self.assertTrue(mock_chat.call_args.kwargs.get("task_mode_active"))
        done = bt.get_job(job["id"])
        self.assertEqual(done["status"], bt.STATUS_DONE)
        self.assertEqual(done["result"], "对比表已生成")
        self.assertIsNotNone(done["finished_at"])
        self.assertEqual(notify.unread_count(), 1)

    def test_run_job_registers_task_for_live_view(self):
        # Background tasks must be visible in the execution live-view (/api/task/{id}/live).
        # That requires a task_mode entry — which the worker creates, not agent.chat.
        job = bt.enqueue("研究并对比三个方案")
        claimed = bt._claim_next_job()
        task_mode._TASKS.clear()  # force load from store, not in-memory cache

        with patch("app.agent.chat", return_value={"reply": "完成", "files": []}):
            bt._run_job(claimed)

        live = task_mode.live_view(job["session_id"])
        self.assertIsNotNone(live)
        self.assertEqual(live["goal"], "研究并对比三个方案")

    def test_run_job_marks_error_on_exception(self):
        job = bt.enqueue("会失败的任务")
        claimed = bt._claim_next_job()

        with patch("app.agent.chat", side_effect=RuntimeError("boom")):
            bt._run_job(claimed)

        failed = bt.get_job(job["id"])
        self.assertEqual(failed["status"], bt.STATUS_ERROR)
        self.assertIn("boom", failed["error"])
        notes = notify.list_notifications()
        self.assertEqual(notes[0]["kind"], "task_failed")

    def test_recover_orphans_requeues_running_jobs(self):
        job = bt.enqueue("中断的任务")
        bt._claim_next_job()  # → running
        self.assertEqual(bt.get_job(job["id"])["status"], bt.STATUS_RUNNING)

        bt._recover_orphans()

        recovered = bt.get_job(job["id"])
        self.assertEqual(recovered["status"], bt.STATUS_QUEUED)
        self.assertIsNone(recovered["started_at"])

    def test_list_jobs_newest_first(self):
        bt.enqueue("old")
        bt.enqueue("new")
        jobs = bt.list_jobs()
        self.assertEqual(jobs[0]["prompt"], "new")

    def test_worker_pool_runs_queued_job_end_to_end(self):
        with patch("app.agent.chat", return_value={"reply": "ok", "files": []}):
            bt.start_workers()
            try:
                job = bt.enqueue("端到端任务")
                deadline = time.time() + 5
                while time.time() < deadline:
                    if bt.get_job(job["id"])["status"] == bt.STATUS_DONE:
                        break
                    time.sleep(0.05)
            finally:
                bt.stop_workers()
        self.assertEqual(bt.get_job(job["id"])["status"], bt.STATUS_DONE)


if __name__ == "__main__":
    unittest.main()
