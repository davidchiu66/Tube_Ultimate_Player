"""空 Cookie 文件不得挡住浏览器 Cookie，也不得让整个请求失败。

真实故障：磁盘上留着 0 字节的 cookie_youtube.txt，自动检测模式下它排在浏览器
Cookie 之前，prepare_cookie_file 抛 CookieFormatError，首页加载整体失败
（"Cookie 文件格式不正确"）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.config_service import ConfigService, cookie_file_has_content
from services.cookie_service import CookieFormatError, prepare_cookie_file


NETSCAPE_ROW = ".youtube.com\tTRUE\t/\tTRUE\t9999999999\tSID\tabc"


class CookieFileContentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _file(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_zero_byte_file_has_no_content(self) -> None:
        self.assertFalse(cookie_file_has_content(self._file("empty.txt", "")))

    def test_whitespace_only_file_has_no_content(self) -> None:
        self.assertFalse(cookie_file_has_content(self._file("blank.txt", "\n  \n\t\n")))

    def test_comment_only_netscape_file_has_no_content(self) -> None:
        # 只有头注释、没有任何 Cookie 行 —— 交给 yt-dlp 也带不上 Cookie。
        self.assertFalse(cookie_file_has_content(self._file("header.txt", "# Netscape HTTP Cookie File\n")))

    def test_real_cookie_file_has_content(self) -> None:
        text = f"# Netscape HTTP Cookie File\n{NETSCAPE_ROW}\n"
        self.assertTrue(cookie_file_has_content(self._file("ok.txt", text)))

    def test_raw_header_file_has_content(self) -> None:
        self.assertTrue(cookie_file_has_content(self._file("raw.txt", "Cookie: SID=abc; HSID=def\n")))

    def test_missing_file_has_no_content(self) -> None:
        self.assertFalse(cookie_file_has_content(self.root / "nope.txt"))


class ConfigCookieFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        default_path = self.root / "default.json"
        default_path.write_text("{}", encoding="utf-8")
        self.config = ConfigService(default_path=default_path, user_path=self.root / "user.json")

    def _configure(self, site: str, text: str) -> Path:
        path = self.root / f"cookie_{site}.txt"
        path.write_text(text, encoding="utf-8")
        self.config.set(f"cookies.{site}.file", str(path))
        return path

    def test_empty_cookie_file_reads_as_unconfigured(self) -> None:
        self._configure("youtube", "")

        self.assertEqual(self.config.cookie_file("youtube"), "")

    def test_usable_cookie_file_is_returned(self) -> None:
        path = self._configure("youtube", f"# Netscape HTTP Cookie File\n{NETSCAPE_ROW}\n")

        self.assertEqual(self.config.cookie_file("youtube"), str(path))

    def test_cookie_file_for_url_follows_the_site(self) -> None:
        self._configure("youtube", "")
        bilibili = self._configure("bilibili", "Cookie: SESSDATA=x\n")

        self.assertEqual(self.config.cookie_file_for_url("https://www.youtube.com/watch?v=a"), "")
        self.assertEqual(
            self.config.cookie_file_for_url("https://www.bilibili.com/video/BV1"),
            str(bilibili),
        )


class PrepareCookieFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_empty_file_reports_that_it_is_empty(self) -> None:
        path = self.root / "empty.txt"
        path.write_text("", encoding="utf-8")

        with self.assertRaises(CookieFormatError) as ctx:
            prepare_cookie_file(str(path))

        self.assertIn("空的", str(ctx.exception))

    def test_garbage_file_still_reports_a_format_error(self) -> None:
        path = self.root / "junk.txt"
        path.write_text("这不是 cookie 内容\n没有等号\n", encoding="utf-8")

        with self.assertRaises(CookieFormatError) as ctx:
            prepare_cookie_file(str(path))

        self.assertIn("格式不正确", str(ctx.exception))

    def test_netscape_file_is_passed_through(self) -> None:
        path = self.root / "ok.txt"
        path.write_text(f"# Netscape HTTP Cookie File\n{NETSCAPE_ROW}\n", encoding="utf-8")

        self.assertEqual(prepare_cookie_file(str(path)), str(path))


if __name__ == "__main__":
    unittest.main()
