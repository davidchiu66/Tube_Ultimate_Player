"""yt-dlp 对「Cookie 无效」不报错：退出码 0、entries 为空。

真实故障：cookie_youtube.txt 里是一条无效 Cookie，yt-dlp 退出码 0、只回 834 字节
JSON（0 条 entries），首页于是一片空白且没有任何提示。只靠 returncode 判断永远
发现不了这种失败。
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from resolver.youtube_resolver import YoutubeResolver, _json_entry_count
from services.config_service import ConfigService


def payload(count: int) -> str:
    entries = [
        {"id": f"vid{index:07d}xxx"[:11], "title": f"视频 {index}", "url": f"https://www.youtube.com/watch?v=abcdefghij{index}"}
        for index in range(count)
    ]
    return json.dumps({"entries": entries})


def completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["yt-dlp"], returncode=returncode, stdout=stdout, stderr="")


class EntryCountTests(unittest.TestCase):
    def test_counts_only_dict_entries(self) -> None:
        self.assertEqual(_json_entry_count(json.dumps({"entries": [{"a": 1}, "junk", None]})), 1)

    def test_tolerates_broken_payloads(self) -> None:
        for value in ("", "not json", "[]", json.dumps({"entries": None})):
            self.assertEqual(_json_entry_count(value), 0)


class EmptyResultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        default_path = root / "default.json"
        default_path.write_text("{}", encoding="utf-8")
        self.config = ConfigService(default_path=default_path, user_path=root / "user.json")
        cookie = root / "cookie_youtube.txt"
        cookie.write_text("Cookie: SID=stale", encoding="utf-8")
        self.config.set("cookies.youtube.file", str(cookie))
        self.config.set("content.default_home", "youtube")
        self.cookie_path = cookie

        self.resolver = YoutubeResolver.__new__(YoutubeResolver)
        self.resolver.config = self.config
        self.resolver.ytdlp_path = Path("yt-dlp")

    def _run_home(self, outputs: list[str]):
        calls: list[list[str]] = []

        def fake_run(command, _url, _attempt):
            calls.append(command)
            return completed(outputs[min(len(calls) - 1, len(outputs) - 1)])

        with patch.object(YoutubeResolver, "_run_ytdlp", side_effect=fake_run):
            with patch(
                "resolver.youtube_resolver.detect_browser_cookie_sources",
                return_value=[("Firefox", "firefox:p1"), ("Chrome", "chrome:Default")],
            ):
                with patch("resolver.youtube_resolver.prepare_cookie_file", side_effect=lambda path, _url: path):
                    return self.resolver.fetch_home_videos(page=1, page_size=5), calls

    def test_empty_home_retries_with_browser_cookies(self) -> None:
        (videos, has_next), calls = self._run_home([payload(0), payload(3)])

        self.assertEqual(len(videos), 3)
        self.assertFalse(has_next)
        # 第一次用 Cookie 文件，重试改用浏览器 Cookie。
        self.assertIn("--cookies", calls[0])
        self.assertIn("--cookies-from-browser", calls[1])

    def test_all_empty_raises_a_cookie_specific_error(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            self._run_home([payload(0)])

        message = str(ctx.exception)
        self.assertIn("Cookie", message)
        self.assertIn(str(self.cookie_path), message)

    def test_error_names_the_browser_when_a_browser_is_in_use(self) -> None:
        self.config.set("cookies.youtube.file", "")
        self.config.set("youtube.cookie_browser", "brave:Default")

        with self.assertRaises(RuntimeError) as ctx:
            self._run_home([payload(0)])

        self.assertIn("brave:Default", str(ctx.exception))

    def test_error_tells_the_user_when_no_cookie_is_configured(self) -> None:
        self.config.set("cookies.youtube.file", "")
        self.config.set("youtube.cookie_browser", "")

        with self.assertRaises(RuntimeError) as ctx:
            self._run_home([payload(0)])

        self.assertIn("没有配置任何 Cookie", str(ctx.exception))

    def test_non_empty_home_does_not_retry(self) -> None:
        (videos, _has_next), calls = self._run_home([payload(2)])

        self.assertEqual(len(videos), 2)
        self.assertEqual(len(calls), 1)


class CookieSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        default_path = self.root / "default.json"
        default_path.write_text("{}", encoding="utf-8")
        self.config = ConfigService(default_path=default_path, user_path=self.root / "user.json")
        self.resolver = YoutubeResolver.__new__(YoutubeResolver)
        self.resolver.config = self.config
        self.resolver.ytdlp_path = Path("yt-dlp")

    def test_explicit_browser_wins(self) -> None:
        self.config.set("youtube.cookie_browser", "chrome:Default")

        self.assertEqual(self.resolver._cookie_source("https://www.youtube.com/"), ("browser", "chrome:Default"))

    def test_cookie_file_is_reported_when_present(self) -> None:
        cookie = self.root / "c.txt"
        cookie.write_text("Cookie: SID=x", encoding="utf-8")
        self.config.set("cookies.youtube.file", str(cookie))

        self.assertEqual(self.resolver._cookie_source("https://www.youtube.com/"), ("file", str(cookie)))

    def test_probed_browser_is_reported_in_auto_mode(self) -> None:
        self.config.set("youtube.cookie_browser", "auto")
        self.config.set("cookies.youtube.auto_browser", "brave:Default")

        self.assertEqual(self.resolver._cookie_source("https://www.youtube.com/"), ("browser", "brave:Default"))

    def test_none_when_nothing_configured(self) -> None:
        self.config.set("youtube.cookie_browser", "")

        self.assertEqual(self.resolver._cookie_source("https://www.youtube.com/"), ("none", ""))


if __name__ == "__main__":
    unittest.main()
