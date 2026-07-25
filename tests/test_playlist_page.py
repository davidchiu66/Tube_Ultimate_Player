from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from resolver.models import PlaylistEntry, PlaylistInfo, SavedPlaylist
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

    def test_saved_playlist_controls_emit_selected_key(self) -> None:
        page = PlaylistPage()
        emitted: list[tuple[str, str]] = []
        page.load_saved_requested.connect(lambda key: emitted.append(("load", key)))
        page.delete_saved_requested.connect(lambda key: emitted.append(("delete", key)))

        page.set_saved_playlists([SavedPlaylist("saved-1", "稍后观看")], current_key="saved-1")
        page._load_saved()
        page._delete_saved()

        self.assertEqual(emitted, [("load", "saved-1"), ("delete", "saved-1")])

    def test_saved_playlist_buttons_enable_after_manual_selection(self) -> None:
        page = PlaylistPage()
        page.set_saved_playlists([SavedPlaylist("saved-1", "稍后观看")])

        self.assertFalse(page.load_saved_button.isEnabled())
        self.assertFalse(page.delete_saved_button.isEnabled())

        page.saved_combo.setCurrentIndex(1)

        self.assertTrue(page.load_saved_button.isEnabled())
        self.assertTrue(page.delete_saved_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
