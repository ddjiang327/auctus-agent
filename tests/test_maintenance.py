from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import maintenance
from app.config import settings


class MaintenanceTests(unittest.TestCase):
    def test_usage_summary_aggregates_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "tool_calls.jsonl"
            entries = [
                {"tool_name": "read_file", "model": "m1", "token_usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}},
                {"tool_name": "read_file", "model": "m1", "token_usage": {"prompt_tokens": 4, "completion_tokens": 5, "total_tokens": 9}},
            ]
            log_path.write_text("\n".join(json.dumps(x) for x in entries), encoding="utf-8")

            result = maintenance.usage_summary(log_path)

        self.assertEqual(result["calls"], 2)
        self.assertEqual(result["total_tokens"], 12)
        self.assertEqual(result["by_model"]["m1"]["calls"], 2)
        self.assertEqual(result["by_tool"]["read_file"]["total_tokens"], 12)

    def test_clean_logs_keeps_tail_and_writes_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "tool_calls.jsonl"
            log_path.write_text("a\nb\nc\n", encoding="utf-8")

            result = maintenance.clean_logs(keep=2, log_path=log_path)

            self.assertEqual(result["before"], 3)
            self.assertEqual(result["after"], 2)
            self.assertEqual(log_path.read_text(encoding="utf-8"), "b\nc\n")
            self.assertTrue(Path(result["backup"]).exists())

    def test_sleep_evolve_runs_scan_and_throttles_next_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "maintenance_state.json"
            db_path = Path(tmp) / "secretary.db"
            fake_scan = {"ok": True, "candidates_added": 1, "ids": ["learn-1"]}

            with patch("app.maintenance.evolution.scan", return_value=fake_scan) as scan_mock:
                first = maintenance.sleep_evolve(
                    min_interval_hours=12,
                    state_path=state_path,
                    db_path=db_path,
                )
                second = maintenance.sleep_evolve(
                    min_interval_hours=12,
                    state_path=state_path,
                    db_path=db_path,
                )

        self.assertFalse(first["skipped"])
        self.assertEqual(first["evolution"]["candidates_added"], 1)
        self.assertTrue(second["skipped"])
        scan_mock.assert_called_once()

    def test_sleep_evolve_force_ignores_throttle(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "maintenance_state.json"
            db_path = Path(tmp) / "secretary.db"
            fake_scan = {"ok": True, "candidates_added": 0, "ids": []}

            with patch("app.maintenance.evolution.scan", return_value=fake_scan) as scan_mock:
                maintenance.sleep_evolve(state_path=state_path, db_path=db_path)
                forced = maintenance.sleep_evolve(force=True, state_path=state_path, db_path=db_path)

        self.assertFalse(forced["skipped"])
        self.assertEqual(scan_mock.call_count, 2)

    def test_stability_check_writes_snapshot_and_checks_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_workspace = settings.workspace_dir
            old_output = settings.output_dir
            old_logs = settings.logs_dir
            old_data = settings.data_dir
            settings.workspace_dir = root / "inputs"
            settings.output_dir = root / "outputs"
            settings.logs_dir = root / "logs"
            settings.data_dir = root / "data"
            settings.workspace_dir.mkdir()
            settings.output_dir.mkdir()
            settings.logs_dir.mkdir()
            settings.data_dir.mkdir()
            with sqlite3.connect(settings.data_dir / "secretary.db") as conn:
                conn.execute("CREATE TABLE t (id INTEGER)")
            (settings.logs_dir / "tool_calls.jsonl").write_text(json.dumps({"ok": True}) + "\n", encoding="utf-8")

            try:
                result = maintenance.stability_check(snapshot=True)
                snapshot_exists = Path(result["snapshot_path"]).exists()
            finally:
                settings.workspace_dir = old_workspace
                settings.output_dir = old_output
                settings.logs_dir = old_logs
                settings.data_dir = old_data

        self.assertEqual(result["status"], "ok")
        self.assertTrue(snapshot_exists)
        sqlite_check = next(item for item in result["checks"] if item["name"] == "secretary_db")
        self.assertEqual(sqlite_check["quick_check"], "ok")


if __name__ == "__main__":
    unittest.main()
