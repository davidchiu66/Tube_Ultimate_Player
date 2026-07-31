"""E3 验证：鼠标滚轮调节音量，并复用键盘那套音量提示。"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.player_page import VOLUME_WHEEL_STEP, PlayerPage  # noqa: E402


def wheel_event(delta_y: int) -> QWheelEvent:
    return QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(0, 0),
        QPoint(0, delta_y),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


class WheelVolumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.page = PlayerPage()
        self.addCleanup(self.page.deleteLater)
        # 让 _shortcut_context_active() 为真。
        self.page._has_media = True
        self.page._loading = False
        self.page.show()
        self.page.volume_slider.setValue(50)

    def test_scroll_up_raises_volume_and_shows_hint(self) -> None:
        handled = self.page.eventFilter(self.page.video_widget, wheel_event(120))

        self.assertTrue(handled)
        self.assertEqual(self.page.volume_slider.value(), 50 + VOLUME_WHEEL_STEP)
        self.assertTrue(self.page.shortcut_hint.isVisible())
        self.assertIn("音量", self.page.shortcut_hint.text())

    def test_scroll_down_lowers_volume(self) -> None:
        self.page.eventFilter(self.page.video_widget, wheel_event(-120))

        self.assertEqual(self.page.volume_slider.value(), 50 - VOLUME_WHEEL_STEP)

    def test_volume_clamps_at_bounds(self) -> None:
        self.page.volume_slider.setValue(98)
        self.page.eventFilter(self.page.video_widget, wheel_event(120))
        self.assertEqual(self.page.volume_slider.value(), 100)

        self.page.volume_slider.setValue(2)
        self.page.eventFilter(self.page.video_widget, wheel_event(-120))
        self.assertEqual(self.page.volume_slider.value(), 0)

    def test_slider_wheel_is_not_intercepted(self) -> None:
        # 音量滑块自身的滚轮要保留原生行为，eventFilter 不能吃掉它。
        handled = self.page.eventFilter(self.page.volume_slider, wheel_event(120))
        self.assertFalse(handled)

    def test_quality_combo_wheel_is_not_intercepted(self) -> None:
        handled = self.page.eventFilter(self.page.quality_combo, wheel_event(120))
        self.assertFalse(handled)

    def test_no_response_without_media(self) -> None:
        self.page._has_media = False
        handled = self.page.eventFilter(self.page.video_widget, wheel_event(120))

        self.assertFalse(handled)
        self.assertEqual(self.page.volume_slider.value(), 50)


if __name__ == "__main__":
    unittest.main()
