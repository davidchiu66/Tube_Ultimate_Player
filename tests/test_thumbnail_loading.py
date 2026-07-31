from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.thumbnail_cache import ThumbnailCache, _looks_like_complete_image  # noqa: E402


class _FakeLabel:
    """替身控件：destroyed=True 时模拟 C++ 对象已析构后的 RuntimeError。"""

    def __init__(self, *, destroyed: bool = False) -> None:
        self.destroyed = destroyed
        self.pixmap_calls = 0
        self.text = None

    def setPixmap(self, _pixmap) -> None:  # noqa: N802
        if self.destroyed:
            raise RuntimeError("Internal C++ object already deleted.")
        self.pixmap_calls += 1

    def setText(self, text: str) -> None:  # noqa: N802
        if self.destroyed:
            raise RuntimeError("Internal C++ object already deleted.")
        self.text = text


class _FakeReply:
    def __init__(self) -> None:
        self.deleted = False

    def deleteLater(self) -> None:  # noqa: N802
        self.deleted = True


class ThumbnailLoadingTests(unittest.TestCase):
    def test_complete_png_is_accepted(self) -> None:
        data = QByteArray(Path("docs/assets/icons/app-icon-16.png").read_bytes())

        self.assertTrue(_looks_like_complete_image(data))

    def test_truncated_png_is_rejected_before_qt_decode(self) -> None:
        data = QByteArray(Path("docs/assets/icons/app-icon-16.png").read_bytes()[:64])

        self.assertFalse(_looks_like_complete_image(data))

    def test_html_error_payload_is_rejected(self) -> None:
        data = QByteArray(b"<html><body>403 Forbidden</body></html>")

        self.assertFalse(_looks_like_complete_image(data))


class ThumbnailWaiterIsolationTests(unittest.TestCase):
    """C2：失效控件不得中断同批次其余等待者的回填。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.cache = ThumbnailCache()
        self.key = ("https://cdn/thumb.jpg", 120, 68)

    def _register(self, *labels: _FakeLabel) -> None:
        import weakref

        self.cache._in_flight[self.key] = [weakref.ref(label) for label in labels]

    def _finish(self, *, failure: str = "") -> _FakeReply:
        reply = _FakeReply()
        with patch(
            "ui.thumbnail_cache.read_image_reply",
            return_value=(QByteArray(Path("docs/assets/icons/app-icon-16.png").read_bytes()), failure),
        ):
            self.cache._handle_finished(reply, self.key, "封面失败")
        return reply

    def test_destroyed_label_does_not_abort_remaining_waiters(self) -> None:
        dead = _FakeLabel(destroyed=True)
        alive_before, alive_after = _FakeLabel(), _FakeLabel()
        self._register(alive_before, dead, alive_after)

        reply = self._finish()

        self.assertEqual(alive_before.pixmap_calls, 1)
        self.assertEqual(alive_after.pixmap_calls, 1)
        self.assertEqual(alive_after.text, "")
        self.assertTrue(reply.deleted)

    def test_failure_path_also_survives_destroyed_label(self) -> None:
        dead = _FakeLabel(destroyed=True)
        alive = _FakeLabel()
        self._register(dead, alive)

        self._finish(failure="http status 403")

        self.assertEqual(alive.text, "封面失败")

    def test_in_flight_entry_is_cleared_after_finish(self) -> None:
        self._register(_FakeLabel())

        self._finish()

        self.assertEqual(self.cache.pending_count(), 0)

    def test_cancel_for_drops_only_that_waiter(self) -> None:
        keep, drop = _FakeLabel(), _FakeLabel()
        self._register(keep, drop)

        self.cache.cancel_for(drop)
        self._finish()

        self.assertEqual(keep.pixmap_calls, 1)
        self.assertEqual(drop.pixmap_calls, 0)

    def test_garbage_collected_label_is_skipped(self) -> None:
        alive = _FakeLabel()
        collected = _FakeLabel()
        self._register(collected, alive)
        del collected

        self._finish()

        self.assertEqual(alive.pixmap_calls, 1)


if __name__ == "__main__":
    unittest.main()
