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

from database.favorite_repository import FavoriteRepository
from ui.player_page import format_seconds


class FavoritePage(QWidget):
    play_requested = Signal(str)
    remove_requested = Signal(str)

    def __init__(self, favorites: FavoriteRepository) -> None:
        super().__init__()
        self.favorites = favorites

        self.list_widget = QListWidget()
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addLayout(header)
        layout.addWidget(self.list_widget, 1)

        self.refresh_button.clicked.connect(self.refresh)
        self.play_button.clicked.connect(self._play_selected)
        self.remove_button.clicked.connect(self._remove_selected)
        self.list_widget.itemDoubleClicked.connect(lambda _: self._play_selected())
        self.refresh()

    def refresh(self) -> None:
        self.list_widget.clear()
        for row in self.favorites.all():
            title = row.get("title") or "未命名视频"
            uploader = row.get("uploader") or "未知作者"
            duration = format_seconds(row.get("duration") or 0)
            updated_at = row.get("updated_at") or ""
            item = QListWidgetItem(f"{title}\n{uploader} | {duration} | {updated_at}")
            item.setData(256, row.get("webpage_url") or "")
            item.setData(257, row.get("video_id") or "")
            self.list_widget.addItem(item)

    def _play_selected(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            return
        url = str(item.data(256) or "")
        if url:
            self.play_requested.emit(url)

    def _remove_selected(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            return
        video_id = str(item.data(257) or "")
        if video_id:
            self.remove_requested.emit(video_id)
