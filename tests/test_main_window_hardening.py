"""C3 / F3 / P4 验证：关闭守卫、空清晰度兜底、页面惰性构造。"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from resolver.models import VideoInfo, VideoQuality  # noqa: E402
from ui.main_window import MainWindow, _skip_after_shutdown  # noqa: E402


LAZY_PAGE_NAMES = (
    "playlist",
    "download",
    "favorite",
    "history",
    "settings",
    "about",
)


def make_quality(label: str = "1080p") -> VideoQuality:
    return VideoQuality(
        label=label,
        height=1080,
        width=1920,
        fps=30,
        vcodec="avc1",
        acodec="mp4a.40.2",
        ext="mp4",
        format_id="137",
        video_url="https://cdn/video.mp4",
    )


class ShutdownGuardTests(unittest.TestCase):
    """C3：关闭流程开始后，迟到的 worker 回调必须被丢弃。"""

    def setUp(self) -> None:
        calls: list[str] = []

        class Fake:
            _shutting_down = False

            @_skip_after_shutdown
            def on_success(self, payload: str) -> str:
                calls.append(payload)
                return payload

        self.calls = calls
        self.fake = Fake()

    def test_callback_runs_before_shutdown(self) -> None:
        self.assertEqual(self.fake.on_success("ok"), "ok")
        self.assertEqual(self.calls, ["ok"])

    def test_callback_is_dropped_after_shutdown(self) -> None:
        self.fake._shutting_down = True

        self.assertIsNone(self.fake.on_success("late"))
        self.assertEqual(self.calls, [])

    def test_wrapper_keeps_original_name(self) -> None:
        self.assertEqual(type(self.fake).on_success.__name__, "on_success")


class DefaultQualityTests(unittest.TestCase):
    """F3：没有任何清晰度时返回 None，而不是让 StopIteration 逃逸。"""

    def _state(self, preferred: str = "Auto"):
        return SimpleNamespace(config=SimpleNamespace(get=lambda _key, _default=None: preferred))

    def test_empty_qualities_returns_none(self) -> None:
        video = VideoInfo(video_id="abc", title="无清晰度")

        result = MainWindow._select_default_quality(self._state(), video)

        self.assertIsNone(result)

    def test_preferred_quality_wins_when_present(self) -> None:
        video = VideoInfo(
            video_id="abc",
            title="有清晰度",
            qualities={"720p": make_quality("720p"), "1080p": make_quality("1080p")},
        )

        result = MainWindow._select_default_quality(self._state("1080p"), video)

        self.assertEqual(result.label, "1080p")

    def test_auto_falls_back_to_first_quality(self) -> None:
        video = VideoInfo(
            video_id="abc",
            title="自动",
            qualities={"720p": make_quality("720p"), "1080p": make_quality("1080p")},
        )

        result = MainWindow._select_default_quality(self._state("Auto"), video)

        self.assertEqual(result.label, "720p")

    def test_missing_preferred_quality_falls_back(self) -> None:
        video = VideoInfo(video_id="abc", title="缺失", qualities={"720p": make_quality("720p")})

        result = MainWindow._select_default_quality(self._state("4320p"), video)

        self.assertEqual(result.label, "720p")


class LazyPageTests(unittest.TestCase):
    """P4：启动只构建首页与播放页，其余页面首访时才建。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        # 构造函数会 singleShot 一次真实的首页加载，测试里不需要联网。
        patcher = patch.object(MainWindow, "load_home", lambda _self: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        # MainWindow 用的是真实 ConfigService / DownloadManager，关闭时会把配置与
        # 下载任务写回**真实**运行目录。单测不许碰用户数据，这里把两处落盘掐掉。
        for target, attribute in (
            ("services.config_service.ConfigService.save", None),
            ("download.download_manager.DownloadManager._save_tasks", None),
        ):
            silence = patch(target, lambda *_args, **_kwargs: None)
            silence.start()
            self.addCleanup(silence.stop)
        self.window = MainWindow()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)

    def test_startup_only_builds_visible_pages(self) -> None:
        self.assertEqual(self.window._lazy_pages, {})
        self.assertEqual(self.window.stack.count(), 2)

    def test_each_page_is_built_once_on_first_access(self) -> None:
        for offset, name in enumerate(LAZY_PAGE_NAMES, start=1):
            page = getattr(self.window, f"{name}_page")
            self.assertIsNotNone(page)
            self.assertEqual(self.window.stack.count(), 2 + offset)
            # 第二次访问必须命中缓存，不能再往 stack 里塞一份。
            self.assertIs(getattr(self.window, f"{name}_page"), page)
            self.assertEqual(self.window.stack.count(), 2 + offset)

        self.assertEqual(sorted(self.window._lazy_pages), sorted(LAZY_PAGE_NAMES))

    def test_created_page_lookup_does_not_build(self) -> None:
        self.assertIsNone(self.window._created_page("settings"))
        self.assertEqual(self.window._lazy_pages, {})


if __name__ == "__main__":
    unittest.main()
