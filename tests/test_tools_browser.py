from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.config import settings
from app import tools_browser as tb


class _FakePage:
    """Minimal stand-in for a Playwright page: screenshot() just writes a file."""

    def screenshot(self, path: str, full_page: bool = False) -> None:
        Path(path).write_bytes(b"\x89PNG fake")


class AutoSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_output = settings.output_dir
        settings.output_dir = Path(self.tmp.name) / "outputs"
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        self._old_state = dict(tb._STATE)

    def tearDown(self):
        tb._STATE.clear()
        tb._STATE.update(self._old_state)
        settings.output_dir = self.old_output
        self.tmp.cleanup()

    def test_auto_snapshot_noop_when_not_ready(self):
        tb._STATE.update({"ready": False, "page": None})
        tb._auto_snapshot("open")
        self.assertEqual(list(settings.output_dir.glob("auto-*.png")), [])

    def test_auto_snapshot_writes_png_when_ready(self):
        tb._STATE.update({"ready": True, "page": _FakePage()})
        tb._auto_snapshot("click")
        shots = list(settings.output_dir.glob("auto-*.png"))
        self.assertEqual(len(shots), 1)
        self.assertIn("-click.png", shots[0].name)

    def test_auto_snapshot_never_raises_on_screenshot_error(self):
        class Boom:
            def screenshot(self, path, full_page=False):
                raise RuntimeError("boom")

        tb._STATE.update({"ready": True, "page": Boom()})
        # Must not raise — auto-snapshot is best-effort.
        tb._auto_snapshot("open")
        self.assertEqual(list(settings.output_dir.glob("auto-*.png")), [])

    def test_prune_keeps_only_most_recent(self):
        for i in range(tb._AUTO_SHOT_KEEP + 5):
            p = settings.output_dir / f"auto-{1000 + i}-view.png"
            p.write_bytes(b"x")
            os.utime(p, (1000 + i, 1000 + i))  # distinct mtimes for deterministic ordering
        tb._prune_auto_shots(settings.output_dir)
        remaining = list(settings.output_dir.glob("auto-*.png"))
        self.assertEqual(len(remaining), tb._AUTO_SHOT_KEEP)


if __name__ == "__main__":
    unittest.main()
