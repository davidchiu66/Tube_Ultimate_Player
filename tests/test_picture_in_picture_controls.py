from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ui.player_page import PlayerPage
from resolver.models import PlaylistEntry, PlaylistInfo


class PictureInPictureControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.page = PlayerPage()
        self.page.resize(640, 360)
        self.page.show()
        self.page.set_playback_available(True)
        QApplication.processEvents()

    def tearDown(self) -> None:
        self.page.close()

    def test_entry_button_is_after_fullscreen_and_has_shortcut_tooltip(self) -> None:
        controls = self.page.fullscreen_button.parentWidget().findChildren(type(self.page.fullscreen_button))
        self.assertGreater(controls.index(self.page.picture_in_picture_button), controls.index(self.page.fullscreen_button))
        self.assertEqual(self.page.picture_in_picture_button.toolTip(), "小窗播放（W）")

    def test_mode_shows_title_and_mini_controls(self) -> None:
        self.page.set_picture_in_picture(True)

        self.assertTrue(self.page.picture_in_picture)
        self.assertTrue(self.page.picture_in_picture_title_bar.isVisible())
        self.assertTrue(self.page.picture_in_picture_control_bar.isVisible())
        self.assertFalse(self.page.control_panel.isVisible())
        self.assertFalse(hasattr(self.page, "picture_in_picture_exit_button"))
        self.assertFalse(self.page.picture_in_picture_close_button.icon().isNull())

    def test_all_picture_in_picture_styles_refresh_svg_icons(self) -> None:
        for style in ("style_a", "style_b", "style_c"):
            self.page.set_picture_in_picture_style(style)
            self.assertEqual(self.page.picture_in_picture_style, style)
            self.assertFalse(self.page.picture_in_picture_play_button.icon().isNull())
            self.assertFalse(self.page.picture_in_picture_lock_button.icon().isNull())

    def test_random_picture_in_picture_style_resolves_to_one_fixed_style(self) -> None:
        self.page.set_picture_in_picture_style("random")

        self.assertEqual(self.page.picture_in_picture_style_preference, "random")
        self.assertIn(self.page.picture_in_picture_style, {"style_a", "style_b", "style_c"})

    def test_playback_hint_uses_themed_svg_icon(self) -> None:
        self.page.set_picture_in_picture(True)

        self.page._show_picture_in_picture_playback_hint("pause")

        self.assertEqual(self.page.picture_in_picture_hint_label.text(), "暂停")
        self.assertFalse(self.page.picture_in_picture_hint_icon.pixmap().isNull())

    def test_random_style_is_resolved_again_for_new_media(self) -> None:
        with patch("ui.player_page.random_choice", side_effect=["style_a", "style_b"]):
            self.page.set_picture_in_picture_style("random")
            self.page._refresh_picture_in_picture_style_for_media()

        self.assertEqual(self.page.picture_in_picture_style_preference, "random")
        self.assertEqual(self.page.picture_in_picture_style, "style_b")

    def test_escape_exits_picture_in_picture(self) -> None:
        requests: list[bool] = []
        self.page.picture_in_picture_requested.connect(lambda: requests.append(True))
        self.page.set_picture_in_picture(True)

        self.page._shortcut_exit_fullscreen()

        self.assertEqual(requests, [True])

    def test_lock_only_changes_window_interaction_state(self) -> None:
        states: list[bool] = []
        self.page.picture_in_picture_lock_changed.connect(states.append)
        self.page.set_picture_in_picture(True)

        self.page.picture_in_picture_lock_button.click()

        self.assertTrue(self.page.picture_in_picture_locked)
        self.assertTrue(self.page.picture_in_picture_play_button.isEnabled())
        self.assertEqual(states, [True])

    def test_finished_playback_shows_two_action_overlay(self) -> None:
        self.page.set_picture_in_picture(True)
        self.page.set_playback_finished(True)

        self.assertTrue(self.page.picture_in_picture_end_overlay.isVisible())
        self.assertEqual(self.page.picture_in_picture_replay_button.text(), "重新播放")
        self.assertEqual(self.page.picture_in_picture_return_button.text(), "返回播放器")

    def test_mini_progress_and_volume_follow_normal_controls(self) -> None:
        self.page.update_duration(100)
        self.page.update_position(25)
        self.page.set_volume(63)

        self.assertEqual(self.page.picture_in_picture_progress_slider.value(), 250)
        self.assertEqual(self.page.picture_in_picture_volume_slider.value(), 63)

    def test_picture_in_picture_disables_normal_cursor_hiding(self) -> None:
        self.page._set_cursor_hidden(True)

        self.page.set_picture_in_picture(True)
        self.page._handle_idle_timeout()

        self.assertFalse(self.page._idle_timer.isActive())
        self.assertEqual(self.page.video_widget.cursor().shape(), Qt.CursorShape.ArrowCursor)
        self.assertEqual(self.page.picture_in_picture_control_bar.cursor().shape(), Qt.CursorShape.ArrowCursor)

    def test_resize_cursor_reaches_overlay_children_and_survives_control_show(self) -> None:
        self.page.set_picture_in_picture(True)

        self.page.set_picture_in_picture_cursor(Qt.CursorShape.SizeFDiagCursor)
        self.page._show_picture_in_picture_controls()

        self.assertEqual(self.page.video_widget.cursor().shape(), Qt.CursorShape.SizeFDiagCursor)
        self.assertEqual(self.page.picture_in_picture_close_button.cursor().shape(), Qt.CursorShape.SizeFDiagCursor)
        self.assertEqual(self.page.picture_in_picture_progress_slider.cursor().shape(), Qt.CursorShape.SizeFDiagCursor)

    def test_locked_picture_in_picture_forces_arrow_cursor(self) -> None:
        self.page.set_picture_in_picture(True)
        self.page.set_picture_in_picture_cursor(Qt.CursorShape.SizeHorCursor)

        self.page.picture_in_picture_lock_button.click()

        self.assertEqual(self.page.picture_in_picture_close_button.cursor().shape(), Qt.CursorShape.ArrowCursor)
        self.assertEqual(self.page.video_widget.cursor().shape(), Qt.CursorShape.ArrowCursor)

    def test_resize_zone_on_close_button_takes_priority_over_click(self) -> None:
        mouse_events: list[str] = []
        exit_requests: list[bool] = []
        self.page.picture_in_picture_mouse_event.connect(lambda kind, _position: mouse_events.append(kind))
        self.page.picture_in_picture_requested.connect(lambda: exit_requests.append(True))
        self.page.set_picture_in_picture(True)
        QApplication.processEvents()

        point = QPoint(self.page.picture_in_picture_close_button.width() - 1, self.page.picture_in_picture_close_button.height() // 2)
        QTest.mousePress(self.page.picture_in_picture_close_button, Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseRelease(self.page.picture_in_picture_close_button, Qt.MouseButton.LeftButton, pos=point)

        self.assertEqual(mouse_events, ["press", "release"])
        self.assertEqual(exit_requests, [])

    def test_playlist_and_collection_overlays_stay_closed_in_picture_in_picture(self) -> None:
        entry = PlaylistEntry("list", "1", "Video", "https://example.com/1", position=1)
        playlist = PlaylistInfo("list", "列表", "https://example.com", entries=[entry])
        self.page.set_playlist_context(playlist, current_index=0)
        self.page.set_collection_context(playlist, current_index=0)
        self.page.set_picture_in_picture(True)

        self.page._handle_mouse_move(self.page, self.page.rect().topLeft())

        self.assertFalse(self.page.playlist_overlay.isVisible())
        self.assertFalse(self.page.collection_overlay.isVisible())
        self.assertFalse(self.page.playlist_overlay.is_open())
        self.assertFalse(self.page.collection_overlay.is_open())

if __name__ == "__main__":
    unittest.main()
