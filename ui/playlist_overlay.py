from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSize, QTimer, Qt, Signal
from PySide6.QtNetwork import QNetworkAccessManager
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from resolver.models import PlaylistEntry, PlaylistInfo, SavedPlaylist
from ui.thumbnail_cache import ThumbnailCache


ITEM_HEIGHT = 92
THUMB_WIDTH = 120
THUMB_HEIGHT = 68
PANEL_WIDTH = 430


class PlaylistItemWidget(QFrame):
    def __init__(
        self,
        entry: PlaylistEntry,
        index: int,
        network: QNetworkAccessManager,
        thumbnail_cache: ThumbnailCache,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.entry = entry
        self.index = index
        self._network = network
        self._thumbnail_cache = thumbnail_cache
        self._thumbnail_requested = False
        self.setObjectName("PlaylistOverlayItem")
        self.setFixedHeight(ITEM_HEIGHT)

        self.index_label = QLabel(str(index + 1))
        self.index_label.setObjectName("PlaylistOverlayIndex")
        self.index_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.index_label.setFixedWidth(24)

        self.thumbnail_label = QLabel()
        self.thumbnail_label.setObjectName("PlaylistOverlayThumb")
        self.thumbnail_label.setFixedSize(THUMB_WIDTH, THUMB_HEIGHT)
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setText("封面")

        self.title_label = QLabel()
        self.title_label.setObjectName("PlaylistOverlayTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.title_label.setFixedHeight(38)

        self.meta_label = QLabel()
        self.meta_label.setObjectName("PlaylistOverlayMeta")
        self.meta_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(6)
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.meta_label)
        text_layout.addStretch(1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self.index_label)
        layout.addWidget(self.thumbnail_label)
        layout.addLayout(text_layout, 1)

        self._apply_entry()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_title()

    def set_active(self, active: bool) -> None:
        self.setProperty("active", active)
        self._refresh_style()

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self._refresh_style()

    def ensure_thumbnail_loaded(self) -> None:
        if self._thumbnail_requested:
            return
        self._thumbnail_requested = True
        self._load_thumbnail()

    def _refresh_style(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)

    def _apply_entry(self) -> None:
        self._apply_title()
        meta = self.entry.uploader or ""
        if self.entry.duration:
            duration = format_seconds(self.entry.duration)
            meta = f"{meta} - {duration}" if meta else duration
        self.meta_label.setText(meta)

    def _apply_title(self) -> None:
        width = max(80, self.width() - 180)
        self.title_label.setText(elide_two_lines(self.title_label, self.entry.title, width))

    def _load_thumbnail(self) -> None:
        self._thumbnail_cache.load(
            self._network,
            self.entry.thumbnail,
            self.thumbnail_label.size(),
            self.thumbnail_label,
            empty_text="无封面",
            error_text="封面失败",
        )


class PlaylistOverlay(QFrame):
    entry_activated = Signal(int)
    download_entries_requested = Signal(object)
    save_requested = Signal()
    load_saved_requested = Signal(str)
    delete_saved_requested = Signal(str)
    auto_play_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PlaylistOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMouseTracking(True)

        self._playlist: PlaylistInfo | None = None
        self._current_index = -1
        self._saved_playlists: list[SavedPlaylist] = []
        self._open = False
        self._network = QNetworkAccessManager(self)
        self._thumbnail_cache = ThumbnailCache(self)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(220)
        self._hide_timer.timeout.connect(self.hide_overlay)

        self._animation = QPropertyAnimation(self, b"pos", self)
        self._animation.setDuration(220)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.title_label = QLabel("播放列表")
        self.title_label.setObjectName("OverlayTitle")
        self.meta_label = QLabel("当前没有可用的播放列表")
        self.meta_label.setObjectName("MetaLabel")

        self.saved_combo = QComboBox()
        self.saved_combo.addItem("选择已保存列表", "")
        self.load_button = QPushButton("加载")
        self.save_button = QPushButton("保存")
        self.delete_button = QPushButton("删除")
        self.auto_play_checkbox = QCheckBox("自动连播")

        combo_row = QHBoxLayout()
        combo_row.setSpacing(6)
        combo_row.addWidget(self.saved_combo, 1)
        combo_row.addWidget(self.load_button)
        combo_row.addWidget(self.save_button)
        combo_row.addWidget(self.delete_button)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("PlaylistOverlayList")
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list_widget.setSpacing(4)
        self.list_widget.itemDoubleClicked.connect(self._double_clicked)
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        self.list_widget.verticalScrollBar().valueChanged.connect(self._schedule_visible_thumbnail_load)

        self.play_selected_button = QPushButton("播放选中")
        self.download_selected_button = QPushButton("下载选中")
        self.download_all_button = QPushButton("下载全部")
        self.cancel_button = QPushButton("取消")

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        action_row.addWidget(self.play_selected_button)
        action_row.addWidget(self.download_selected_button)
        action_row.addWidget(self.download_all_button)
        action_row.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self.title_label)
        layout.addWidget(self.meta_label)
        layout.addLayout(combo_row)
        layout.addWidget(self.auto_play_checkbox)
        layout.addWidget(self.list_widget, 1)
        layout.addLayout(action_row)

        self.load_button.clicked.connect(self._load_saved)
        self.save_button.clicked.connect(self.save_requested)
        self.delete_button.clicked.connect(self._delete_saved)
        self.saved_combo.currentIndexChanged.connect(self._update_button_state)
        self.play_selected_button.clicked.connect(self._play_selected)
        self.download_selected_button.clicked.connect(self._download_selected)
        self.download_all_button.clicked.connect(self._download_all)
        self.cancel_button.clicked.connect(self.hide_overlay)
        self.auto_play_checkbox.toggled.connect(self.auto_play_changed)

        self.setFixedWidth(PANEL_WIDTH)
        self._update_button_state()

    def set_playlist(
        self,
        playlist: PlaylistInfo | None,
        *,
        current_index: int = -1,
        auto_play_next: bool = True,
    ) -> None:
        self._playlist = playlist
        self._current_index = current_index
        self.auto_play_checkbox.blockSignals(True)
        self.auto_play_checkbox.setChecked(auto_play_next)
        self.auto_play_checkbox.blockSignals(False)
        self.list_widget.clear()
        if playlist is None or not playlist.entries:
            self.title_label.setText("播放列表")
            self._update_empty_state_text()
            self._update_button_state()
            if not self.has_available_content():
                self.hide_overlay(animated=False)
            return

        self.title_label.setText(playlist.title)
        self.meta_label.setText(f"{playlist.uploader or 'Unknown'} - {len(playlist.entries)} 条")
        for index, entry in enumerate(playlist.entries):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setSizeHint(self._item_size_hint())
            self.list_widget.addItem(item)
            widget = PlaylistItemWidget(entry, index, self._network, self._thumbnail_cache, self.list_widget)
            self.list_widget.setItemWidget(item, widget)
        self.set_current_index(current_index)
        self._sync_selection_visuals()
        self._update_button_state()
        self._schedule_visible_thumbnail_load()

    def set_saved_playlists(self, playlists: list[SavedPlaylist], current_key: str = "") -> None:
        self._saved_playlists = list(playlists)
        self.saved_combo.blockSignals(True)
        self.saved_combo.clear()
        self.saved_combo.addItem("选择已保存列表", "")
        selected_index = 0
        for index, playlist in enumerate(self._saved_playlists, start=1):
            self.saved_combo.addItem(playlist.name, playlist.playlist_key)
            if playlist.playlist_key == current_key:
                selected_index = index
        self.saved_combo.setCurrentIndex(selected_index)
        self.saved_combo.blockSignals(False)
        if not self.has_playlist():
            self._update_empty_state_text()
            self._sync_selection_visuals()
        self._update_button_state()

    def set_current_index(self, index: int) -> None:
        self._current_index = index
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            widget = self.list_widget.itemWidget(item)
            if isinstance(widget, PlaylistItemWidget):
                widget.set_active(row == index)
            if row == index:
                item.setSelected(True)
                self.list_widget.scrollToItem(item)
        self._sync_selection_visuals()

    def handle_pointer(self, pos: QPoint) -> None:
        if not self.has_available_content():
            return
        if self.geometry().contains(pos):
            self.show_overlay()
            return
        if self._is_in_hot_zone(pos):
            self.show_overlay()
            return
        self.schedule_hide()

    def handle_idle_timeout(self) -> None:
        self.hide_overlay()

    def schedule_hide(self) -> None:
        self._hide_timer.start()

    def show_overlay(self, animated: bool = True) -> None:
        if not self.has_available_content():
            return
        self._hide_timer.stop()
        self._open = True
        self.show()
        self.raise_()
        self._move_panel(animated)
        self._schedule_visible_thumbnail_load()

    def hide_overlay(self, animated: bool = True) -> None:
        self._hide_timer.stop()
        self._open = False
        self._move_panel(animated)

    def relayout(self, host_rect) -> None:
        panel_height = max(320, host_rect.height() - 24)
        self.setFixedHeight(panel_height)
        self._move_panel(animated=False)

    def has_playlist(self) -> bool:
        return self._playlist is not None and bool(self._playlist.entries)

    def has_available_content(self) -> bool:
        return self.has_playlist() or bool(self._saved_playlists)

    def current_saved_key(self) -> str:
        return str(self.saved_combo.currentData() or "")

    def _is_in_hot_zone(self, pos: QPoint) -> bool:
        parent = self.parentWidget()
        if parent is None:
            return False
        return pos.x() >= parent.width() - 22

    def _move_panel(self, animated: bool) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        visible_x = parent.width() - self.width() - 12
        hidden_x = parent.width() + 4
        y = max(12, (parent.height() - self.height()) // 2)
        target = QPoint(visible_x if self._open else hidden_x, y)
        if animated:
            self._animation.stop()
            self._animation.setStartValue(self.pos())
            self._animation.setEndValue(target)
            self._animation.start()
        else:
            self._animation.stop()
            self.move(target)
        if not self._open and not animated:
            self.hide()
        elif self._open:
            self.show()

    def _schedule_visible_thumbnail_load(self, _value: int | None = None) -> None:
        if self._open:
            QTimer.singleShot(0, self._load_visible_thumbnails)

    def _load_visible_thumbnails(self) -> None:
        if not self._open:
            return
        viewport_rect = self.list_widget.viewport().rect().adjusted(0, -ITEM_HEIGHT, 0, ITEM_HEIGHT)
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if not self.list_widget.visualItemRect(item).intersects(viewport_rect):
                continue
            widget = self.list_widget.itemWidget(item)
            if isinstance(widget, PlaylistItemWidget):
                widget.ensure_thumbnail_loaded()

    def _play_selected(self) -> None:
        items = self.list_widget.selectedItems()
        if not items:
            QMessageBox.information(self, "提示", "请先选择一个视频。")
            return
        self.entry_activated.emit(int(items[0].data(Qt.ItemDataRole.UserRole)))
        self.hide_overlay()

    def _double_clicked(self, item: QListWidgetItem) -> None:
        self.entry_activated.emit(int(item.data(Qt.ItemDataRole.UserRole)))
        self.hide_overlay()

    def _download_selected(self) -> None:
        entries = self._selected_entries()
        if entries:
            self.download_entries_requested.emit(entries)

    def _download_all(self) -> None:
        if self._playlist and self._playlist.entries:
            self.download_entries_requested.emit(list(self._playlist.entries))

    def _selected_entries(self) -> list[PlaylistEntry]:
        playlist = self._playlist
        if playlist is None:
            return []
        result: list[PlaylistEntry] = []
        for item in self.list_widget.selectedItems():
            index = int(item.data(Qt.ItemDataRole.UserRole))
            if 0 <= index < len(playlist.entries):
                result.append(playlist.entries[index])
        return result

    def _load_saved(self) -> None:
        playlist_key = self.current_saved_key()
        if playlist_key:
            self.load_saved_requested.emit(playlist_key)

    def _delete_saved(self) -> None:
        playlist_key = self.current_saved_key()
        if playlist_key:
            self.delete_saved_requested.emit(playlist_key)

    def _on_selection_changed(self) -> None:
        self._sync_selection_visuals()
        self._update_button_state()

    def _update_button_state(self) -> None:
        has_playlist = self.has_playlist()
        has_available = self.has_available_content()
        has_selection = bool(self.list_widget.selectedItems())
        self.play_selected_button.setEnabled(has_selection)
        self.download_selected_button.setEnabled(has_selection)
        self.download_all_button.setEnabled(has_playlist)
        self.cancel_button.setEnabled(has_available or self._open)
        self.save_button.setEnabled(has_playlist)
        saved_key = self.current_saved_key()
        self.load_button.setEnabled(bool(saved_key))
        self.delete_button.setEnabled(bool(saved_key))

    @staticmethod
    def _item_size_hint():
        return QSize(PANEL_WIDTH - 28, ITEM_HEIGHT)

    def _update_empty_state_text(self) -> None:
        count = len(self._saved_playlists)
        if count > 0:
            self.meta_label.setText(f"已保存 {count} 个播放列表，可从下拉框加载")
        else:
            self.meta_label.setText("当前没有可用的播放列表")

    def _sync_selection_visuals(self) -> None:
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            widget = self.list_widget.itemWidget(item)
            if isinstance(widget, PlaylistItemWidget):
                widget.set_selected(item.isSelected())


def elide_two_lines(label: QLabel, text: str, width: int) -> str:
    source = str(text or "").strip()
    if not source:
        return ""
    metrics = label.fontMetrics()
    words = source.split()
    if not words:
        return metrics.elidedText(source, Qt.TextElideMode.ElideRight, width * 2)

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if metrics.horizontalAdvance(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) == 1:
            break
    remainder_words = words[len(" ".join(lines + ([current] if current else [])).split()):]
    if current and len(lines) < 2:
        lines.append(current)
    if remainder_words and lines:
        tail = " ".join(remainder_words)
        lines[-1] = metrics.elidedText(f"{lines[-1]} {tail}".strip(), Qt.TextElideMode.ElideRight, width)
    return "\n".join(lines[:2])


def format_seconds(seconds: int | float) -> str:
    seconds = int(seconds or 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
