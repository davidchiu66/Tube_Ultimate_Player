from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from resolver.models import PlaylistEntry, PlaylistInfo, SavedPlaylist
from ui.playlist_overlay import PlaylistOverlay
from ui.playlist_page import PlaylistPage
from ui.toolbar import PlayerToolbar


class PlaylistNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_toolbar_exposes_playlist_button_after_home(self) -> None:
        toolbar = PlayerToolbar()
        order = [
            toolbar.layout().itemAt(index).widget()
            for index in range(toolbar.layout().count())
            if toolbar.layout().itemAt(index).widget() is not None
        ]

        self.assertIn(toolbar.playlist_button, order)
        self.assertLess(order.index(toolbar.home_button), order.index(toolbar.playlist_button))
        self.assertLess(order.index(toolbar.playlist_button), order.index(toolbar.player_button))

    def test_playlist_page_presents_entries_in_library_table(self) -> None:
        page = PlaylistPage()
        playlist = PlaylistInfo(
            playlist_id="p",
            title="我的播放列表",
            webpage_url="https://example.com/playlist",
            entries=[
                PlaylistEntry("p", "v1", "第一集", "https://example.com/1", uploader="作者", duration=65, position=1),
                PlaylistEntry("p", "v2", "第二集", "https://example.com/2", uploader="作者", duration=125, position=2),
            ],
        )

        page.set_playlist(playlist, current_index=1)

        self.assertEqual(page.table.objectName(), "LibraryTable")
        self.assertEqual(page.table.rowCount(), 2)
        self.assertEqual(page.table.item(0, 0).text(), "第一集")
        self.assertEqual(page.table.item(1, 0).text(), "▶ 第二集")
        self.assertIn("共 2 条", page.meta_label.text())

    def test_switching_saved_playlist_loads_without_button(self) -> None:
        page = PlaylistPage()
        emitted: list[str] = []
        page.load_saved_requested.connect(emitted.append)

        # "加载"按钮已移除，切换下拉框本身就是加载动作。
        self.assertFalse(hasattr(page, "load_saved_button"))

        page.set_saved_playlists([SavedPlaylist("saved-1", "稍后观看"), SavedPlaylist("saved-2", "合集")])
        self.assertEqual(emitted, [])

        page.saved_combo.setCurrentIndex(1)
        self.assertEqual(emitted, ["saved-1"])

        page.saved_combo.setCurrentIndex(2)
        self.assertEqual(emitted, ["saved-1", "saved-2"])

    def test_programmatic_refill_does_not_trigger_load(self) -> None:
        page = PlaylistPage()
        emitted: list[str] = []
        page.load_saved_requested.connect(emitted.append)

        saved = [SavedPlaylist("saved-1", "稍后观看"), SavedPlaylist("saved-2", "合集")]
        # 主窗口加载完毕后会回填下拉框，这一步不能再次触发加载（否则递归）。
        page.set_saved_playlists(saved, current_key="saved-2")
        self.assertEqual(page.current_saved_key(), "saved-2")
        self.assertEqual(emitted, [])

        # 用户手工再选中同一项也不重复加载。
        page.saved_combo.setCurrentIndex(2)
        self.assertEqual(emitted, [])

        page.saved_combo.setCurrentIndex(1)
        self.assertEqual(emitted, ["saved-1"])

    def test_placeholder_selection_does_not_trigger_load(self) -> None:
        page = PlaylistPage()
        emitted: list[str] = []
        page.load_saved_requested.connect(emitted.append)

        page.set_saved_playlists([SavedPlaylist("saved-1", "稍后观看")], current_key="saved-1")
        page.saved_combo.setCurrentIndex(0)

        self.assertEqual(emitted, [])
        self.assertFalse(page.delete_saved_button.isEnabled())

    def test_saved_playlist_delete_emits_selected_key(self) -> None:
        page = PlaylistPage()
        emitted: list[str] = []
        page.delete_saved_requested.connect(emitted.append)

        page.set_saved_playlists([SavedPlaylist("saved-1", "稍后观看")], current_key="saved-1")
        self.assertTrue(page.delete_saved_button.isEnabled())
        page._delete_saved()

        self.assertEqual(emitted, ["saved-1"])

    def test_saved_combo_ignores_wheel_without_focus(self) -> None:
        page = PlaylistPage()
        page.set_saved_playlists([SavedPlaylist("saved-1", "A"), SavedPlaylist("saved-2", "B")])
        emitted: list[str] = []
        page.load_saved_requested.connect(emitted.append)

        event = QWheelEvent(
            QPointF(4, 4),
            QPointF(4, 4),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
        page.saved_combo.wheelEvent(event)

        # 未聚焦时滚轮不改选中项，"切换即加载"才不会被误触发。
        self.assertEqual(page.saved_combo.currentIndex(), 0)
        self.assertEqual(emitted, [])


class PlaylistOverlaySavedListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_overlay_switching_saved_playlist_loads_without_button(self) -> None:
        overlay = PlaylistOverlay()
        emitted: list[str] = []
        overlay.load_saved_requested.connect(emitted.append)

        self.assertFalse(hasattr(overlay, "load_button"))

        overlay.set_saved_playlists([SavedPlaylist("saved-1", "稍后观看"), SavedPlaylist("saved-2", "合集")])
        self.assertEqual(emitted, [])

        overlay.saved_combo.setCurrentIndex(2)
        self.assertEqual(emitted, ["saved-2"])

    def test_overlay_programmatic_refill_does_not_trigger_load(self) -> None:
        overlay = PlaylistOverlay()
        emitted: list[str] = []
        overlay.load_saved_requested.connect(emitted.append)

        overlay.set_saved_playlists([SavedPlaylist("saved-1", "稍后观看")], current_key="saved-1")
        overlay.saved_combo.setCurrentIndex(1)

        self.assertEqual(emitted, [])
        self.assertTrue(overlay.delete_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
