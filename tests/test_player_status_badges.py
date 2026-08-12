from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from download.models import (
    STATUS_COMPLETED,
    STATUS_DELETED,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_PAUSED,
    STATUS_QUEUED,
    DownloadTask,
)
from ui.main_window import MainWindow
from ui.player_page import PlayerPage


class PlayerStatusBadgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.page = PlayerPage.__new__(PlayerPage)
        PlayerPage.__init__(self.page)

    def tearDown(self) -> None:
        self.page.close()

    def test_no_state_hides_the_badge(self) -> None:
        self.page.set_favorite_state(False, available=True)
        self.page.set_download_state("")

        self.assertEqual(self.page.status_label.text(), "")
        self.assertFalse(self.page.status_label.isVisible())

    def test_favorite_only(self) -> None:
        self.page.set_favorite_state(True, available=True)
        self.page.set_download_state("")

        self.assertEqual(self.page.status_label.text(), "已收藏")

    def test_downloaded_only(self) -> None:
        self.page.set_favorite_state(False, available=True)
        self.page.set_download_state(STATUS_COMPLETED)

        self.assertEqual(self.page.status_label.text(), "已下载")

    def test_favorite_and_downloaded_are_combined(self) -> None:
        self.page.set_favorite_state(True, available=True)
        self.page.set_download_state(STATUS_COMPLETED)

        self.assertEqual(self.page.status_label.text(), "已收藏 · 已下载")

    def test_each_download_status_has_its_own_text(self) -> None:
        self.page.set_favorite_state(False, available=True)
        cases = {
            STATUS_QUEUED: "已加入下载队列",
            STATUS_PAUSED: "下载已暂停",
            STATUS_FAILED: "下载失败",
            STATUS_COMPLETED: "已下载",
        }
        for status, expected in cases.items():
            with self.subTest(status=status):
                self.page.set_download_state(status)
                self.assertEqual(self.page.status_label.text(), expected)

    def test_downloading_shows_progress(self) -> None:
        self.page.set_favorite_state(False, available=True)
        self.page.set_download_state(STATUS_DOWNLOADING, 42.4)

        self.assertEqual(self.page.status_label.text(), "下载中 42%")

    def test_downloading_without_progress_omits_percent(self) -> None:
        self.page.set_download_state(STATUS_DOWNLOADING, 0.0)

        self.assertEqual(self.page.status_label.text(), "下载中")

    def test_unknown_status_is_ignored(self) -> None:
        self.page.set_favorite_state(True, available=True)
        self.page.set_download_state(STATUS_DELETED)

        self.assertEqual(self.page.status_label.text(), "已收藏")

    def test_loading_clears_stale_badge(self) -> None:
        self.page.set_favorite_state(True, available=True)
        self.page.set_download_state(STATUS_COMPLETED)

        self.page.set_loading(True)

        self.assertEqual(self.page.status_label.text(), "")
        self.assertFalse(self.page.status_label.isVisible())


class DownloadTaskLookupTests(unittest.TestCase):
    @staticmethod
    def _manager(tasks: list[DownloadTask]) -> SimpleNamespace:
        from download.download_manager import DownloadManager

        manager = SimpleNamespace(
            _tasks=tasks,
            _url_index={t.url: t for t in tasks if t.url and t.status != STATUS_DELETED},
        )
        manager.task_for_video = DownloadManager.task_for_video.__get__(manager, SimpleNamespace)
        return manager

    def test_lookup_by_url(self) -> None:
        task = DownloadTask(url="https://example.com/a", title="A", video_id="vid-a")
        manager = self._manager([task])

        self.assertIs(manager.task_for_video(url="https://example.com/a"), task)

    def test_lookup_falls_back_to_video_id(self) -> None:
        task = DownloadTask(url="https://example.com/a", title="A", video_id="vid-a")
        manager = self._manager([task])

        # 同一个视频从不同入口进来，URL 带了多余参数，仍要认出来。
        found = manager.task_for_video(url="https://example.com/a?t=30", video_id="vid-a")

        self.assertIs(found, task)

    def test_deleted_tasks_are_not_returned(self) -> None:
        task = DownloadTask(url="https://example.com/a", title="A", video_id="vid-a", status=STATUS_DELETED)
        manager = self._manager([task])

        self.assertIsNone(manager.task_for_video(url="https://example.com/a", video_id="vid-a"))

    def test_unknown_video_returns_none(self) -> None:
        manager = self._manager([])

        self.assertIsNone(manager.task_for_video(url="https://example.com/x", video_id="vid-x"))


class DownloadStateSyncTests(unittest.TestCase):
    @staticmethod
    def _state(video, tasks: list[DownloadTask]) -> SimpleNamespace:
        applied: list[tuple[str, float]] = []
        return SimpleNamespace(
            current_video=video,
            player_page=SimpleNamespace(
                set_download_state=lambda status="", progress=0.0: applied.append((status, progress))
            ),
            download_manager=SimpleNamespace(
                task_for_video=lambda url="", video_id="": next(
                    (t for t in tasks if t.url == url or (video_id and t.video_id == video_id)), None
                )
            ),
            _applied=applied,
        )

    def test_sync_without_video_clears_state(self) -> None:
        state = self._state(None, [])

        MainWindow._sync_current_download_state(state)

        self.assertEqual(state._applied, [("", 0.0)])

    def test_sync_reports_matching_task(self) -> None:
        video = SimpleNamespace(webpage_url="https://example.com/a", video_id="vid-a")
        task = DownloadTask(
            url="https://example.com/a", title="A", video_id="vid-a", status=STATUS_DOWNLOADING, progress=12.0
        )
        state = self._state(video, [task])

        MainWindow._sync_current_download_state(state)

        self.assertEqual(state._applied, [(STATUS_DOWNLOADING, 12.0)])

    def test_task_change_for_other_video_is_ignored(self) -> None:
        video = SimpleNamespace(webpage_url="https://example.com/a", video_id="vid-a")
        state = self._state(video, [])
        other = DownloadTask(url="https://example.com/b", title="B", video_id="vid-b", status=STATUS_COMPLETED)

        MainWindow._sync_download_state_from_task(state, other)

        self.assertEqual(state._applied, [])

    def test_task_change_for_current_video_updates_badge(self) -> None:
        video = SimpleNamespace(webpage_url="https://example.com/a", video_id="vid-a")
        state = self._state(video, [])
        task = DownloadTask(
            url="https://example.com/a", title="A", video_id="vid-a", status=STATUS_COMPLETED, progress=100.0
        )

        MainWindow._sync_download_state_from_task(state, task)

        self.assertEqual(state._applied, [(STATUS_COMPLETED, 100.0)])


if __name__ == "__main__":
    unittest.main()
