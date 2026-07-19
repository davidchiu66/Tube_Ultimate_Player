from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from resolver.models import VideoInfo
from ui.main_window import MainWindow


class BrowserPlaybackTests(unittest.TestCase):
    def test_current_video_webpage_is_opened_in_default_browser(self) -> None:
        messages: list[str] = []
        state = SimpleNamespace(
            current_video=VideoInfo(
                video_id="video-1",
                title="Video",
                webpage_url="https://www.youtube.com/watch?v=video-1",
            ),
            toast=SimpleNamespace(show_message=messages.append),
        )

        with patch("ui.main_window.QDesktopServices.openUrl", return_value=True) as open_url:
            MainWindow._open_current_video_in_browser(state)

        self.assertEqual(open_url.call_count, 1)
        self.assertEqual(
            open_url.call_args.args[0].toString(),
            "https://www.youtube.com/watch?v=video-1",
        )
        self.assertEqual(messages, [])

    def test_missing_video_webpage_shows_error(self) -> None:
        messages: list[str] = []
        state = SimpleNamespace(
            current_video=VideoInfo(video_id="video-1", title="Video"),
            toast=SimpleNamespace(show_message=messages.append),
        )

        with patch("ui.main_window.QDesktopServices.openUrl") as open_url:
            MainWindow._open_current_video_in_browser(state)

        open_url.assert_not_called()
        self.assertEqual(messages, ["浏览器播放失败：视频链接不可用"])


if __name__ == "__main__":
    unittest.main()
