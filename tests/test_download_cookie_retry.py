"""下载链路的浏览器 Cookie 重试。

真实故障：自动检测选中 brave:Default，yt-dlp 报
"Failed to decrypt with DPAPI"（Chrome 127+ / Brave / Edge 的 App-Bound
Encryption），下载直接失败。解析链路早就有换浏览器重试，下载链路没有 ——
于是「首页有内容、能播放，但下载失败」。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from download.command_builder import (
    build_download_command,
    should_retry_with_alternate_browser,
    should_retry_with_cookie_file,
)
from download.download_worker import DownloadWorker
from download.models import DownloadTask
from services.config_service import ConfigService
from services.cookie_probe_service import CookieDatabase, _ranked

DPAPI_ERROR = "ERROR: Failed to decrypt with DPAPI. See  https://github.com/yt-dlp/yt-dlp/issues/10927  for more info"


class RetryDetectionTests(unittest.TestCase):
    def test_browser_cookie_failures_are_detected(self) -> None:
        for text in (
            DPAPI_ERROR,
            "ERROR: could not copy Chrome cookie database",
            "ERROR: Could not find Chrome cookies database",
            "ERROR: Could not decrypt cookie value",
        ):
            self.assertTrue(should_retry_with_alternate_browser(text), text)
            self.assertTrue(should_retry_with_cookie_file(text), text)

    def test_unrelated_failures_do_not_trigger_a_browser_retry(self) -> None:
        for text in (
            "ERROR: Video unavailable",
            "ERROR: unable to download video data: HTTP Error 403",
            "",
        ):
            self.assertFalse(should_retry_with_alternate_browser(text), text)


class CommandOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        default_path = self.root / "default.json"
        default_path.write_text("{}", encoding="utf-8")
        self.config = ConfigService(default_path=default_path, user_path=self.root / "user.json")
        self.config.set("download.save_dir", str(self.root))
        self.task = DownloadTask(
            url="https://www.youtube.com/watch?v=abcdefghijk",
            title="t",
            video_id="abcdefghijk",
            source_site="youtube",
            save_dir=str(self.root),
        )

    def _cookie_arg(self, command: list[str]) -> tuple[str, str]:
        for flag in ("--cookies-from-browser", "--cookies"):
            if flag in command:
                return flag, command[command.index(flag) + 1]
        return ("", "")

    def test_override_replaces_the_probed_browser(self) -> None:
        self.config.set("youtube.cookie_browser", "auto")
        self.config.set("cookies.youtube.auto_browser", "brave:Default")

        flag, value = self._cookie_arg(build_download_command(self.task, self.config))
        self.assertEqual((flag, value), ("--cookies-from-browser", "brave:Default"))

        flag, value = self._cookie_arg(
            build_download_command(self.task, self.config, override_cookie_browser="firefox:p1")
        )
        self.assertEqual((flag, value), ("--cookies-from-browser", "firefox:p1"))

    def test_override_also_beats_an_explicit_browser(self) -> None:
        self.config.set("youtube.cookie_browser", "brave:Default")

        flag, value = self._cookie_arg(
            build_download_command(self.task, self.config, override_cookie_browser="firefox:p1")
        )
        self.assertEqual((flag, value), ("--cookies-from-browser", "firefox:p1"))


class WorkerRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        default_path = self.root / "default.json"
        default_path.write_text("{}", encoding="utf-8")
        self.config = ConfigService(default_path=default_path, user_path=self.root / "user.json")
        self.config.set("youtube.cookie_browser", "auto")
        self.config.set("cookies.youtube.auto_browser", "brave:Default")
        self.task = DownloadTask(
            url="https://www.youtube.com/watch?v=abcdefghijk",
            title="t",
            video_id="abcdefghijk",
            source_site="youtube",
            save_dir=str(self.root),
        )

    def _worker(self, outcomes: list[tuple[int, str]]):
        worker = DownloadWorker(self.task, self.config)
        attempts: list[str] = []

        class Output:
            def __init__(self, returncode: int, text: str) -> None:
                self.returncode = returncode
                self.text = text
                self.output_path = "out.mp4" if returncode == 0 else ""

            def succeeded(self) -> bool:
                return self.returncode == 0

            def error_message(self) -> str:
                return self.text

        def fake_run(force_cookie_file: bool = False, override_cookie_browser: str = ""):
            attempts.append("file" if force_cookie_file else (override_cookie_browser or "primary"))
            index = min(len(attempts) - 1, len(outcomes) - 1)
            return Output(*outcomes[index])

        worker._run_once = fake_run  # type: ignore[assignment]
        return worker, attempts

    def _run(self, worker):
        completed: list[str] = []
        failed: list[str] = []
        worker.signals.completed.connect(lambda _tid, path: completed.append(path))
        worker.signals.failed.connect(lambda _tid, msg: failed.append(msg))
        with patch(
            "download.download_worker.detect_browser_cookie_sources",
            return_value=[("Brave", "brave:Default"), ("Firefox", "firefox:p1"), ("Chrome", "chrome:Default")],
        ):
            worker.run()
        return completed, failed

    def test_dpapi_failure_retries_with_another_browser(self) -> None:
        worker, attempts = self._worker([(1, DPAPI_ERROR), (0, "")])

        completed, failed = self._run(worker)

        self.assertEqual(completed, ["out.mp4"])
        self.assertEqual(failed, [])
        # 第一次用探测出的 brave，重试跳过它并换成 firefox。
        self.assertEqual(attempts[0], "primary")
        self.assertEqual(attempts[1], "firefox:p1")

    def test_currently_used_browser_is_not_retried(self) -> None:
        worker, attempts = self._worker([(1, DPAPI_ERROR)])

        self._run(worker)

        self.assertNotIn("brave:Default", attempts)

    def test_all_browsers_failing_reports_the_error(self) -> None:
        worker, _attempts = self._worker([(1, DPAPI_ERROR)])

        completed, failed = self._run(worker)

        self.assertEqual(completed, [])
        self.assertEqual(len(failed), 1)
        self.assertIn("DPAPI", failed[0])

    def test_unrelated_failure_does_not_retry_browsers(self) -> None:
        worker, attempts = self._worker([(1, "ERROR: Video unavailable")])

        self._run(worker)

        self.assertEqual(attempts, ["primary"])

    def test_success_on_first_attempt_does_not_retry(self) -> None:
        worker, attempts = self._worker([(0, "")])

        completed, _failed = self._run(worker)

        self.assertEqual(completed, ["out.mp4"])
        self.assertEqual(attempts, ["primary"])


class ProbeRankingTests(unittest.TestCase):
    def test_firefox_is_probed_before_chromium(self) -> None:
        # 探测只能确认 Cookie 名字在不在，确认不了值能否被解密；Chromium 的
        # App-Bound Encryption 会让 yt-dlp 解不出来，所以同样命中时先选 Firefox。
        chromium = CookieDatabase(browser_spec="brave:Default", path=Path("brave.db"), kind="chromium")
        firefox = CookieDatabase(browser_spec="firefox:p1", path=Path("ff.db"), kind="firefox")

        self.assertEqual([db.browser_spec for db in _ranked([chromium, firefox])], ["firefox:p1", "brave:Default"])

    def test_relative_order_within_a_kind_is_stable(self) -> None:
        first = CookieDatabase(browser_spec="edge:Default", path=Path("e.db"), kind="chromium")
        second = CookieDatabase(browser_spec="chrome:Default", path=Path("c.db"), kind="chromium")

        self.assertEqual([db.browser_spec for db in _ranked([first, second])], ["edge:Default", "chrome:Default"])


if __name__ == "__main__":
    unittest.main()
