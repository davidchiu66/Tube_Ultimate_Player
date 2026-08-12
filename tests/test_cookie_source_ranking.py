"""自动模式下没有探测结果时的兜底 Cookie 源必须按「读得出来」排，不是按「谁是默认」。

背景：Chromium 系从 127 起有 App-Bound Encryption，yt-dlp 多半只报 DPAPI 解密失败；
Firefox 的值是明文。原先兜底取 `detect_browser_cookie_sources()[0]`（默认浏览器），
默认浏览器是 Chromium 系时就读不出 Cookie —— 表现为「识别到 Firefox 却读不出来，
只有把 Firefox 设为默认浏览器才行」。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from services import config_service
from services.config_service import (
    ConfigService,
    detect_browser_cookie_source,
    is_firefox_cookie_spec,
    rank_cookie_sources,
)


DETECTED = [
    ("默认浏览器 - Google Chrome (Default)", "chrome:Default"),
    ("Brave (Default)", "brave:Default"),
    ("Firefox (abc.default-release)", "firefox:abc.default-release"),
    ("Microsoft Edge (Default)", "edge:Default"),
]


class FirefoxSpecTests(unittest.TestCase):
    def test_specs_with_profiles_and_absolute_paths_are_recognised(self) -> None:
        for spec in ("firefox", "firefox:abc.default", r"firefox:E:\Portable\Data\profile", "FireFox:p"):
            self.assertTrue(is_firefox_cookie_spec(spec), spec)

    def test_other_browsers_are_not_firefox(self) -> None:
        for spec in ("chrome:Default", "edge:Default", "opera", "", r"chrome:E:\x\Default"):
            self.assertFalse(is_firefox_cookie_spec(spec), spec)


class RankingTests(unittest.TestCase):
    def test_firefox_moves_ahead_of_every_chromium_source(self) -> None:
        ranked = rank_cookie_sources(DETECTED)

        self.assertEqual(ranked[0][1], "firefox:abc.default-release")

    def test_order_within_a_kind_is_preserved(self) -> None:
        """档内保持发现顺序，默认浏览器仍排在同类最前。"""
        ranked = rank_cookie_sources(DETECTED)

        self.assertEqual([value for _label, value in ranked[1:]], ["chrome:Default", "brave:Default", "edge:Default"])

    def test_multiple_firefox_profiles_keep_their_relative_order(self) -> None:
        sources = [
            ("默认浏览器 - Brave", "brave:Default"),
            ("Firefox (first)", "firefox:first"),
            ("Firefox (second)", "firefox:second"),
        ]

        self.assertEqual(
            [value for _label, value in rank_cookie_sources(sources)],
            ["firefox:first", "firefox:second", "brave:Default"],
        )

    def test_empty_input_is_fine(self) -> None:
        self.assertEqual(rank_cookie_sources([]), [])


class FallbackSourceTests(unittest.TestCase):
    def test_fallback_prefers_firefox_over_the_default_browser(self) -> None:
        with mock.patch.object(config_service, "detect_browser_cookie_sources", return_value=DETECTED):
            self.assertEqual(detect_browser_cookie_source(), "firefox:abc.default-release")

    def test_fallback_keeps_the_default_browser_when_firefox_is_absent(self) -> None:
        sources = [item for item in DETECTED if not item[1].startswith("firefox")]
        with mock.patch.object(config_service, "detect_browser_cookie_sources", return_value=sources):
            self.assertEqual(detect_browser_cookie_source(), "chrome:Default")

    def test_no_browser_yields_empty(self) -> None:
        with mock.patch.object(config_service, "detect_browser_cookie_sources", return_value=[]):
            self.assertEqual(detect_browser_cookie_source(), "")


class AutoModeTests(unittest.TestCase):
    """ConfigService 的两条兜底路径都要吃到新排序。"""

    def _config(self) -> ConfigService:
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        config = ConfigService(
            default_path=Path("config/default_config.json"),
            user_path=Path(temp.name) / "user.json",
        )
        config.set("youtube.cookie_browser", "auto")
        config.set("youtube.cookie_browser_profile", "")
        return config

    def test_site_fallback_uses_firefox_when_no_probe_result_is_stored(self) -> None:
        config = self._config()
        with mock.patch.object(config_service, "detect_browser_cookie_sources", return_value=DETECTED):
            self.assertEqual(config.auto_cookie_browser_for_site("youtube"), "firefox:abc.default-release")
            self.assertEqual(config.auto_cookie_browser(), "firefox:abc.default-release")
            self.assertEqual(config.cookie_browser(), "firefox:abc.default-release")

    def test_probe_result_still_wins_over_the_ranking(self) -> None:
        config = self._config()
        config.set_probed_cookie_browsers({"youtube": "edge:Default"})
        with mock.patch.object(config_service, "detect_browser_cookie_sources", return_value=DETECTED):
            self.assertEqual(config.auto_cookie_browser_for_site("youtube"), "edge:Default")

    def test_explicit_selection_is_untouched(self) -> None:
        config = self._config()
        config.set("youtube.cookie_browser", "edge")
        config.set("youtube.cookie_browser_profile", "Profile 2")
        with mock.patch.object(config_service, "detect_browser_cookie_sources", return_value=DETECTED):
            self.assertEqual(config.explicit_cookie_browser(), "edge:Profile 2")
            self.assertEqual(config.auto_cookie_browser_for_site("youtube"), "")


class WindowsFirefoxSpecTests(unittest.TestCase):
    """标准位置给目录名，Microsoft Store 版必须给绝对路径。"""

    def _sources(self, tmp: Path) -> list[tuple[str, str]]:
        env = {"LOCALAPPDATA": str(tmp / "Local"), "APPDATA": str(tmp / "Roaming")}
        with (
            mock.patch.object(config_service, "detect_portable_default_browser_sources", return_value=[]),
            mock.patch.object(config_service, "_detect_default_windows_browser", return_value="firefox"),
        ):
            return config_service.detect_browser_cookie_sources(platform_name="win32", environ=env)

    def test_standard_profile_is_listed_by_directory_name(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "Roaming" / "Mozilla" / "Firefox" / "Profiles" / "abc.default-release"
            profile.mkdir(parents=True)
            (profile / "cookies.sqlite").write_bytes(b"")

            values = [value for _label, value in self._sources(root)]

            self.assertEqual(values, ["firefox:abc.default-release"])

    def test_microsoft_store_profile_is_listed_by_absolute_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = (
                root
                / "Local"
                / "Packages"
                / "Mozilla.Firefox_abc"
                / "LocalCache"
                / "Roaming"
                / "Mozilla"
                / "Firefox"
                / "Profiles"
                / "store.default-release"
            )
            profile.mkdir(parents=True)
            (profile / "cookies.sqlite").write_bytes(b"")

            values = [value for _label, value in self._sources(root)]

            # 裸目录名会被 yt-dlp 拼到 %APPDATA% 下扑空，只能给绝对路径。
            self.assertEqual(values, [f"firefox:{profile}"])


if __name__ == "__main__":
    unittest.main()
