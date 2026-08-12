from __future__ import annotations

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database.favorite_repository import FavoriteRepository
from resolver.source_utils import source_site_label
from ui.player_page import format_seconds


class FavoritePage(QWidget):
    play_requested = Signal(str)
    remove_requested = Signal(str)
    download_videos_requested = Signal(list)
    remove_videos_requested = Signal(list)

    def __init__(self, favorites: FavoriteRepository) -> None:
        super().__init__()
        self.favorites = favorites
        self._rows: list[dict] = []

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索标题、来源或作者")
        self.search_edit.setClearButtonEnabled(True)
        self.list_widget = QTableWidget(0, 6)
        self.list_widget.setObjectName("LibraryTable")
        self.list_widget.setHorizontalHeaderLabels(["标题", "来源", "作者", "时长", "收藏时间", "操作"])
        self.list_widget.verticalHeader().setVisible(False)
        self.list_widget.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.list_widget.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.list_widget.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.list_widget.setAlternatingRowColors(False)
        table_header = self.list_widget.horizontalHeader()
        table_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.list_widget.setColumnWidth(1, 90)
        self.list_widget.setColumnWidth(2, 130)
        self.list_widget.setColumnWidth(3, 90)
        self.list_widget.setColumnWidth(4, 170)
        self.list_widget.setColumnWidth(5, 150)
        self.list_widget.verticalHeader().setDefaultSectionSize(40)

        self.play_button = QPushButton("播放选中")
        self.remove_button = QPushButton("删除收藏")
        self.refresh_button = QPushButton("刷新")

        header = QHBoxLayout()
        title = QLabel("收藏视频")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.refresh_button)
        header.addWidget(self.play_button)
        header.addWidget(self.remove_button)

        self.download_selected_button = QPushButton("下载选中")
        self.download_all_button = QPushButton("下载全部")
        self.delete_selected_button = QPushButton("删除选中")
        self.delete_all_button = QPushButton("删除全部")
        self._batch_buttons = (
            self.download_selected_button,
            self.download_all_button,
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addLayout(header)
        layout.addWidget(self.search_edit)
        layout.addLayout(batch_row)
        layout.addWidget(self.list_widget, 1)

        self.search_edit.textChanged.connect(self._apply_filter)
        self.refresh_button.clicked.connect(self.refresh)
        self.play_button.clicked.connect(self._play_selected)
        self.remove_button.clicked.connect(self._remove_selected)
        self.download_selected_button.clicked.connect(lambda: self._emit_batch("download", selected_only=True))
        self.download_all_button.clicked.connect(lambda: self._emit_batch("download", selected_only=False))
        self.delete_selected_button.clicked.connect(lambda: self._emit_batch("delete", selected_only=True))
        self.delete_all_button.clicked.connect(lambda: self._emit_batch("delete", selected_only=False))
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._play_selected())
        self.list_widget.itemSelectionChanged.connect(self._update_batch_buttons)
        self.refresh()

    def refresh(self) -> None:
        self._rows = self.favorites.all()
        self.list_widget.setRowCount(0)
        for row_data in self._rows:
            row = self.list_widget.rowCount()
            self.list_widget.insertRow(row)
            self.list_widget.setRowHeight(row, 40)
            title = str(row_data.get("title") or "未命名视频")
            source = source_site_label(row_data.get("source_site", ""), row_data.get("webpage_url", ""))
            values = [
                title,
                source,
                str(row_data.get("uploader") or "未知作者"),
                format_seconds(row_data.get("duration") or 0),
                str(row_data.get("updated_at") or ""),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (1, 3, 4):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row_data.get("webpage_url") or "")
                    item.setData(Qt.ItemDataRole.UserRole + 1, row_data.get("video_id") or "")
                self.list_widget.setItem(row, column, item)

            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(4, 0, 4, 0)
            action_layout.setSpacing(4)
            play_button = QPushButton("播放")
            remove_button = QPushButton("删除")
            for button in (play_button, remove_button):
                button.setFixedHeight(28)
                button.setMinimumWidth(56)
                button.setObjectName("LibraryActionButton")
            play_button.clicked.connect(lambda _=False, index=row: self._play_row(index))
            remove_button.clicked.connect(lambda _=False, index=row: self._remove_row(index))
            action_layout.addWidget(play_button)
            action_layout.addWidget(remove_button)
            self.list_widget.setCellWidget(row, 5, actions)
        self._apply_filter()

    def _apply_filter(self, _text: str = "") -> None:
        query = self.search_edit.text().strip().casefold()
        for row in range(self.list_widget.rowCount()):
            data = self._rows[row] if row < len(self._rows) else {}
            haystack = " ".join(
                (
                    str(data.get("title") or ""),
                    source_site_label(data.get("source_site", ""), data.get("webpage_url", "")),
                    str(data.get("uploader") or ""),
                    str(data.get("webpage_url") or ""),
                )
            ).casefold()
            self.list_widget.setRowHidden(row, bool(query and query not in haystack))
        self._update_batch_buttons()

    # ------------------------------------------------------------------
    # 批量操作
    # ------------------------------------------------------------------

    def _visible_rows(self) -> list[int]:
        """当前筛选后可见的行，按表格从上到下的顺序。

        「全部」按这个口径而不是整张表：用户搜索之后点「删除全部」，
        期望删的几乎一定是眼前这些（与下载页一致）。
        """
        return [row for row in range(self.list_widget.rowCount()) if not self.list_widget.isRowHidden(row)]

    def _selected_rows(self) -> list[int]:
        selected = {index.row() for index in self.list_widget.selectionModel().selectedRows()}
        return [row for row in self._visible_rows() if row in selected]

    def _records_for_rows(self, rows: list[int]) -> list[dict]:
        return [self._rows[row] for row in rows if 0 <= row < len(self._rows)]

    def _emit_batch(self, action: str, *, selected_only: bool) -> None:
        rows = self._selected_rows() if selected_only else self._visible_rows()
        records = self._records_for_rows(rows)
        if not records:
            QMessageBox.information(self, "提示", "没有可操作的收藏。")
            return
        if action == "download":
            self.download_videos_requested.emit(records)
            return
        if not self._confirm_delete(len(records), selected_only=selected_only):
            return
        video_ids = [str(record.get("video_id") or "") for record in records]
        self.remove_videos_requested.emit([video_id for video_id in video_ids if video_id])

    def _confirm_delete(self, count: int, *, selected_only: bool) -> bool:
        scope = "选中的" if selected_only else "列表中当前显示的"
        # 如实说明只删收藏记录：本地已下载的文件不归收藏页管。
        answer = QMessageBox.question(
            self,
            "确认删除",
            f"将删除{scope} {count} 条收藏记录。\n\n已下载的本地文件不会被删除。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _update_batch_buttons(self) -> None:
        visible = self._visible_rows()
        selected = self._selected_rows()
        self.download_selected_button.setEnabled(bool(selected))
        self.delete_selected_button.setEnabled(bool(selected))
        self.download_all_button.setEnabled(bool(visible))
        self.delete_all_button.setEnabled(bool(visible))
        self.download_all_button.setToolTip(f"下载当前列表中显示的 {len(visible)} 个视频")
        self.delete_all_button.setToolTip(f"删除当前列表中显示的 {len(visible)} 条收藏记录")

    def _play_selected(self) -> None:
        row = self.list_widget.currentRow()
        if row >= 0:
            self._play_row(row)

    def _play_row(self, row: int) -> None:
        if not (0 <= row < self.list_widget.rowCount()):
            return
        item = self.list_widget.item(row, 0)
        url = str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
        if url:
            self.play_requested.emit(url)

    def _remove_selected(self) -> None:
        row = self.list_widget.currentRow()
        if row >= 0:
            self._remove_row(row)

    def _remove_row(self, row: int) -> None:
        if not (0 <= row < self.list_widget.rowCount()):
            return
        item = self.list_widget.item(row, 0)
        video_id = str(item.data(Qt.ItemDataRole.UserRole + 1) or "") if item else ""
        if video_id:
            self.remove_requested.emit(video_id)
