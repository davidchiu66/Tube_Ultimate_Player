"""E4 验证：按站点探测「哪个浏览器登录过该站点」。

用临时目录伪造浏览器 Cookie 库：真建 cookies / moz_cookies 表，只塞 host 与 name，
不需要真实加密值 —— 这也正是实现的关键性质：探测从不读取 value 列。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from services.cookie_probe_service import (
    CookieDatabase,
    _has_login_cookie,
    probe_site_cookie_browsers,
    probe_site_cookie_browsers_detailed,
)


def make_chromium_db(path: Path, entries: list[tuple[str, str]]) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value BLOB)")
    conn.executemany("INSERT INTO cookies (host_key, name, value) VALUES (?, ?, ?)", [(h, n, b"enc") for h, n in entries])
    conn.commit()
    conn.close()


def make_firefox_db(path: Path, entries: list[tuple[str, str]]) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT)")
    conn.executemany("INSERT INTO moz_cookies (host, name, value) VALUES (?, ?, ?)", [(h, n, "v") for h, n in entries])
    conn.commit()
    conn.close()


class ProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _chromium(self, name: str, entries: list[tuple[str, str]]) -> CookieDatabase:
        path = self.root / f"{name}.db"
        make_chromium_db(path, entries)
        return CookieDatabase(browser_spec=name, path=path, kind="chromium")

    def _firefox(self, name: str, entries: list[tuple[str, str]]) -> CookieDatabase:
        path = self.root / f"{name}.db"
        make_firefox_db(path, entries)
        return CookieDatabase(browser_spec=name, path=path, kind="firefox")

    def test_selects_browser_with_bilibili_login(self) -> None:
        edge = self._chromium("edge:Default", [(".example.com", "foo")])
        chrome = self._chromium("chrome:Default", [(".bilibili.com", "SESSDATA")])

        result = probe_site_cookie_browsers(("bilibili",), [edge, chrome])

        self.assertEqual(result, {"bilibili": "chrome:Default"})

    def test_sites_can_pick_different_browsers(self) -> None:
        chrome = self._chromium("chrome:Default", [(".bilibili.com", "SESSDATA")])
        edge = self._chromium("edge:Default", [(".youtube.com", "LOGIN_INFO")])

        result = probe_site_cookie_browsers(("bilibili", "youtube"), [chrome, edge])

        self.assertEqual(result, {"bilibili": "chrome:Default", "youtube": "edge:Default"})

    def test_first_match_wins(self) -> None:
        first = self._chromium("edge:Default", [(".bilibili.com", "SESSDATA")])
        second = self._chromium("chrome:Default", [(".bilibili.com", "SESSDATA")])

        result = probe_site_cookie_browsers(("bilibili",), [first, second])

        self.assertEqual(result["bilibili"], "edge:Default")

    def test_no_login_returns_empty(self) -> None:
        db = self._chromium("chrome:Default", [(".bilibili.com", "buvid3")])

        result = probe_site_cookie_browsers(("bilibili", "youtube"), [db])

        self.assertEqual(result, {})

    def test_firefox_schema_is_supported(self) -> None:
        db = self._firefox("firefox:p", [(".youtube.com", "SAPISID")])

        result = probe_site_cookie_browsers(("youtube",), [db])

        self.assertEqual(result, {"youtube": "firefox:p"})

    def test_missing_file_is_skipped(self) -> None:
        missing = CookieDatabase(browser_spec="gone:Default", path=self.root / "nope.db", kind="chromium")
        good = self._chromium("chrome:Default", [(".bilibili.com", "bili_jct")])

        result = probe_site_cookie_browsers(("bilibili",), [missing, good])

        self.assertEqual(result, {"bilibili": "chrome:Default"})

    def test_broken_database_does_not_abort_the_round(self) -> None:
        broken = self.root / "broken.db"
        broken.write_bytes(b"not a sqlite file")
        good = self._chromium("chrome:Default", [(".bilibili.com", "DedeUserID")])

        result = probe_site_cookie_browsers(
            ("bilibili",),
            [CookieDatabase(browser_spec="broken:Default", path=broken, kind="chromium"), good],
        )

        self.assertEqual(result, {"bilibili": "chrome:Default"})

    def test_query_never_touches_value_column(self) -> None:
        """探测只读 host 与 name —— 绝不接触也不解密凭据内容。"""
        executed: list[str] = []

        class RecordingConn:
            def execute(self, query, params=None):
                executed.append(query)

                class Cursor:
                    @staticmethod
                    def fetchone():
                        return (1,)

                return Cursor()

        self.assertTrue(_has_login_cookie(RecordingConn(), "chromium", "bilibili"))
        self.assertEqual(len(executed), 1)
        self.assertNotIn("value", executed[0].lower())

    def test_unknown_site_is_rejected(self) -> None:
        db = self._chromium("chrome:Default", [(".bilibili.com", "SESSDATA")])

        self.assertEqual(probe_site_cookie_browsers(("weibo",), [db]), {})

    def test_stops_once_every_site_matched(self) -> None:
        both = self._chromium("chrome:Default", [(".bilibili.com", "SESSDATA"), (".youtube.com", "SID")])
        unused = CookieDatabase(browser_spec="never:Default", path=self.root / "absent.db", kind="chromium")

        result = probe_site_cookie_browsers(("bilibili", "youtube"), [both, unused])

        self.assertEqual(set(result), {"bilibili", "youtube"})
        self.assertNotIn("never:Default", result.values())

    def test_youtube_login_names_cover_google_domain_variants(self) -> None:
        # 不同浏览器留下的标志 Cookie 不一致：只有 HSID/SSID 也应当算已登录。
        db = self._chromium("chrome:Default", [(".google.com", "HSID"), ("accounts.google.com", "SSID")])

        self.assertEqual(probe_site_cookie_browsers(("youtube",), [db]), {"youtube": "chrome:Default"})

    def test_locked_database_is_reported_as_unreadable(self) -> None:
        """运行中的 Chromium 会独占 Cookies 库 —— 读不到 ≠ 没登录，必须分开报告。"""
        locked = self.root / "locked.db"
        locked.write_bytes(b"not a sqlite file")
        good = self._chromium("chrome:Default", [(".youtube.com", "SAPISID")])

        report = probe_site_cookie_browsers_detailed(
            ("youtube",),
            [CookieDatabase(browser_spec="brave:Default", path=locked, kind="chromium"), good],
        )

        self.assertEqual(report.matches, {"youtube": "chrome:Default"})
        self.assertEqual(report.unreadable, ["brave:Default"])

    def test_readable_databases_are_not_listed_as_unreadable(self) -> None:
        db = self._chromium("chrome:Default", [(".youtube.com", "LOGIN_INFO")])

        report = probe_site_cookie_browsers_detailed(("youtube",), [db])

        self.assertEqual(report.unreadable, [])


if __name__ == "__main__":
    unittest.main()
