"""E2 验证：下载列表最新任务置顶，且行号与按钮严格对应。

最容易出错的是 insertRow(0) 之后没同步 _rows 里已有行的行号 —— 表现为
「点了 A 的按钮却操作了 B」。本模块把行号维护固定成基线。
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from download.models import STATUS_COMPLETED, STATUS_DOWNLOADING, DownloadTask  # noqa: E402
from ui.download_page import DownloadPage  # noqa: E402


def make_task(task_id: str, *, created: datetime, title: str = "") -> DownloadTask:
    return DownloadTask(
        url=f"https://example.com/{task_id}",
        title=title or task_id,
        video_id=task_id,
        task_id=task_id,
        created_at=created,
    )


class DownloadOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.page = DownloadPage()
        self.addCleanup(self.page.deleteLater)
        self.base = datetime(2026, 7, 31, 10, 0, 0)

    def _titles_top_to_bottom(self) -> list[str]:
        return [self.page.table.item(row, 0).text() for row in range(self.page.table.rowCount())]

    def _assert_row_mapping_consistent(self) -> None:
        for task_id, row in self.page._rows.items():
            self.assertEqual(self.page.table.item(row, 0).text(), self.page._tasks[task_id].title)

    def test_newest_added_task_is_on_top(self) -> None:
        for index in range(3):
            self.page.add_task(make_task(f"t{index}", created=self.base + timedelta(minutes=index)))

        self.assertEqual(self._titles_top_to_bottom(), ["t2", "t1", "t0"])
        self._assert_row_mapping_consistent()

    def test_row_mapping_survives_middle_removal(self) -> None:
        for index in range(3):
            self.page.add_task(make_task(f"t{index}", created=self.base + timedelta(minutes=index)))

        self.page.remove_task("t1")

        self.assertEqual(self._titles_top_to_bottom(), ["t2", "t0"])
        self._assert_row_mapping_consistent()

    def test_update_task_does_not_reorder(self) -> None:
        for index in range(3):
            self.page.add_task(make_task(f"t{index}", created=self.base + timedelta(minutes=index)))

        changed = make_task("t0", created=self.base)
        changed.status = STATUS_DOWNLOADING
        changed.progress = 50.0
        self.page.update_task(changed)

        self.assertEqual(self._titles_top_to_bottom(), ["t2", "t1", "t0"])

    def test_button_click_targets_its_own_row(self) -> None:
        for index in range(3):
            task = make_task(f"t{index}", created=self.base + timedelta(minutes=index))
            task.status = STATUS_DOWNLOADING
            self.page.add_task(task)

        captured: list[str] = []
        self.page.pause_requested.connect(captured.append)

        # 第 2 行（从 0 计）应当是最旧的 t0。
        actions = self.page.table.cellWidget(2, 8)
        pause_button = actions.findChildren(QPushButton)[0]
        pause_button.click()

        self.assertEqual(captured, ["t0"])


if __name__ == "__main__":
    unittest.main()
