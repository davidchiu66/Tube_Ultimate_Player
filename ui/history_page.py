from __future__ import annotations

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from database.history_repository import HistoryRepository
from resolver.source_utils import source_site_label
from ui.player_page import format_seconds


class HistoryPage(QWidget):
    play_requested = Signal(str)

    def __init__(self, history: HistoryRepository) -> None:
        super().__init__()
        self.history = history
        self._rows: list[dict] = []
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索标题、来源或链接")
        self.search_edit.setClearButtonEnabled(True)
        self.list_widget = QTableWidget(0, 7)
        self.list_widget.setObjectName("LibraryTable")
        self.list_widget.setHorizontalHeaderLabels(["标题", "来源", "作者", "时长", "播放次数", "最近播放", "操作"])
        self.list_widget.verticalHeader().setVisible(False)
        self.list_widget.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.list_widget.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.list_widget.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.list_widget.setAlternatingRowColors(False)
        table_header = self.list_widget.horizontalHeader()
        table_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.list_widget.setColumnWidth(1, 90)
        self.list_widget.setColumnWidth(2, 130)
        self.list_widget.setColumnWidth(3, 90)
        self.list_widget.setColumnWidth(4, 100)
        self.list_widget.setColumnWidth(5, 170)
        self.list_widget.setColumnWidth(6, 100)
        self.list_widget.verticalHeader().setDefaultSectionSize(40)

        self.play_button = QPushButton("播放选中")
        self.refresh_button = QPushButton("刷新")

        header = QHBoxLayout()
        title = QLabel("播放历史")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.refresh_button)
        header.addWidget(self.play_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addLayout(header)
        layout.addWidget(self.search_edit)
        layout.addWidget(self.list_widget, 1)

        self.search_edit.textChanged.connect(self._apply_filter)
        self.refresh_button.clicked.connect(self.refresh)
        self.play_button.clicked.connect(self._play_selected)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._play_selected())
        self.refresh()

    def refresh(self) -> None:
        self._rows = self.history.recent()
        self.list_widget.setRowCount(0)
        for row_data in self._rows:
            row = self.list_widget.rowCount()
            self.list_widget.insertRow(row)
            self.list_widget.setRowHeight(row, 40)
            values = [
                str(row_data.get("title") or "未命名视频"),
                source_site_label(row_data.get("source_site", ""), row_data.get("webpage_url", "")),
                str(row_data.get("uploader") or "未知作者"),
                format_seconds(row_data.get("duration") or 0),
                f"{row_data.get('play_count') or 1} 次",
                str(row_data.get("last_played_at") or ""),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (1, 3, 4, 5):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row_data.get("webpage_url") or "")
                self.list_widget.setItem(row, column, item)

            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(4, 0, 4, 0)
            play_button = QPushButton("播放")
            play_button.setFixedHeight(28)
            play_button.setMinimumWidth(56)
            play_button.setObjectName("LibraryActionButton")
            play_button.clicked.connect(lambda _=False, index=row: self._play_row(index))
            action_layout.addWidget(play_button)
            self.list_widget.setCellWidget(row, 6, actions)
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
