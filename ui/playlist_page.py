from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from resolver.models import PlaylistEntry, PlaylistInfo
from ui.player_page import format_seconds


class PlaylistPage(QWidget):
    back_requested = Signal()
    play_entry_requested = Signal(object, int)
    download_entries_requested = Signal(object)
    save_requested = Signal()
    auto_play_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._playlist: PlaylistInfo | None = None
        self._current_index = -1

        self.title_label = QLabel("播放列表")
        self.title_label.setObjectName("PageTitle")
        self.meta_label = QLabel("等待加载播放列表...")
        self.meta_label.setObjectName("MetaLabel")
        self.description_label = QLabel("双击列表项即可开始播放，支持 Ctrl 多选后批量下载。")
        self.description_label.setObjectName("MetaLabel")
        self.description_label.setWordWrap(True)
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)
        self.loading_bar.hide()

        self.back_button = QPushButton("返回")
        self.play_selected_button = QPushButton("播放选中")
        self.play_all_button = QPushButton("从头播放")
        self.download_selected_button = QPushButton("下载选中")
        self.download_all_button = QPushButton("下载全部")
        self.save_button = QPushButton("保存列表")
        self.auto_play_checkbox = QCheckBox("自动连播")
        self.select_all_button = QPushButton("全选")

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_widget.itemDoubleClicked.connect(self._play_double_clicked)
        self.list_widget.itemSelectionChanged.connect(self._update_button_state)

        top_actions = QHBoxLayout()
        top_actions.setSpacing(8)
        top_actions.addWidget(self.back_button)
        top_actions.addWidget(self.play_selected_button)
        top_actions.addWidget(self.play_all_button)
        top_actions.addWidget(self.download_selected_button)
        top_actions.addWidget(self.download_all_button)
        top_actions.addWidget(self.save_button)
        top_actions.addWidget(self.select_all_button)
        top_actions.addStretch(1)
        top_actions.addWidget(self.auto_play_checkbox)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(self.title_label)
        layout.addWidget(self.meta_label)
        layout.addWidget(self.description_label)
        layout.addWidget(self.loading_bar)
        layout.addLayout(top_actions)
        layout.addWidget(self.list_widget, 1)

        self.back_button.clicked.connect(self.back_requested)
        self.play_selected_button.clicked.connect(self._play_selected)
        self.play_all_button.clicked.connect(lambda: self._emit_play_index(0))
        self.download_selected_button.clicked.connect(self._download_selected)
        self.download_all_button.clicked.connect(self._download_all)
        self.save_button.clicked.connect(self.save_requested)
        self.select_all_button.clicked.connect(self.list_widget.selectAll)
        self.auto_play_checkbox.toggled.connect(self.auto_play_changed)

        self._update_button_state()

    def set_loading(self, loading: bool, message: str = "") -> None:
        self.loading_bar.setVisible(loading)
        if loading:
            self.title_label.setText("播放列表")
            self.meta_label.setText(message or "正在加载播放列表，请稍候...")
            self.list_widget.clear()
        self._update_button_state()

    def set_playlist(self, playlist: PlaylistInfo, current_index: int = -1, auto_play_next: bool = True) -> None:
        self._playlist = playlist
        self._current_index = current_index
        self.title_label.setText(playlist.title)
        self.meta_label.setText(
            f"{playlist.source_type} | {playlist.uploader or 'Unknown'} | 共 {len(playlist.entries)} 条"
        )
        self.auto_play_checkbox.blockSignals(True)
        self.auto_play_checkbox.setChecked(auto_play_next)
        self.auto_play_checkbox.blockSignals(False)

        self.list_widget.clear()
        for index, entry in enumerate(playlist.entries):
            text = self._format_entry_text(entry)
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.list_widget.addItem(item)
            if index == current_index:
                item.setSelected(True)
                self.list_widget.scrollToItem(item)

        self._refresh_current_highlight()
        self._update_button_state()

    def set_current_index(self, index: int) -> None:
        self._current_index = index
        self._refresh_current_highlight()

    def selected_entries(self) -> list[PlaylistEntry]:
        playlist = self._playlist
        if playlist is None:
            return []
        result: list[PlaylistEntry] = []
        for item in self.list_widget.selectedItems():
            index = int(item.data(Qt.ItemDataRole.UserRole))
            if 0 <= index < len(playlist.entries):
                result.append(playlist.entries[index])
        return result

    def _play_selected(self) -> None:
        entries = self.selected_entries()
        if not entries:
            QMessageBox.information(self, "提示", "请先选择一个视频。")
            return
        first = entries[0]
        playlist = self._playlist
        if playlist is None:
            return
        index = max(0, first.position - 1)
        self._emit_play_index(index)

    def _play_double_clicked(self, item: QListWidgetItem) -> None:
        self._emit_play_index(int(item.data(Qt.ItemDataRole.UserRole)))

    def _emit_play_index(self, index: int) -> None:
        playlist = self._playlist
        if playlist is None or not (0 <= index < len(playlist.entries)):
            return
        self.play_entry_requested.emit(playlist, index)

    def _download_selected(self) -> None:
        entries = self.selected_entries()
        if entries:
            self.download_entries_requested.emit(entries)

    def _download_all(self) -> None:
        if self._playlist and self._playlist.entries:
            self.download_entries_requested.emit(list(self._playlist.entries))

    def _refresh_current_highlight(self) -> None:
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            base_text = self._format_entry_text(self._playlist.entries[row]) if self._playlist else item.text()
            item.setText(("▶ " if row == self._current_index else "") + base_text)

    def _update_button_state(self) -> None:
        has_playlist = self._playlist is not None and bool(self._playlist.entries)
        has_selection = bool(self.list_widget.selectedItems())
        self.play_selected_button.setEnabled(has_selection)
        self.play_all_button.setEnabled(has_playlist)
        self.download_selected_button.setEnabled(has_selection)
        self.download_all_button.setEnabled(has_playlist)
        self.save_button.setEnabled(has_playlist)
        self.select_all_button.setEnabled(has_playlist)

    @staticmethod
    def _format_entry_text(entry: PlaylistEntry) -> str:
        parts = [f"{entry.position:02d}. {entry.title}"]
        meta = []
        if entry.uploader:
            meta.append(entry.uploader)
        if entry.duration:
            meta.append(format_seconds(entry.duration))
        if meta:
            parts.append(" | ".join(meta))
        return "  ".join(parts)
