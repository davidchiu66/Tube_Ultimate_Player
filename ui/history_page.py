from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from database.history_repository import HistoryRepository
from ui.player_page import format_seconds


class HistoryPage(QWidget):
    play_requested = Signal(str)

    def __init__(self, history: HistoryRepository) -> None:
        super().__init__()
        self.history = history
        self.list_widget = QListWidget()
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
        layout.addLayout(header)
        layout.addWidget(self.list_widget, 1)

        self.refresh_button.clicked.connect(self.refresh)
        self.play_button.clicked.connect(self._play_selected)
        self.list_widget.itemDoubleClicked.connect(lambda _: self._play_selected())
        self.refresh()

    def refresh(self) -> None:
        self.list_widget.clear()
        for row in self.history.recent():
            title = row.get("title") or "未命名视频"
            duration = format_seconds(row.get("duration") or 0)
            count = row.get("play_count") or 1
            last_played_at = row.get("last_played_at") or ""
            item = QListWidgetItem(f"{title}\n{duration} | 播放 {count} 次 | {last_played_at}")
            item.setData(256, row.get("webpage_url") or "")
            self.list_widget.addItem(item)

    def _play_selected(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            return
        url = str(item.data(256) or "")
        if url:
            self.play_requested.emit(url)
