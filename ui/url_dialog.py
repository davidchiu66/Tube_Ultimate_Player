from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
)

from services.config_service import ConfigService


class UrlPlayDialog(QDialog):
    def __init__(
        self,
        parent=None,
        initial_url: str = "",
        config: ConfigService | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("播放 URL")
        self.setModal(True)
        self.resize(560, 380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        label = QLabel("请输入视频 URL（支持 YouTube / Bilibili）")
        self.url_edit = QLineEdit(initial_url)
        self.url_edit.setPlaceholderText("https://www.youtube.com/watch?v=... 或 https://www.bilibili.com/video/...")

        history_label = QLabel("最近播放")
        history_label.setObjectName("MetaLabel")
        self.history_list = QListWidget()
        self.history_list.setObjectName("RecentUrlList")
        self.history_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history_list.customContextMenuRequested.connect(self._show_history_menu)
        self.history_list.itemClicked.connect(self._fill_from_item)
        self.history_list.itemActivated.connect(self._play_from_item)
        self.history_list.itemDoubleClicked.connect(self._play_from_item)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_button:
            ok_button.setText("播放")
        if cancel_button:
            cancel_button.setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addWidget(label)
        layout.addWidget(self.url_edit)
        layout.addWidget(history_label)
        layout.addWidget(self.history_list, 1)
        layout.addWidget(buttons)

        self.url_edit.returnPressed.connect(self.accept)
        self.url_edit.selectAll()
        self.url_edit.setFocus()

        self._reload_history()

    def url(self) -> str:
        return self.url_edit.text().strip()

    def _reload_history(self) -> None:
        self.history_list.clear()
        if self._config is None:
            self.history_list.setVisible(False)
            return
        entries = self._config.recent_urls()
        self.history_list.setVisible(bool(entries))
        for entry in entries:
            url = entry.get("url", "")
            title = entry.get("title", "")
            item = QListWidgetItem(f"{title or url}\n{url}" if title else url)
            item.setData(Qt.ItemDataRole.UserRole, url)
            item.setToolTip(url)
            self.history_list.addItem(item)

    def _fill_from_item(self, item: QListWidgetItem) -> None:
        url = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if url:
            self.url_edit.setText(url)

    def _play_from_item(self, item: QListWidgetItem) -> None:
        url = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if url:
            self.url_edit.setText(url)
            self.accept()

    def _show_history_menu(self, pos) -> None:
        if self._config is None:
            return
        item = self.history_list.itemAt(pos)
        menu = QMenu(self)
        remove_action = menu.addAction("删除此条") if item is not None else None
        clear_action = menu.addAction("清空历史") if self.history_list.count() else None
        if remove_action is None and clear_action is None:
            return
        chosen = menu.exec(self.history_list.mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is remove_action and item is not None:
            self._config.remove_recent_url(str(item.data(Qt.ItemDataRole.UserRole) or ""))
        elif chosen is clear_action:
            self._config.clear_recent_urls()
        self._config.save()
        self._reload_history()
