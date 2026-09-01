from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from download.models import (
    STATUS_COMPLETED,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_PAUSED,
    STATUS_QUEUED,
    DownloadTask,
)
from resolver.source_utils import source_site_label


STATUS_TEXT = {
    STATUS_QUEUED: "\u7b49\u5f85\u4e2d",
    STATUS_DOWNLOADING: "\u4e0b\u8f7d\u4e2d",
    STATUS_PAUSED: "\u6682\u505c",
    STATUS_COMPLETED: "\u5df2\u5b8c\u6210",
    STATUS_FAILED: "\u5931\u8d25",
}


class DownloadPage(QWidget):
    pause_requested = Signal(str)
    start_requested = Signal(str)
    delete_requested = Signal(str)
    play_file_requested = Signal(str)
    pause_tasks_requested = Signal(list)
    start_tasks_requested = Signal(list)
    delete_tasks_requested = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self._rows: dict[str, int] = {}
        self._tasks: dict[str, DownloadTask] = {}
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索标题、来源或视频 ID")
        self.search_edit.setClearButtonEnabled(True)

        title = QLabel("\u4e0b\u8f7d\u5217\u8868")
        title.setObjectName("PageTitle")

        self.table = QTableWidget(0, 9)
        self.table.setObjectName("LibraryTable")
        self.table.setHorizontalHeaderLabels(
            [
                "\u6807\u9898",
                "来源",
                "\u6e05\u6670\u5ea6",
                "\u72b6\u6001",
                "\u8fdb\u5ea6",
                "\u901f\u5ea6",
                "\u5269\u4f59\u65f6\u95f4",
                "\u4fdd\u5b58\u8def\u5f84",
                "\u64cd\u4f5c",
            ]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self._double_clicked)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(4, 150)
        self.table.setColumnWidth(8, 280)
        self.table.verticalHeader().setDefaultSectionSize(40)

        search_row = QHBoxLayout()
        search_row.addWidget(self.search_edit, 1)

        self.pause_selected_button = QPushButton("暂停选中")
        self.pause_all_button = QPushButton("暂停所有")
        self.start_selected_button = QPushButton("启动选中")
        self.start_all_button = QPushButton("启动所有")
        self.delete_selected_button = QPushButton("删除选中")
        self.delete_all_button = QPushButton("删除所有")
        self._batch_buttons = (
            self.pause_selected_button,
            self.pause_all_button,
            self.start_selected_button,
            self.start_all_button,
            self.delete_selected_button,
            self.delete_all_button,
        )
        batch_row = QHBoxLayout()
        batch_row.setContentsMargins(0, 0, 0, 0)
        batch_row.setSpacing(8)
        for button in self._batch_buttons:
            button.setFixedHeight(28)
            button.setObjectName("LibraryActionButton")
            batch_row.addWidget(button)
        batch_row.addStretch(1)

        self.pause_selected_button.clicked.connect(lambda: self._emit_batch("pause", selected_only=True))
        self.pause_all_button.clicked.connect(lambda: self._emit_batch("pause", selected_only=False))
        self.start_selected_button.clicked.connect(lambda: self._emit_batch("start", selected_only=True))
        self.start_all_button.clicked.connect(lambda: self._emit_batch("start", selected_only=False))
        self.delete_selected_button.clicked.connect(lambda: self._emit_batch("delete", selected_only=True))
        self.delete_all_button.clicked.connect(lambda: self._emit_batch("delete", selected_only=False))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addLayout(search_row)
        layout.addLayout(batch_row)
        layout.addWidget(self.table, 1)

        self.search_edit.textChanged.connect(self._apply_filter)
        self.table.itemSelectionChanged.connect(self._update_batch_buttons)
        self._update_batch_buttons()

    def add_task(self, task: DownloadTask) -> None:
        if task.task_id in self._rows:
            self.update_task(task)
            return
        # 最新的下载放最前面。insertRow(0) 会把已有行（含 cellWidget）整体下移，
        # 因此 _rows 里所有行号都要 +1 —— 漏了这一步会表现为「点了 A 的按钮却操作了 B」。
        row = 0
        self.table.insertRow(row)
        for existing_id in self._rows:
            self._rows[existing_id] += 1
        self.table.setRowHeight(row, 40)
        self._rows[task.task_id] = row
        self._tasks[task.task_id] = task

        progress = QProgressBar()
        progress.setRange(0, 100)
        progress.setTextVisible(True)
        self.table.setCellWidget(row, 4, progress)
        progress.setObjectName("LibraryProgress")

        actions = QWidget()
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(4)
        pause_button = QPushButton("\u6682\u505c")
        start_button = QPushButton("\u542f\u52a8")
        delete_button = QPushButton("\u5220\u9664")
        play_button = QPushButton("\u64ad\u653e")
        for button in (pause_button, start_button, delete_button, play_button):
            button.setMinimumWidth(56)
            button.setFixedHeight(28)
            button.setObjectName("LibraryActionButton")
        pause_button.clicked.connect(lambda _=False, task_id=task.task_id: self.pause_requested.emit(task_id))
        start_button.clicked.connect(lambda _=False, task_id=task.task_id: self.start_requested.emit(task_id))
        delete_button.clicked.connect(lambda _=False, task_id=task.task_id: self.delete_requested.emit(task_id))
        play_button.clicked.connect(lambda _=False, task_id=task.task_id: self._play_task(task_id))
        action_layout.addWidget(pause_button)
        action_layout.addWidget(start_button)
        action_layout.addWidget(delete_button)
        action_layout.addWidget(play_button)
        self.table.setCellWidget(row, 8, actions)

        self.update_task(task)

    def update_task(self, task: DownloadTask) -> None:
        if task.task_id not in self._rows:
            self.add_task(task)
            return
        row = self._rows[task.task_id]
        self._tasks[task.task_id] = task
        display_path = self._local_file_path(task) if task.status == STATUS_COMPLETED else ""
        values = [
            task.title,
            source_site_label(task.source_site, task.url),
            task.quality_label,
            STATUS_TEXT.get(task.status, task.status),
            "",
            task.speed_text,
            task.eta_text,
            display_path or task.output_path or task.save_dir,
        ]
        for col, value in enumerate(values):
            if col == 4:
                continue
            item = self.table.item(row, col)
            if item is None:
                item = QTableWidgetItem()
                self.table.setItem(row, col, item)
            item.setText(str(value or ""))
            if col in (1, 2, 3, 5, 6):
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        progress = self.table.cellWidget(row, 4)
        if isinstance(progress, QProgressBar):
            progress.setValue(int(max(0, min(100, task.progress))))

        actions = self.table.cellWidget(row, 8)
        if actions:
            buttons = actions.findChildren(QPushButton)
            if len(buttons) >= 4:
                pause_button, start_button, delete_button, play_button = buttons[:4]
                pause_button.setEnabled(task.status in (STATUS_QUEUED, STATUS_DOWNLOADING))
                start_button.setEnabled(task.status in (STATUS_PAUSED, STATUS_FAILED))
                delete_button.setEnabled(True)
                play_button.setEnabled(task.status == STATUS_COMPLETED and bool(self._local_file_path(task)))
        self._apply_filter()

    def remove_task(self, task_id: str) -> None:
        row = self._rows.pop(task_id, None)
        self._tasks.pop(task_id, None)
        if row is None:
            return
        self.table.removeRow(row)
        for item_id, item_row in list(self._rows.items()):
            if item_row > row:
                self._rows[item_id] = item_row - 1
        self._update_batch_buttons()

    def _apply_filter(self, _text: str = "") -> None:
        query = self.search_edit.text().strip().casefold()
        for task_id, row in self._rows.items():
            task = self._tasks.get(task_id)
            if task is None:
                self.table.setRowHidden(row, True)
                continue
            haystack = " ".join(
                (
                    task.title,
                    source_site_label(task.source_site, task.url),
                    task.url,
                    task.video_id,
                )
            ).casefold()
            self.table.setRowHidden(row, bool(query and query not in haystack))
        self._update_batch_buttons()

    # ------------------------------------------------------------------
    # 批量操作
    # ------------------------------------------------------------------

    PAUSABLE = (STATUS_QUEUED, STATUS_DOWNLOADING)
    STARTABLE = (STATUS_PAUSED, STATUS_FAILED)

    def _visible_task_ids(self) -> list[str]:
        """当前筛选后可见的任务，按表格从上到下的顺序。

        「所有」按这个口径而不是整张表：用户搜索之后点「删除所有」，
        期望删的几乎一定是眼前这些。
        """
        ordered = sorted(self._rows.items(), key=lambda item: item[1])
        return [task_id for task_id, row in ordered if not self.table.isRowHidden(row)]

    def _selected_task_ids(self) -> list[str]:
        rows = {index.row() for index in self.table.selectionModel().selectedRows()}
        ordered = sorted(self._rows.items(), key=lambda item: item[1])
        return [task_id for task_id, row in ordered if row in rows and not self.table.isRowHidden(row)]

    def _candidates(self, action: str, task_ids: list[str]) -> list[str]:
        if action == "delete":
            return list(task_ids)
        allowed = self.PAUSABLE if action == "pause" else self.STARTABLE
        return [task_id for task_id in task_ids if (self._tasks.get(task_id) and self._tasks[task_id].status in allowed)]

    def _emit_batch(self, action: str, *, selected_only: bool) -> None:
        task_ids = self._selected_task_ids() if selected_only else self._visible_task_ids()
        if not task_ids:
            QMessageBox.information(self, "提示", "没有可操作的任务。")
            return
        candidates = self._candidates(action, task_ids)
        if not candidates:
            QMessageBox.information(self, "提示", "选中的任务当前都不支持该操作。")
            return
        if action == "delete" and not self._confirm_delete(len(candidates), selected_only=selected_only):
            return
        signal = {
            "pause": self.pause_tasks_requested,
            "start": self.start_tasks_requested,
            "delete": self.delete_tasks_requested,
        }[action]
        signal.emit(candidates)

    def _confirm_delete(self, count: int, *, selected_only: bool) -> bool:
        scope = "选中的" if selected_only else "列表中当前显示的"
        # 如实说明只删记录：DownloadManager.delete_task 不动本地文件。
        answer = QMessageBox.question(
            self,
            "确认删除",
            f"将删除{scope} {count} 个下载任务记录。\n\n已下载的本地文件不会被删除。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _update_batch_buttons(self) -> None:
        visible = self._visible_task_ids()
        selected = self._selected_task_ids()
        self.pause_selected_button.setEnabled(bool(self._candidates("pause", selected)))
        self.start_selected_button.setEnabled(bool(self._candidates("start", selected)))
        self.delete_selected_button.setEnabled(bool(selected))
        self.pause_all_button.setEnabled(bool(self._candidates("pause", visible)))
        self.start_all_button.setEnabled(bool(self._candidates("start", visible)))
        self.delete_all_button.setEnabled(bool(visible))
        self.pause_all_button.setToolTip(f"暂停当前列表中显示的 {len(visible)} 个任务")
        self.start_all_button.setToolTip(f"启动当前列表中显示的 {len(visible)} 个任务")
        self.delete_all_button.setToolTip(f"删除当前列表中显示的 {len(visible)} 个任务记录")

    def _double_clicked(self, row: int, _column: int) -> None:
        task_id = self._task_id_for_row(row)
        if task_id:
            self._play_task(task_id)

    def _play_task(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if not task or task.status != STATUS_COMPLETED:
            return
        path = self._local_file_path(task)
        if path:
            self.play_file_requested.emit(path)
        else:
            self._show_missing_file_warning()

    @staticmethod
    def _local_file_path(task: DownloadTask) -> str:
        if task.output_path and Path(task.output_path).exists():
            return task.output_path
        if not task.video_id or not task.save_dir:
            return ""
        save_dir = Path(task.save_dir)
        if not save_dir.exists():
            return ""
        markers = [f" [{candidate}]" for candidate in _video_id_candidates(task.video_id)]
        raw_id = str(task.video_id or "").split(":", 1)[-1]
        if raw_id:
            markers.append(f" [{raw_id}]")
        transient_suffixes = (".part", ".ytdl", ".tmp", ".temp")
        try:
            for path in save_dir.iterdir():
                if not path.is_file():
                    continue
                if path.name.lower().endswith(transient_suffixes):
                    continue
                if not any(marker in path.name for marker in markers):
                    continue
                task.output_path = str(path)
                return task.output_path
        except OSError:
            return ""
        return ""

    def _show_missing_file_warning(self) -> None:
        QMessageBox.warning(
            self,
            "\u6587\u4ef6\u4e0d\u5b58\u5728",
            "\u4e0b\u8f7d\u8bb0\u5f55\u5bf9\u5e94\u7684\u672c\u5730\u6587\u4ef6\u4e0d\u5b58\u5728\u3002",
        )

    def _task_id_for_row(self, row: int) -> str:
        for task_id, task_row in self._rows.items():
            if task_row == row:
                return task_id
        return ""


def _video_id_candidates(video_id: str) -> list[str]:
    raw = str(video_id or "").strip()
    if not raw:
        return []

    candidates: list[str] = [raw]
    if raw.startswith(("bilibili:", "douyin:", "tiktok:", "xiaohongshu:")):
        raw = raw.split(":", 1)[1]
        if raw not in candidates:
            candidates.append(raw)
    if raw.startswith("BV") or raw.startswith("av"):
        trimmed = raw.split(":p", 1)[0]
        if trimmed and trimmed not in candidates:
            candidates.append(trimmed)
    return candidates
