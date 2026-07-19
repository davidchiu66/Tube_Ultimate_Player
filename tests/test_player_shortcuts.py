from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QShortcut
from PySide6.QtWidgets import QApplication

from resolver.models import PlaylistEntry, PlaylistInfo, VideoInfo
from services.config_service import ConfigService
from ui.player_page import PlayerPage


class PlayerShortcutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.page = PlayerPage()
        self.page.show()
        self.page.set_playback_available(True)
        self.page.set_loading(False)
        QApplication.processEvents()

    def tearDown(self) -> None:
        self.page.close()

    def test_arrow_shortcuts_are_registered(self) -> None:
        sequences = {shortcut.key().toString() for shortcut in self.page.findChildren(QShortcut)}
        self.assertTrue({"Left", "Right", "Up", "Down"}.issubset(sequences))

    def test_extended_playback_shortcuts_are_registered(self) -> None:
        sequences = {shortcut.key().toString() for shortcut in self.page.findChildren(QShortcut)}
        self.assertTrue({"M", "Home", "End", "PgUp", "PgDown"}.issubset(sequences))

    def test_action_and_large_seek_shortcuts_are_registered(self) -> None:
        sequences = {shortcut.key().toString() for shortcut in self.page.findChildren(QShortcut)}
        self.assertTrue({"S", "D", "C", "Ctrl+C", "Ctrl+Left", "Ctrl+Right"}.issubset(sequences))

    def test_custom_shortcut_configuration_is_applied(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = ConfigService(
                default_path=Path("config/default_config.json"),
                user_path=Path(temp_dir) / "user.json",
            )
            config.set("shortcuts.stop", "Ctrl+S")
            page = PlayerPage(config)
            sequences = {shortcut.key().toString() for shortcut in page.findChildren(QShortcut)}
            page.close()

        self.assertIn("Ctrl+S", sequences)
        self.assertNotIn("S", sequences)

    def test_left_and_right_seek_ten_seconds(self) -> None:
        targets: list[float] = []
        self.page.seek_requested.connect(targets.append)
        self.page.update_duration(100)
        self.page.update_position(50)

        self.page._shortcut_seek(-10)
        self.page._shortcut_seek(10)

        self.assertEqual(targets, [40.0, 60.0])

    def test_seek_is_clamped_to_media_bounds(self) -> None:
        targets: list[float] = []
        self.page.seek_requested.connect(targets.append)
        self.page.update_duration(100)
        self.page.update_position(5)
        self.page._shortcut_seek(-10)
        self.page.update_position(98)
        self.page._shortcut_seek(10)

        self.assertEqual(targets, [0.0, 100.0])

    def test_up_and_down_adjust_volume_by_five(self) -> None:
        values: list[int] = []
        self.page.set_volume(80)
        self.page.volume_changed.connect(values.append)

        self.page._shortcut_volume(5)
        self.page._shortcut_volume(-5)

        self.assertEqual(values, [85, 80])

    def test_mute_toggles_previous_volume(self) -> None:
        values: list[int] = []
        self.page.set_volume(65)
        self.page.volume_changed.connect(values.append)

        self.page._shortcut_toggle_mute()
        self.page._shortcut_toggle_mute()

        self.assertEqual(values, [0, 65])

    def test_home_and_end_seek_to_media_boundaries(self) -> None:
        targets: list[float] = []
        self.page.seek_requested.connect(targets.append)
        self.page.update_duration(125)

        self.page._shortcut_seek_start()
        self.page._shortcut_seek_end()

        self.assertEqual(targets, [0.0, 125.0])

    def test_page_keys_move_between_playlist_entries(self) -> None:
        entries = [
            PlaylistEntry("p", str(index), f"Video {index}", f"https://example.com/{index}", position=index + 1)
            for index in range(3)
        ]
        playlist = PlaylistInfo("p", "Playlist", "https://example.com", entries=entries)
        targets: list[int] = []
        self.page.playlist_entry_requested.connect(targets.append)
        self.page.set_playlist_context(playlist, current_index=1)

        self.page._shortcut_playlist_step(-1)
        self.page._shortcut_playlist_step(1)
        self.page.set_playlist_current_index(0)
        self.page._shortcut_playlist_step(-1)
        self.page.set_playlist_current_index(2)
        self.page._shortcut_playlist_step(1)

        self.assertEqual(targets, [0, 2])

    def test_mouse_move_in_playlist_item_resets_player_activity(self) -> None:
        entry = PlaylistEntry("p", "1", "Video", "https://example.com/1", position=1)
        playlist = PlaylistInfo("p", "Playlist", "https://example.com", entries=[entry])
        self.page.set_playlist_context(playlist, current_index=0)
        self.page.set_paused(False)
        self.page._idle_timer.stop()
        item = self.page.playlist_overlay.list_widget.item(0)
        item_widget = self.page.playlist_overlay.list_widget.itemWidget(item)
        target = item_widget.title_label

        event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(2, 2),
            QPointF(2, 2),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(target, event)

        self.assertTrue(self.page._idle_timer.isActive())

    def test_browser_play_button_is_available_for_online_video(self) -> None:
        requests: list[bool] = []
        self.page.browser_play_requested.connect(lambda: requests.append(True))
        self.page.update_video_info(
            VideoInfo(
                video_id="video-1",
                title="Video",
                webpage_url="https://www.youtube.com/watch?v=video-1",
            ),
            "Auto",
        )

        self.assertTrue(self.page.browser_play_button.isEnabled())
        self.page.browser_play_button.click()

        self.assertEqual(requests, [True])

    def test_browser_play_button_is_disabled_for_local_file(self) -> None:
        self.page.set_browser_play_available(True)
        self.page.update_local_file_info("C:/Videos/example.mp4")

        self.assertFalse(self.page.browser_play_button.isEnabled())

    def test_action_shortcuts_emit_available_actions(self) -> None:
        actions: list[str] = []
        self.page.stop_requested.connect(lambda: actions.append("stop"))
        self.page.download_requested.connect(lambda: actions.append("download"))
        self.page.favorite_requested.connect(lambda: actions.append("favorite"))
        self.page.cast_requested.connect(lambda: actions.append("cast"))
        self.page.set_download_available(True)
        self.page.set_favorite_state(False, available=True)
        self.page.set_cast_available(True)

        self.page._shortcut_stop()
        self.page._shortcut_download()
        self.page._shortcut_favorite()
        self.page._shortcut_cast()

        self.assertEqual(actions, ["stop", "download", "favorite", "cast"])

    def test_ctrl_arrows_seek_sixty_seconds(self) -> None:
        targets: list[float] = []
        self.page.seek_requested.connect(targets.append)
        self.page.update_duration(300)
        self.page.update_position(120)

        self.page._shortcut_seek(-60)
        self.page._shortcut_seek(60)

        self.assertEqual(targets, [60.0, 180.0])


if __name__ == "__main__":
    unittest.main()
