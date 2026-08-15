from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QTableWidget  # noqa: E402

from database.history_repository import HistoryRepository  # noqa: E402
from database.sqlite_manager import SQLiteManager  # noqa: E402
from resolver.models import VideoInfo  # noqa: E402
from ui.history_page import HISTORY_PAGE_LIMIT, HistoryPage  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def _video(index: int, *, uploader: str = "作者A") -> VideoInfo:
    return VideoInfo(
        video_id=f"vid-{index}",
        title=f"视频 {index}",
        source_site="youtube",
        uploader=uploader,
        duration=60 + index,
        webpage_url=f"https://www.youtube.com/watch?v=vid-{index}",
    )


class HistoryRepositoryBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = SQLiteManager(Path(self.temp_dir.name) / "test.sqlite3")
        self.history = HistoryRepository(self.db)
        for index in range(3):
            self.history.record_play(_video(index))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_remove_deletes_all_rows_for_the_video_id(self) -> None:
        with self.db.connection() as conn:
            conn.execute(
                """
                INSERT INTO history (video_id, title, source_site, last_played_at, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("vid-0", "旧记录", "youtube", "2026-08-13T12:00:00", "2026-08-13T12:00:00"),
            )

        removed = self.history.remove("vid-0")

        self.assertEqual(removed, 2)
        self.assertNotIn("vid-0", {row["video_id"] for row in self.history.recent(20)})

    def test_remove_many_deletes_only_requested_ids(self) -> None:
        removed = self.history.remove_many(["vid-0", "vid-2", "vid-2", "unknown"])

        self.assertEqual(removed, 2)
        self.assertEqual({row["video_id"] for row in self.history.recent(20)}, {"vid-1"})

    def test_blank_and_unknown_ids_are_noops(self) -> None:
        self.assertEqual(self.history.remove("  "), 0)
        self.assertEqual(self.history.remove("unknown"), 0)
        self.assertEqual(self.history.remove_many(["", "  "]), 0)
        self.assertEqual(len(self.history.recent(20)), 3)


class HistoryPageBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = SQLiteManager(Path(self.temp_dir.name) / "test.sqlite3")
        self.history = HistoryRepository(self.db)
        self.history.record_play(_video(0, uploader="作者A"))
        self.history.record_play(_video(1, uploader="作者B"))
        self.history.record_play(_video(2, uploader="作者B"))
        self.page = HistoryPage(self.history)
        self.downloads: list[list[dict]] = []
        self.removals: list[list[str]] = []
        self.single_removals: list[str] = []
        self.page.download_videos_requested.connect(self.downloads.append)
        self.page.remove_videos_requested.connect(self.removals.append)
        self.page.remove_requested.connect(self.single_removals.append)

    def tearDown(self) -> None:
        self.page.close()
        self.temp_dir.cleanup()

    def _row_for_video(self, video_id: str) -> int:
        for row, data in enumerate(self.page._rows):
            if data.get("video_id") == video_id:
                return row
        raise AssertionError(f"没有找到 {video_id} 对应的行")

    def test_multi_selection_and_inline_delete_are_available(self) -> None:
        self.assertEqual(
            self.page.list_widget.selectionMode(),
            QTableWidget.SelectionMode.ExtendedSelection,
        )
        actions = self.page.list_widget.cellWidget(0, 6)
        labels = {button.text() for button in actions.findChildren(QPushButton)}
        self.assertEqual(labels, {"播放", "删除"})

    def test_download_selected_emits_only_selected_records(self) -> None:
        self.page.list_widget.selectRow(self._row_for_video("vid-1"))

        self.page._emit_batch("download", selected_only=True)

        self.assertEqual([record["video_id"] for record in self.downloads[0]], ["vid-1"])

    def test_all_is_scoped_to_the_search_filter(self) -> None:
        self.page.search_edit.setText("作者B")

        self.page._emit_batch("download", selected_only=False)

        self.assertEqual({record["video_id"] for record in self.downloads[0]}, {"vid-1", "vid-2"})
        self.assertIn("2", self.page.download_all_button.toolTip())
        self.assertIn("2", self.page.delete_all_button.toolTip())

    def test_selection_hidden_by_filter_is_excluded(self) -> None:
        self.page.list_widget.selectRow(self._row_for_video("vid-0"))
        self.page.search_edit.setText("作者B")

        with patch.object(QMessageBox, "information") as notice:
            self.page._emit_batch("download", selected_only=True)

        self.assertEqual(self.downloads, [])
        notice.assert_called_once()

    def test_delete_asks_for_confirmation_before_emitting(self) -> None:
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.page._emit_batch("delete", selected_only=False)

        self.assertEqual(self.removals, [])

    def test_delete_all_emits_only_visible_video_ids(self) -> None:
        self.page.search_edit.setText("作者B")

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.page._emit_batch("delete", selected_only=False)

        self.assertEqual(set(self.removals[0]), {"vid-1", "vid-2"})

    def test_buttons_track_selection_and_empty_filters(self) -> None:
        self.page.list_widget.clearSelection()
        self.page._update_batch_buttons()
        self.assertFalse(self.page.download_selected_button.isEnabled())
        self.assertFalse(self.page.delete_selected_button.isEnabled())
        self.assertTrue(self.page.download_all_button.isEnabled())
        self.assertTrue(self.page.delete_all_button.isEnabled())

        self.page.list_widget.selectRow(self._row_for_video("vid-0"))
        self.assertTrue(self.page.download_selected_button.isEnabled())
        self.assertTrue(self.page.delete_selected_button.isEnabled())

        self.page.search_edit.setText("不存在的关键词")
        self.assertFalse(self.page.download_selected_button.isEnabled())
        self.assertFalse(self.page.delete_selected_button.isEnabled())
        self.assertFalse(self.page.download_all_button.isEnabled())
        self.assertFalse(self.page.delete_all_button.isEnabled())

    def test_inline_delete_emits_the_row_video_id(self) -> None:
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.page._remove_row(self._row_for_video("vid-2"))

        self.assertEqual(self.single_removals, ["vid-2"])

    def test_inline_delete_can_be_cancelled(self) -> None:
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.page._remove_row(self._row_for_video("vid-2"))

        self.assertEqual(self.single_removals, [])

    def test_repository_normalizes_invalid_limits(self) -> None:
        self.assertEqual(self.history.recent(0), [])
        self.assertEqual(self.history.recent(-1), [])
        self.assertEqual(len(self.history.recent("invalid")), 3)

    def test_page_explicitly_requests_two_hundred_records(self) -> None:
        calls: list[int] = []
        fake_history = SimpleNamespace(recent=lambda limit: calls.append(limit) or [])
        page = HistoryPage(fake_history)
        self.addCleanup(page.close)

        self.assertEqual(HISTORY_PAGE_LIMIT, 200)
        self.assertEqual(calls, [200])


class HistoryBatchWiringTests(unittest.TestCase):
    @staticmethod
    def _state(
        *,
        enqueue_result=(0, 0),
        removed: int = 0,
        remove_error: Exception | None = None,
    ) -> SimpleNamespace:
        messages: list[str] = []
        calls: list[tuple] = []
        refreshed: list[bool] = []

        def enqueue_many(videos, quality_label):
            calls.append((list(videos), quality_label))
            if isinstance(enqueue_result, Exception):
                raise enqueue_result
            return enqueue_result

        def remove(_video_id):
            if remove_error is not None:
                raise remove_error
            return removed

        def remove_many(_video_ids):
            if remove_error is not None:
                raise remove_error
            return removed

        page = SimpleNamespace(refresh=lambda: refreshed.append(True))
        return SimpleNamespace(
            toast=SimpleNamespace(show_message=messages.append),
            download_manager=SimpleNamespace(enqueue_many=enqueue_many),
            history=SimpleNamespace(remove=remove, remove_many=remove_many),
            _created_page=lambda name: page if name == "history" else None,
            _messages=messages,
            _calls=calls,
            _refreshed=refreshed,
        )

    def test_records_enqueue_as_auto_and_report_skips(self) -> None:
        state = self._state(enqueue_result=(1, 2))

        MainWindow._download_history_records(
            state,
            [{"video_id": "vid-0", "webpage_url": "https://example.com/0", "duration": 61}],
        )

        videos, quality = state._calls[0]
        self.assertEqual(quality, "Auto")
        self.assertEqual([video.video_id for video in videos], ["vid-0"])
        self.assertEqual(state._messages, ["已加入下载队列 1 个，跳过 2 个（已在队列或已完成）"])

    def test_download_failure_and_missing_urls_are_reported(self) -> None:
        failed = self._state(enqueue_result=RuntimeError("boom"))
        MainWindow._download_history_records(
            failed,
            [{"video_id": "vid-0", "webpage_url": "https://example.com/0"}],
        )
        self.assertEqual(failed._messages, ["批量下载失败"])

        empty = self._state()
        MainWindow._download_history_records(empty, [{"video_id": "vid-0", "webpage_url": ""}])
        self.assertEqual(empty._messages, ["下载失败：播放历史里没有可用的视频地址"])

    def test_single_and_batch_remove_refresh_and_report_actual_count(self) -> None:
        single = self._state(removed=1)
        MainWindow._remove_history_record(single, "vid-0")
        self.assertEqual(single._messages, ["已从播放历史中移除 1 条"])
        self.assertEqual(single._refreshed, [True])

        batch = self._state(removed=3)
        MainWindow._remove_history_records(batch, ["vid-0", "vid-1", "vid-2"])
        self.assertEqual(batch._messages, ["已从播放历史中移除 3 条"])
        self.assertEqual(batch._refreshed, [True])

    def test_remove_failures_do_not_escape(self) -> None:
        single = self._state(remove_error=RuntimeError("boom"))
        MainWindow._remove_history_record(single, "vid-0")
        self.assertEqual(single._messages, ["删除播放历史失败"])
        self.assertEqual(single._refreshed, [])

        batch = self._state(remove_error=RuntimeError("boom"))
        MainWindow._remove_history_records(batch, ["vid-0"])
        self.assertEqual(batch._messages, ["批量删除播放历史失败"])
        self.assertEqual(batch._refreshed, [])


if __name__ == "__main__":
    unittest.main()
