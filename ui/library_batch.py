from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QPushButton


class LibraryBatchMixin:
    """收藏、历史等资料库页面共用的批量操作行为。"""

    _batch_empty_message = "没有可操作的记录。"
    _batch_download_all_tooltip = "下载当前列表中显示的 {count} 个视频"
    _batch_delete_all_tooltip = "删除当前列表中显示的 {count} 条记录"

    @property
    def _batch_records(self) -> list[dict]:
        raise NotImplementedError

    def _batch_delete_confirm_text(self, count: int, selected_only: bool) -> str:
        raise NotImplementedError

    def _batch_id_of(self, record: dict) -> str:
        raise NotImplementedError

    def _build_batch_row(self) -> QHBoxLayout:
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

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for button in self._batch_buttons:
            button.setFixedHeight(28)
            button.setObjectName("LibraryActionButton")
            row.addWidget(button)
        row.addStretch(1)

        self.download_selected_button.clicked.connect(
            lambda: self._emit_batch("download", selected_only=True)
        )
        self.download_all_button.clicked.connect(
            lambda: self._emit_batch("download", selected_only=False)
        )
        self.delete_selected_button.clicked.connect(
            lambda: self._emit_batch("delete", selected_only=True)
        )
        self.delete_all_button.clicked.connect(
            lambda: self._emit_batch("delete", selected_only=False)
        )
        self.list_widget.itemSelectionChanged.connect(self._update_batch_buttons)
        return row

    def _visible_rows(self) -> list[int]:
        """返回当前搜索筛选后的可见行，保持表格顺序。"""
        return [
            row
            for row in range(self.list_widget.rowCount())
            if not self.list_widget.isRowHidden(row)
        ]

    def _selected_rows(self) -> list[int]:
        selected = {index.row() for index in self.list_widget.selectionModel().selectedRows()}
        return [row for row in self._visible_rows() if row in selected]

    def _records_for_rows(self, rows: list[int]) -> list[dict]:
        records = self._batch_records
        return [records[row] for row in rows if 0 <= row < len(records)]

    def _emit_batch(self, action: str, *, selected_only: bool) -> None:
        rows = self._selected_rows() if selected_only else self._visible_rows()
        records = self._records_for_rows(rows)
        if not records:
            QMessageBox.information(self, "提示", self._batch_empty_message)
            return
        if action == "download":
            self.download_videos_requested.emit(records)
            return
        if action != "delete" or not self._confirm_delete(len(records), selected_only=selected_only):
            return
        video_ids = [self._batch_id_of(record) for record in records]
        self.remove_videos_requested.emit([video_id for video_id in video_ids if video_id])

    def _confirm_delete(self, count: int, *, selected_only: bool) -> bool:
        answer = QMessageBox.question(
            self,
            "确认删除",
            self._batch_delete_confirm_text(count, selected_only),
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
        self.download_all_button.setToolTip(
            self._batch_download_all_tooltip.format(count=len(visible))
        )
        self.delete_all_button.setToolTip(
            self._batch_delete_all_tooltip.format(count=len(visible))
        )
