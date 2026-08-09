from __future__ import annotations

import unittest
from unittest import mock

from download import command_builder
from download.models import DownloadTask


def _task(url: str = "https://www.youtube.com/watch?v=abc") -> DownloadTask:
    return DownloadTask(
        url=url,
        video_id="abc",
        source_site="youtube",
        title="t",
        quality_label="Auto",
        format_selector="best",
        save_dir=".",
        expected_bytes=None,
    )


class _FakeConfig:
    def __init__(self, cookie_file: str = "", browser: str = "", auto: str = "") -> None:
        self._cookie_file = cookie_file
        self._browser = browser
        self._auto = auto

    def js_runtime(self) -> str:
        return ""

    def download_ffmpeg_location(self) -> str:
        return ""

    def effective_proxy(self):
        return ("", "")

    def explicit_cookie_browser(self) -> str:
        return self._browser

    def cookie_file_for_url(self, url: str) -> str:
        return self._cookie_file

    def cookie_site_for_url(self, url: str) -> str:
        return "youtube"

    def auto_cookie_browser_for_site(self, site: str) -> str:
        return self._auto


class ExplicitCookieFileTest(unittest.TestCase):
    def test_explicit_cookie_file_wins_over_browser(self) -> None:
        config = _FakeConfig(browser="chrome:Default")
        command = command_builder.build_download_command(
            _task(), config, explicit_cookie_file="C:/tmp/chromium.txt"
        )
        self.assertIn("--cookies", command)
        self.assertIn("C:/tmp/chromium.txt", command)
        self.assertNotIn("--cookies-from-browser", command)

    def test_without_explicit_file_uses_browser(self) -> None:
        config = _FakeConfig(browser="chrome:Default")
        command = command_builder.build_download_command(_task(), config)
        self.assertIn("--cookies-from-browser", command)


class ChromiumFallbackFileTest(unittest.TestCase):
    def test_skips_firefox_and_returns_first_success(self) -> None:
        config = _FakeConfig(auto="")
        sources = [
            ("Firefox (default)", "firefox:default"),
            ("Google Chrome (Default)", "chrome:Default"),
            ("Microsoft Edge (Default)", "edge:Default"),
        ]
        calls: list[str] = []

        def fake_extract(spec: str, url: str) -> str:
            calls.append(spec)
            return "C:/tmp/out.txt" if spec == "chrome:Default" else ""

        with (
            mock.patch.object(command_builder, "detect_browser_cookie_sources", return_value=sources),
            mock.patch.object(command_builder, "extract_cookies_to_netscape", side_effect=fake_extract),
        ):
            result = command_builder.chromium_cookie_fallback_file(config, "https://www.youtube.com/")

        self.assertEqual(result, "C:/tmp/out.txt")
        self.assertNotIn("firefox:default", calls)
        self.assertEqual(calls[0], "chrome:Default")

    def test_all_fail_returns_empty(self) -> None:
        config = _FakeConfig()
        sources = [("Google Chrome (Default)", "chrome:Default")]
        with (
            mock.patch.object(command_builder, "detect_browser_cookie_sources", return_value=sources),
            mock.patch.object(command_builder, "extract_cookies_to_netscape", return_value=""),
        ):
            self.assertEqual(
                command_builder.chromium_cookie_fallback_file(config, "https://www.youtube.com/"),
                "",
            )

    def test_prefers_configured_browser_first(self) -> None:
        config = _FakeConfig(browser="edge:Default")
        sources = [("Google Chrome (Default)", "chrome:Default")]
        calls: list[str] = []

        with (
            mock.patch.object(command_builder, "detect_browser_cookie_sources", return_value=sources),
            mock.patch.object(
                command_builder,
                "extract_cookies_to_netscape",
                side_effect=lambda spec, url: calls.append(spec) or "",
            ),
        ):
            command_builder.chromium_cookie_fallback_file(config, "https://www.youtube.com/")

        self.assertEqual(calls[0], "edge:Default")


if __name__ == "__main__":
    unittest.main()
