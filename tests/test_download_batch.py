"""E5 验证：下载列表批量暂停/启动/删除。"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from download.models import (  # noqa: E402
    STATUS_COMPLETED,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_PAUSED,
    STATUS_QUEUED,
    DownloadTask,
)
from ui.download_page import DownloadPage  # noqa: E402


def make_task(task_id: str, status: str, *, created: datetime, title: str = "") -> DownloadTask:
    task = DownloadTask(
        url=f"https://example.com/{task_id}",
        title=title or task_id,
        video_id=task_id,
        task_id=task_id,
        created_at=created,
    )
    task.status = status
    return task


class BatchSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.page = DownloadPage()
        self.addCleanup(self.page.deleteLater)
        base = datetime(2026, 7, 31, 10, 0, 0)
        self.statuses = {
            "a": STATUS_DOWNLOADING,
            "b": STATUS_PAUSED,
            "c": STATUS_COMPLETED,
            "d": STATUS_FAILED,
            "e": STATUS_QUEUED,
        }
        for index, (task_id, status) in enumerate(self.statuses.items()):
            self.page.add_task(make_task(task_id, status, created=base + timedelta(minutes=index)))

    def _select(self, task_ids: list[str]) -> None:
        # selectRow 在 ExtendedSelection 下会替换选区，多选要走 selectionModel。
        model = self.page.table.selectionModel()
        model.clearSelection()
        for task_id in task_ids:
            row = self.page._rows[task_id]
            model.select(
                self.page.table.model().index(row, 0),
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
            )

    def test_pause_filters_to_pausable_statuses(self) -> None:
        captured: list[list[str]] = []
        self.page.pause_tasks_requested.connect(captured.append)

        self.page._emit_batch("pause", selected_only=False)

        self.assertEqual(sorted(captured[0]), ["a", "e"])

    def test_start_filters_to_startable_statuses(self) -> None:
        captured: list[list[str]] = []
        self.page.start_tasks_requested.connect(captured.append)

        self.page._emit_batch("start", selected_only=False)

        self.assertEqual(sorted(captured[0]), ["b", "d"])

    def test_all_scope_honours_the_search_filter(self) -> None:
        # 只让 a 命中：其余任务的标题里没有这个词（URL 里的 example.com 会误伤单字母查询）。
        self.page.update_task(make_task("a", STATUS_DOWNLOADING, created=datetime(2026, 7, 31, 10, 0), title="独一无二"))
        self.page.search_edit.setText("独一无二")
        captured: list[list[str]] = []
        self.page.delete_tasks_requested.connect(captured.append)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.page._emit_batch("delete", selected_only=False)

        self.assertEqual(captured[0], ["a"])

    def test_selected_scope_uses_selection(self) -> None:
        self._select(["b", "d"])
        captured: list[list[str]] = []
        self.page.start_tasks_requested.connect(captured.append)

        self.page._emit_batch("start", selected_only=True)

        self.assertEqual(sorted(captured[0]), ["b", "d"])

    def test_delete_is_cancellable(self) -> None:
        captured: list[list[str]] = []
        self.page.delete_tasks_requested.connect(captured.append)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.page._emit_batch("delete", selected_only=False)

        self.assertEqual(captured, [])

    def test_buttons_disabled_without_selection(self) -> None:
        self.page.table.clearSelection()
        self.page._update_batch_buttons()

        self.assertFalse(self.page.pause_selected_button.isEnabled())
        self.assertFalse(self.page.start_selected_button.isEnabled())
        self.assertFalse(self.page.delete_selected_button.isEnabled())
        self.assertTrue(self.page.delete_all_button.isEnabled())

    def test_no_candidate_shows_notice_and_emits_nothing(self) -> None:
        self._select(["c"])  # 已完成：既不能暂停也不能启动
        captured: list[list[str]] = []
        self.page.pause_tasks_requested.connect(captured.append)

        with patch.object(QMessageBox, "information") as notice:
            self.page._emit_batch("pause", selected_only=True)

        self.assertEqual(captured, [])
        notice.assert_called_once()


if __name__ == "__main__":
    unittest.main()
