from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from services.config_service import ConfigService
from ui.main_window import MainWindow
from ui.settings_page import SettingsPage


def _config(temp_dir: str) -> ConfigService:
    return ConfigService(
        default_path=Path("config/default_config.json"),
        user_path=Path(temp_dir) / "user.json",
    )


class PlaybackWindowModeConfigTests(unittest.TestCase):
    def test_default_mode_is_windowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _config(temp_dir)

            self.assertEqual(config.playback_window_mode(), "window")
            self.assertFalse(config.playback_starts_fullscreen())

    def test_fullscreen_mode_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _config(temp_dir)
            config.set("player.playback_window_mode", "fullscreen")

            self.assertEqual(config.playback_window_mode(), "fullscreen")
            self.assertTrue(config.playback_starts_fullscreen())

    def test_unknown_mode_falls_back_to_windowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _config(temp_dir)
            config.set("player.playback_window_mode", "maximized-ish")

            self.assertEqual(config.playback_window_mode(), "window")
            self.assertFalse(config.playback_starts_fullscreen())


class PlaybackWindowModeSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_radios_reflect_saved_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _config(temp_dir)
            config.set("player.playback_window_mode", "fullscreen")
            config.save()
            page = SettingsPage(config)

            self.assertTrue(page.playback_window_fullscreen.isChecked())
            self.assertFalse(page.playback_window_windowed.isChecked())
            page.close()

    def test_save_writes_selected_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _config(temp_dir)
            page = SettingsPage(config)
            self.assertTrue(page.playback_window_windowed.isChecked())
            page.playback_window_fullscreen.setChecked(True)
            page.save()
            page.close()

            self.assertEqual(config.playback_window_mode(), "fullscreen")
            reloaded = _config(temp_dir)
            self.assertEqual(reloaded.playback_window_mode(), "fullscreen")


class PlaybackWindowModeApplyTests(unittest.TestCase):
    @staticmethod
    def _state(mode: str, *, fullscreen: bool = False) -> SimpleNamespace:
        calls: list[bool] = []
        return SimpleNamespace(
            _pending_playback_fullscreen=False,
            config=SimpleNamespace(playback_starts_fullscreen=lambda: mode == "fullscreen"),
            isFullScreen=lambda: fullscreen,
            _enter_player_fullscreen=lambda: calls.append(True),
            _calls=calls,
        )

    def test_window_mode_never_enters_fullscreen(self) -> None:
        state = self._state("window")

        MainWindow._arm_playback_window_mode(state)
        MainWindow._apply_playback_window_mode(state)

        self.assertEqual(state._calls, [])
        self.assertFalse(state._pending_playback_fullscreen)

    def test_fullscreen_mode_enters_once_per_session(self) -> None:
        state = self._state("fullscreen")

        MainWindow._arm_playback_window_mode(state)
        MainWindow._apply_playback_window_mode(state)
        MainWindow._apply_playback_window_mode(state)

        self.assertEqual(state._calls, [True])
        self.assertFalse(state._pending_playback_fullscreen)

    def test_apply_without_arming_does_nothing(self) -> None:
        state = self._state("fullscreen")

        MainWindow._apply_playback_window_mode(state)

        self.assertEqual(state._calls, [])

    def test_already_fullscreen_is_left_alone(self) -> None:
        state = self._state("fullscreen", fullscreen=True)

        MainWindow._arm_playback_window_mode(state)
        MainWindow._apply_playback_window_mode(state)

        self.assertEqual(state._calls, [])
        self.assertFalse(state._pending_playback_fullscreen)


if __name__ == "__main__":
    unittest.main()
