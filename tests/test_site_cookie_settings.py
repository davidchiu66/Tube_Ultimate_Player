from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from resolver.youtube_resolver import YoutubeResolver
from services.config_service import ConfigService
from ui.settings_page import SettingsPage


class SiteCookieSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_config_selects_cookie_file_by_target_site(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = ConfigService(
                default_path=Path("config/default_config.json"),
                user_path=root / "user.json",
            )
            youtube_cookie = root / "youtube.txt"
            bilibili_cookie = root / "bilibili.txt"
            config.set("cookies.youtube.file", str(youtube_cookie))
            config.set("cookies.bilibili.file", str(bilibili_cookie))

            self.assertEqual(config.cookie_file_for_url("https://www.youtube.com/watch?v=1"), str(youtube_cookie))
            self.assertEqual(config.cookie_file_for_url("https://www.bilibili.com/video/BV1"), str(bilibili_cookie))

    def test_legacy_cookie_is_assigned_to_current_default_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = ConfigService(
                default_path=Path("config/default_config.json"),
                user_path=root / "user.json",
            )
            legacy_cookie = root / "legacy.txt"
            config.set("content.default_home", "bilibili")
            config.set("youtube.cookie_file", str(legacy_cookie))

            self.assertEqual(config.cookie_file("bilibili"), str(legacy_cookie))
            self.assertEqual(config.cookie_file("youtube"), "")

    def test_settings_switch_cookie_content_with_default_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            youtube_cookie = root / "youtube.txt"
            bilibili_cookie = root / "bilibili.txt"
            youtube_cookie.write_text("youtube-cookie", encoding="utf-8")
            bilibili_cookie.write_text("bilibili-cookie", encoding="utf-8")
            config = ConfigService(
                default_path=Path("config/default_config.json"),
                user_path=root / "user.json",
            )
            config.set("content.default_home", "bilibili")
            config.set("cookies.youtube.file", str(youtube_cookie))
            config.set("cookies.bilibili.file", str(bilibili_cookie))
            config.save()
            page = SettingsPage(config)

            self.assertEqual(page.cookie_content_label.text(), "Bilibili Cookie 内容")
            self.assertEqual(page.cookie_edit.toPlainText(), "bilibili-cookie")
            page.cookie_edit.setPlainText("bilibili-cookie-updated")
            page.default_home_youtube.setChecked(True)
            self.assertEqual(page.cookie_content_label.text(), "YouTube Cookie 内容")
            self.assertEqual(page.cookie_edit.toPlainText(), "youtube-cookie")
            page.cookie_edit.setPlainText("youtube-cookie-updated")
            page.default_home_bilibili.setChecked(True)
            self.assertEqual(page.cookie_edit.toPlainText(), "bilibili-cookie-updated")

            page.save()
            page.close()

            self.assertEqual(youtube_cookie.read_text(encoding="utf-8"), "youtube-cookie-updated")
            self.assertEqual(bilibili_cookie.read_text(encoding="utf-8"), "bilibili-cookie-updated")

    def test_ytdlp_command_uses_cookie_for_video_site(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = ConfigService(
                default_path=Path("config/default_config.json"),
                user_path=root / "user.json",
            )
            youtube_cookie = root / "youtube.txt"
            bilibili_cookie = root / "bilibili.txt"
            config.set("cookies.youtube.file", str(youtube_cookie))
            config.set("cookies.bilibili.file", str(bilibili_cookie))
            resolver = YoutubeResolver.__new__(YoutubeResolver)
            resolver.config = config
            resolver.ytdlp_path = Path("yt-dlp")

            with patch("resolver.youtube_resolver.prepare_cookie_file", side_effect=lambda path, _url: path):
                youtube_command = resolver._build_command("https://www.youtube.com/watch?v=1")
                bilibili_command = resolver._build_command("https://www.bilibili.com/video/BV1")

            self.assertEqual(youtube_command[youtube_command.index("--cookies") + 1], str(youtube_cookie))
            self.assertEqual(bilibili_command[bilibili_command.index("--cookies") + 1], str(bilibili_cookie))


if __name__ == "__main__":
    unittest.main()
