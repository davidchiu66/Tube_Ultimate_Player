"""换浏览器重试的候选顺序。

两处缺陷曾让重试白跑：`current` 拿默认浏览器算（自动模式下真正用出去的却是按站点
探测出的浏览器），于是已经失败过的那个源会被当成新候选再试一遍；候选又按发现顺序
排，Firefox 被压在几个读不出 Cookie 的 Chromium 后面。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from resolver.youtube_resolver import YoutubeResolver
from services.config_service import ConfigService


DETECTED = [
    ("默认浏览器 - Brave (Default)", "brave:Default"),
    ("Google Chrome (Default)", "chrome:Default"),
    ("Firefox (abc.default-release)", "firefox:abc.default-release"),
]


class AlternateBrowserOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "default.json").write_text("{}", encoding="utf-8")
        self.config = ConfigService(default_path=root / "default.json", user_path=root / "user.json")
        self.config.set("youtube.cookie_browser", "auto")
        self.resolver = YoutubeResolver.__new__(YoutubeResolver)
        self.resolver.config = self.config
        self.resolver.ytdlp_path = Path("yt-dlp")

    def _alternates(self, **kwargs) -> list[str]:
        with patch(
            "resolver.youtube_resolver.detect_browser_cookie_sources",
            return_value=DETECTED,
        ):
            return self.resolver._alternate_cookie_browsers(**kwargs)

    def test_firefox_is_retried_before_the_chromium_browsers(self) -> None:
        self.config.set_probed_cookie_browsers({"youtube": "brave:Default"})

        self.assertEqual(self._alternates()[0], "firefox:abc.default-release")

    def test_the_probed_browser_for_the_site_is_not_retried(self) -> None:
        """自动模式下真正用出去的是按站点探测的结果，重试必须跳过它。"""
        self.config.set_probed_cookie_browsers({"youtube": "chrome:Default"})

        self.assertNotIn("chrome:Default", self._alternates())

    def test_a_browser_probed_for_another_site_is_still_a_candidate(self) -> None:
        self.config.set_probed_cookie_browsers({"bilibili": "chrome:Default"})

        self.assertIn("chrome:Default", self._alternates())

    def test_explicit_selection_is_skipped_on_retry(self) -> None:
        self.config.set("youtube.cookie_browser", "brave")
        self.config.set("youtube.cookie_browser_profile", "Default")

        alternates = self._alternates()

        self.assertNotIn("brave:Default", alternates)
        self.assertEqual(alternates[0], "firefox:abc.default-release")

    def test_include_current_keeps_everything_but_still_ranks_firefox_first(self) -> None:
        self.config.set_probed_cookie_browsers({"youtube": "brave:Default"})

        alternates = self._alternates(include_current=True)

        self.assertEqual(
            alternates,
            ["firefox:abc.default-release", "brave:Default", "chrome:Default"],
        )

    def test_duplicate_specs_are_collapsed(self) -> None:
        with patch(
            "resolver.youtube_resolver.detect_browser_cookie_sources",
            return_value=[("默认浏览器 - Chrome", "chrome:Default"), ("Chrome", "chrome:Default")],
        ):
            self.assertEqual(self.resolver._alternate_cookie_browsers(), ["chrome:Default"])


class DownloadWorkerOrderTests(unittest.TestCase):
    """下载侧用的是同一套顺序。"""

    def test_download_worker_ranks_firefox_first(self) -> None:
        from download.download_worker import DownloadWorker

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "default.json").write_text("{}", encoding="utf-8")
        config = ConfigService(default_path=root / "default.json", user_path=root / "user.json")
        config.set("youtube.cookie_browser", "auto")
        config.set_probed_cookie_browsers({"youtube": "brave:Default"})

        worker = DownloadWorker.__new__(DownloadWorker)
        worker.config = config
        worker.task = SimpleNamespace(url="https://www.youtube.com/watch?v=abcdefghijk")

        with patch(
            "download.download_worker.detect_browser_cookie_sources",
            return_value=DETECTED,
        ):
            alternates = worker._alternate_cookie_browsers()

        self.assertEqual(alternates[0], "firefox:abc.default-release")
        self.assertNotIn("brave:Default", alternates)


if __name__ == "__main__":
    unittest.main()
