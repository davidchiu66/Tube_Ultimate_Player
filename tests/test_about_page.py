from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from resolver.youtube_resolver import YoutubeResolver
from ui.about_page import AboutPage


class AboutPageYtdlpVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_about_page_displays_ytdlp_version(self) -> None:
        page = AboutPage()
        page.set_ytdlp_version("2026.07.04")

        self.assertEqual(page.ytdlp_version_label.text(), "yt-dlp 版本：2026.07.04")
        page.close()

    def test_resolver_reads_version_from_selected_executable(self) -> None:
        resolver = YoutubeResolver.__new__(YoutubeResolver)
        resolver.ytdlp_path = Path("yt-dlp")
        result = subprocess.CompletedProcess(
            ["yt-dlp", "--version"], 0, stdout="2026.09.01\n", stderr=""
        )

        with patch("resolver.youtube_resolver.subprocess.run", return_value=result) as run:
            self.assertEqual(resolver.ytdlp_version(), "2026.09.01")

        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["yt-dlp", "--version"])

    def test_resolver_returns_empty_when_version_command_fails(self) -> None:
        resolver = YoutubeResolver.__new__(YoutubeResolver)
        resolver.ytdlp_path = Path("missing-yt-dlp")

        with patch(
            "resolver.youtube_resolver.subprocess.run",
            side_effect=FileNotFoundError("missing-yt-dlp"),
        ):
            self.assertEqual(resolver.ytdlp_version(), "")


if __name__ == "__main__":
    unittest.main()
