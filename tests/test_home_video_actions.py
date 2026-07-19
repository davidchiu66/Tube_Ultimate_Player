from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from resolver.models import HomeVideo
from ui.home_page import HomePage


class HomeVideoActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.page = HomePage()
        self.video = HomeVideo(
            video_id="video-1",
            title="Video",
            webpage_url="https://example.com/watch/video-1",
        )

    def tearDown(self) -> None:
        self.page.close()

    def test_header_no_longer_contains_play_selected_button(self) -> None:
        self.assertFalse(hasattr(self.page, "play_button"))

    def test_home_card_play_button_follows_download_and_emits_url(self) -> None:
        urls: list[str] = []
        self.page.play_requested.connect(urls.append)
        self.page.set_videos([self.video], mode="home")
        card = self.page._cards[0]
        action_layout = card.layout().itemAt(0).layout()

        self.assertEqual(
            [action_layout.itemAt(index).widget().text() for index in range(3)],
            ["收藏", "下载", "播放"],
        )
        card.play_button.click()

        self.assertEqual(urls, [self.video.webpage_url])

    def test_search_card_play_button_emits_url(self) -> None:
        urls: list[str] = []
        self.page.play_requested.connect(urls.append)
        self.page.set_videos([self.video], mode="search", keyword="Video")

        self.page._cards[0].play_button.click()

        self.assertEqual(urls, [self.video.webpage_url])


if __name__ == "__main__":
    unittest.main()
