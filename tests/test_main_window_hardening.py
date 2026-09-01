"""C3 / F3 / P4 验证：关闭守卫、空清晰度兜底、页面惰性构造。"""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect  # noqa: E402
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


def make_quality(label: str = "1080p", *, height: int = 1080, fps: int = 30) -> VideoQuality:
    return VideoQuality(
        label=label,
        height=height,
        width=max(1, height * 16 // 9),
        fps=fps,
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
        normalized = str(preferred or "").strip().lower()
        tier = normalized if normalized in {"high", "medium", "low"} else "high"
        override = "" if not preferred or normalized == "auto" or normalized in {"high", "medium", "low"} else preferred
        return SimpleNamespace(
            config=SimpleNamespace(
                default_quality_tier=lambda: tier,
                default_quality_label_override=lambda: override,
            )
        )

    def test_empty_qualities_returns_none(self) -> None:
        video = VideoInfo(video_id="abc", title="无清晰度")

        result = MainWindow._select_default_quality(self._state(), video)

        self.assertIsNone(result)

    def test_preferred_quality_wins_when_present(self) -> None:
        video = VideoInfo(
            video_id="abc",
            title="有清晰度",
            qualities={
                "720p": make_quality("720p", height=720),
                "1080p": make_quality("1080p", height=1080),
            },
        )

        result = MainWindow._select_default_quality(self._state("1080p"), video)

        self.assertEqual(result.label, "1080p")

    def test_auto_falls_back_to_highest_quality(self) -> None:
        video = VideoInfo(
            video_id="abc",
            title="自动",
            qualities={
                "720p": make_quality("720p", height=720),
                "1080p": make_quality("1080p", height=1080),
            },
        )

        result = MainWindow._select_default_quality(self._state("Auto"), video)

        self.assertEqual(result.label, "1080p")

    def test_medium_selects_the_middle_resolution(self) -> None:
        video = VideoInfo(
            video_id="abc",
            title="中档",
            qualities={
                "480p": make_quality("480p", height=480),
                "1080p": make_quality("1080p", height=1080),
                "720p": make_quality("720p", height=720),
            },
        )

        result = MainWindow._select_default_quality(self._state("medium"), video)

        self.assertEqual(result.label, "720p")

    def test_missing_preferred_quality_falls_back(self) -> None:
        video = VideoInfo(video_id="abc", title="缺失", qualities={"720p": make_quality("720p")})

        result = MainWindow._select_default_quality(self._state("4320p"), video)

        self.assertEqual(result.label, "720p")

    def test_xiaohongshu_automatic_quality_prefers_h264_when_hevc_is_highest(self) -> None:
        video = VideoInfo(
            video_id="xiaohongshu:note",
            source_site="xiaohongshu",
            title="小红书视频",
            qualities={
                "2160p": VideoQuality(
                    label="2160p", width=2160, height=3840, fps=30,
                    vcodec="hevc", acodec="aac", ext="mp4", format_id="hevc",
                    video_url="https://example.test/hevc.mp4",
                ),
                "1080p": VideoQuality(
                    label="1080p", width=1080, height=1920, fps=30,
                    vcodec="h264", acodec="aac", ext="mp4", format_id="h264",
                    video_url="https://example.test/h264.mp4",
                ),
            },
        )

        selected = MainWindow._prefer_compatible_xiaohongshu_quality(video, video.qualities["2160p"])

        self.assertEqual(selected.label, "1080p")


class PlaybackRequestTests(unittest.TestCase):
    def test_begin_playback_request_returns_current_request_id(self) -> None:
        state = SimpleNamespace(
            _playback_request_id=0,
            _pending_quality_reason="",
            _pending_quality_hint=None,
            _playback_request_context=None,
            _quality_hint=lambda: None,
        )

        request_id = MainWindow._begin_playback_request(state, "https://example.test/video", reason="direct")

        self.assertEqual(request_id, 1)
        self.assertEqual(state._playback_request_id, request_id)

    def test_xiaohongshu_video_is_eligible_for_creator_playlist(self) -> None:
        video = VideoInfo(
            video_id="xiaohongshu:note",
            title="视频",
            source_site="xiaohongshu",
            creator_id="user-id",
        )
        state = SimpleNamespace(
            _creator_playlist_generation=0,
            _creator_playlist_workers={},
            current_video=video,
            _shutting_down=False,
        )

        with patch("ui.main_window.QTimer.singleShot") as single_shot:
            MainWindow._schedule_creator_playlist(state, video)

        self.assertEqual(state._creator_playlist_generation, 1)
        single_shot.assert_called_once()

    def test_playback_invalidates_home_generation_while_home_is_loading(self) -> None:
        class Home:
            def is_loading(self):
                return True

            def set_loading(self, value):
                self.value = value

        service = type("Service", (), {"cancel_home_requests": lambda self: setattr(self, "cancelled", True)})()
        state = SimpleNamespace(
            home_page=Home(),
            _browse_generation=4,
            douyin_browser_service=service,
            xiaohongshu_browser_service=service,
            _dlna_device=None,
            _dlna_cast_pending=False,
            _flush_playback_resume=lambda: None,
            _resume_media_key="old",
            _remember_playback_return_widget=lambda: None,
            _clear_playlist_context=lambda: None,
            _arm_playback_window_mode=lambda: None,
        )

        # 只验证播放入口的首页中断逻辑，后续解析流程不属于本用例。
        state._begin_playback_request = lambda *_args, **_kwargs: 1
        state._start_worker = lambda *_args, **_kwargs: None
        with patch.object(MainWindow, "_begin_playback_request", return_value=1):
            state.current_local_media_path = ""
            state._active_queue = ""
            state.player_page = SimpleNamespace(set_loading=lambda *_args: None)
            state.stack = SimpleNamespace(setCurrentWidget=lambda *_args: None)
            state.resolver = SimpleNamespace(detect_url_kind=lambda _url: "video")
            state.thread_pool = None
            state._playback_request_id = 1
            state.url_edit = None
            MainWindow.play_url(state, "https://example.test/video")

        self.assertEqual(state._browse_generation, 5)
        self.assertFalse(getattr(state.home_page, "value", True))
        self.assertTrue(service.cancelled)

class LazyPageTests(unittest.TestCase):
    """P4：启动只构建首页与播放页，其余页面首访时才建。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        # 构造函数会安排首页加载、Cookie 探测和运行时安装提示；硬化测试
        # 不需要这些真实后台任务，尤其不能让浏览器数据库探测线程越过用例边界。
        for method_name in ("load_home", "_start_cookie_probe", "_maybe_prompt_ffmpeg_install"):
            patcher = patch.object(MainWindow, method_name, lambda _self: None)
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

    def test_home_page_number_is_not_committed_before_load_succeeds(self) -> None:
        self.window._browse_source = "douyin"
        self.window._home_page = 1
        self.window._home_cache = [SimpleNamespace(video_id="old")]
        self.window.home_page.set_home_context(1, True, source_label="抖音")
        workers = []

        with (
            patch.object(self.window, "_take_home_state", return_value=None),
            patch.object(self.window, "_start_worker", workers.append),
        ):
            self.window._start_home_load(2)

        self.assertEqual(self.window._home_page, 1)
        self.assertEqual(self.window.home_page.page(), 1)
        self.assertIn("初始化浏览器签名", self.window.home_page.status_label.text())
        self.assertIn("聚合 20 条可播放视频", self.window.home_page.status_label.text())
        workers[0].signals.error.emit("页面尚未准备好")
        QApplication.processEvents()
        self.assertEqual(self.window._home_page, 1)
        self.assertEqual(self.window.home_page.page(), 1)

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows HWND regression")
    def test_picture_in_picture_round_trip_preserves_native_window_ids(self) -> None:
        self.window.show()
        self.window.player_page.set_playback_available(True)
        QApplication.processEvents()
        main_id = int(self.window.winId())
        video_id = int(self.window.player_page.video_widget.winId())

        with (
            patch.object(self.window, "_read_windows_window_styles", return_value=(0, 0)),
            patch.object(self.window, "_apply_windows_window_styles") as apply_styles,
            patch.object(self.window, "setWindowFlags") as set_window_flags,
            patch.object(self.window.mpv, "video_aspect_ratio", return_value=16 / 9),
        ):
            self.window._enter_picture_in_picture()
            self.window._leave_picture_in_picture()
            QApplication.processEvents()

        self.assertGreaterEqual(apply_styles.call_count, 2)
        set_window_flags.assert_not_called()
        self.assertTrue(self.window.top_bar_widget.isVisible())
        restored_style = apply_styles.call_args_list[-1].args[0]
        self.assertTrue(restored_style & 0x00C00000)  # WS_CAPTION
        self.assertTrue(restored_style & 0x00080000)  # WS_SYSMENU
        self.assertEqual(int(self.window.winId()), main_id)
        self.assertEqual(int(self.window.player_page.video_widget.winId()), video_id)

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows fullscreen/PIP regression")
    def test_stop_after_fullscreen_picture_in_picture_restores_main_toolbar(self) -> None:
        self.window.show()
        self.window.player_page.set_playback_available(True)
        self.window._playback_return_widget = self.window.home_page
        normal_geometry = QRect(140, 110, 960, 640)
        self.window.setGeometry(normal_geometry)
        QApplication.processEvents()

        with (
            patch.object(self.window, "_read_windows_window_styles", return_value=(0x00CF0000, 0)),
            patch.object(self.window, "_apply_windows_window_styles", return_value=True),
            patch.object(self.window.mpv, "video_aspect_ratio", return_value=16 / 9),
            patch.object(self.window.mpv, "stop"),
        ):
            self.window._enter_player_fullscreen()
            self.window._enter_picture_in_picture()
            self.window.setGeometry(QRect(1570, 860, 320, 180))
            self.window._stop_playback()
            QApplication.processEvents()

        self.assertFalse(self.window.isFullScreen())
        self.assertIs(self.window.stack.currentWidget(), self.window.home_page)
        self.assertTrue(self.window.top_bar_widget.isVisible())
        self.assertEqual(self.window.geometry(), normal_geometry)


if __name__ == "__main__":
    unittest.main()
