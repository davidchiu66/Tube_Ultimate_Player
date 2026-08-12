from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox, QTableWidget

from database.favorite_repository import FavoriteRepository
from database.sqlite_manager import SQLiteManager
from resolver.models import VideoInfo
from ui.favorite_page import FavoritePage
from ui.main_window import MainWindow


def _video(index: int, *, uploader: str = "作者A") -> VideoInfo:
    return VideoInfo(
        video_id=f"vid-{index}",
        title=f"视频 {index}",
        source_site="youtube",
        uploader=uploader,
        duration=60 + index,
        webpage_url=f"https://www.youtube.com/watch?v=vid-{index}",
    )


class FavoriteRepositoryBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = SQLiteManager(Path(self.temp_dir.name) / "test.sqlite3")
        self.favorites = FavoriteRepository(self.db)
        for index in range(3):
            self.favorites.add_video_info(_video(index))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_remove_many_deletes_only_the_given_ids(self) -> None:
        removed = self.favorites.remove_many(["vid-0", "vid-2"])

        self.assertEqual(removed, 2)
        self.assertEqual(self.favorites.favorite_ids(), {"vid-1"})

    def test_remove_many_with_empty_list_is_a_noop(self) -> None:
        self.assertEqual(self.favorites.remove_many([]), 0)
        self.assertEqual(len(self.favorites.favorite_ids()), 3)

    def test_remove_many_ignores_blank_and_unknown_ids(self) -> None:
        removed = self.favorites.remove_many(["", "  ", "vid-1", "not-there"])

        self.assertEqual(removed, 1)
        self.assertEqual(self.favorites.favorite_ids(), {"vid-0", "vid-2"})


class FavoritePageBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = SQLiteManager(Path(self.temp_dir.name) / "test.sqlite3")
        self.favorites = FavoriteRepository(self.db)
        self.favorites.add_video_info(_video(0, uploader="作者A"))
        self.favorites.add_video_info(_video(1, uploader="作者B"))
        self.favorites.add_video_info(_video(2, uploader="作者B"))
        self.page = FavoritePage(self.favorites)
        self.downloads: list[list[dict]] = []
        self.removals: list[list[str]] = []
        self.page.download_videos_requested.connect(self.downloads.append)
        self.page.remove_videos_requested.connect(self.removals.append)

    def tearDown(self) -> None:
        self.page.close()
        self.temp_dir.cleanup()

    def _row_for_video(self, video_id: str) -> int:
        for row, data in enumerate(self.page._rows):
            if data.get("video_id") == video_id:
                return row
        raise AssertionError(f"没有找到 {video_id} 对应的行")

    def test_multi_selection_is_enabled(self) -> None:
        self.assertEqual(
            self.page.list_widget.selectionMode(),
            QTableWidget.SelectionMode.ExtendedSelection,
        )

    def test_download_selected_emits_only_selected_records(self) -> None:
        self.page.list_widget.selectRow(self._row_for_video("vid-1"))

        self.page._emit_batch("download", selected_only=True)

        self.assertEqual([r["video_id"] for r in self.downloads[0]], ["vid-1"])

    def test_download_all_covers_every_visible_row(self) -> None:
        self.page._emit_batch("download", selected_only=False)

        self.assertEqual({r["video_id"] for r in self.downloads[0]}, {"vid-0", "vid-1", "vid-2"})

    def test_all_is_scoped_to_the_search_filter(self) -> None:
        # 「全部」= 搜索筛选后可见行，与下载页口径一致。
        self.page.search_edit.setText("作者B")

        self.page._emit_batch("download", selected_only=False)

        self.assertEqual({r["video_id"] for r in self.downloads[0]}, {"vid-1", "vid-2"})

    def test_selection_hidden_by_filter_is_excluded(self) -> None:
        self.page.list_widget.selectRow(self._row_for_video("vid-0"))
        self.page.search_edit.setText("作者B")

        with patch.object(QMessageBox, "information") as notice:
            self.page._emit_batch("download", selected_only=True)

        # 选中行被筛掉之后就不该再被批量操作带上。
        self.assertEqual(self.downloads, [])
        notice.assert_called_once()

    def test_delete_asks_for_confirmation_before_emitting(self) -> None:
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.page._emit_batch("delete", selected_only=False)

        self.assertEqual(self.removals, [])

    def test_delete_all_emits_visible_video_ids(self) -> None:
        self.page.search_edit.setText("作者B")

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.page._emit_batch("delete", selected_only=False)

        self.assertEqual(set(self.removals[0]), {"vid-1", "vid-2"})

    def test_buttons_track_selection_and_visibility(self) -> None:
        self.page.list_widget.clearSelection()
        self.page._update_batch_buttons()

        self.assertFalse(self.page.download_selected_button.isEnabled())
        self.assertFalse(self.page.delete_selected_button.isEnabled())
        self.assertTrue(self.page.download_all_button.isEnabled())
        self.assertTrue(self.page.delete_all_button.isEnabled())

        self.page.list_widget.selectRow(self._row_for_video("vid-0"))

        self.assertTrue(self.page.download_selected_button.isEnabled())
        self.assertTrue(self.page.delete_selected_button.isEnabled())

    def test_all_buttons_disabled_when_filter_matches_nothing(self) -> None:
        self.page.search_edit.setText("不存在的关键词")

        self.assertFalse(self.page.download_all_button.isEnabled())
        self.assertFalse(self.page.delete_all_button.isEnabled())


class FavoriteBatchWiringTests(unittest.TestCase):
    @staticmethod
    def _state(enqueue_result=(0, 0), removed: int = 0):
        messages: list[str] = []
        calls: list[tuple] = []

        def enqueue_many(videos, quality_label):
            calls.append((list(videos), quality_label))
            if isinstance(enqueue_result, Exception):
                raise enqueue_result
            return enqueue_result

        return SimpleNamespace(
            current_video=None,
            toast=SimpleNamespace(show_message=messages.append),
            download_manager=SimpleNamespace(enqueue_many=enqueue_many),
            favorites=SimpleNamespace(remove_many=lambda ids: removed),
            player_page=SimpleNamespace(set_favorite_state=lambda *a, **k: None),
            _refresh_favorite_views=lambda: None,
            _messages=messages,
            _calls=calls,
        )

    def test_records_become_video_info_and_enqueue_as_auto(self) -> None:
        state = self._state(enqueue_result=(2, 0))
        records = [
            {"video_id": "vid-0", "title": "视频 0", "webpage_url": "https://example.com/0", "duration": 61},
            {"video_id": "vid-1", "title": "视频 1", "webpage_url": "https://example.com/1", "duration": 62},
        ]

        MainWindow._download_favorite_records(state, records)

        videos, quality = state._calls[0]
        self.assertEqual(quality, "Auto")
        self.assertEqual([v.video_id for v in videos], ["vid-0", "vid-1"])
        self.assertEqual([v.webpage_url for v in videos], ["https://example.com/0", "https://example.com/1"])
        self.assertEqual(state._messages, ["已加入下载队列 2 个"])

    def test_skipped_count_is_reported(self) -> None:
        state = self._state(enqueue_result=(1, 2))

        MainWindow._download_favorite_records(
            state, [{"video_id": "vid-0", "webpage_url": "https://example.com/0"}]
        )

        self.assertEqual(state._messages, ["已加入下载队列 1 个，跳过 2 个（已在队列或已完成）"])

    def test_records_without_url_are_dropped(self) -> None:
        state = self._state()

        MainWindow._download_favorite_records(state, [{"video_id": "vid-0", "webpage_url": ""}])

        self.assertEqual(state._calls, [])
        self.assertEqual(state._messages, ["下载失败：收藏记录里没有可用的视频地址"])

    def test_enqueue_failure_is_reported_not_raised(self) -> None:
        state = self._state(enqueue_result=RuntimeError("boom"))

        MainWindow._download_favorite_records(state, [{"video_id": "vid-0", "webpage_url": "https://example.com/0"}])

        self.assertEqual(state._messages, ["批量下载失败"])

    def test_batch_remove_reports_the_deleted_count(self) -> None:
        state = self._state(removed=3)

        MainWindow._remove_favorites(state, ["vid-0", "vid-1", "vid-2"])

        self.assertEqual(state._messages, ["已从收藏中移除 3 条"])

    def test_batch_remove_with_no_ids_is_a_noop(self) -> None:
        state = self._state(removed=0)

        MainWindow._remove_favorites(state, ["", "   "])

        self.assertEqual(state._messages, [])


if __name__ == "__main__":
    unittest.main()
