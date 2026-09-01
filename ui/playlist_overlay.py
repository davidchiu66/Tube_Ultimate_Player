from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSize, QTimer, Qt, Signal
from PySide6.QtNetwork import QNetworkAccessManager
from PySide6.QtWidgets import (
    QCheckBox,
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

from resolver.models import PlaylistEntry, PlaylistInfo, PlaylistSection, SavedPlaylist
from ui.text_elision import elide_multiline_text, format_seconds, format_upload_date
from ui.thumbnail_cache import ThumbnailCache
from ui.widgets import NoScrollComboBox


ITEM_HEIGHT = 92
THUMB_WIDTH = 120
THUMB_HEIGHT = 68
PANEL_WIDTH = 430
MIN_PANEL_WIDTH = 280
HOT_ZONE_WIDTH = 22
PANEL_MARGIN = 12


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
        self._active = False
        self._selected = False
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
        # 重绘（unpolish/polish）代价不低，状态没变时直接跳过。
        if self._active == active:
            return
        self._active = active
        self.setProperty("active", active)
        self._refresh_style()

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
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
        upload_date = format_upload_date(getattr(self.entry, "upload_date", ""))
        if upload_date:
            meta = f"{meta} - {upload_date}" if meta else upload_date
        self.meta_label.setText(meta)

    def _apply_title(self) -> None:
        width = max(80, self.width() - 180)
        self.title_label.setText(elide_multiline_text(self.title_label, self.entry.title, width, 2))

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
    # 右侧作者列表尚未准备好时，由宿主决定如何展示提示（通常是 Toast）。
    playlist_loading_hint_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        side: str = "right",
        default_title: str = "播放列表",
        object_name: str = "PlaylistOverlay",
        empty_text: str = "当前没有可用的播放列表",
    ) -> None:
        super().__init__(parent)
        self._side = "left" if str(side).lower() == "left" else "right"
        self._default_title = default_title
        self._empty_text = empty_text
        self.setObjectName(object_name)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMouseTracking(True)

        self._playlist: PlaylistInfo | None = None
        self._playlist_signature: tuple | None = None
        self._current_index = -1
        self._active_row = -1
        self._selected_rows: set[int] = set()
        self._saved_playlists: list[SavedPlaylist] = []
        # 与播放列表页同一套防护：程序化重填下拉框时禁止"切换即加载"。
        self._suppress_saved_auto_load = 0
        self._loaded_saved_key = ""
        self._open = False
        # 左右两侧面板互斥显示：两份 PANEL_WIDTH 要 ≥884px，窄窗口下必然遮挡视频。
        self._sibling_overlay: PlaylistOverlay | None = None
        self._context_available = False
        self._loading = False
        self._loading_hint_shown = False
        self._section_mode = False
        self._active_section_id = ""
        self._display_indices: list[int] = []
        self._network = QNetworkAccessManager(self)
        self._thumbnail_cache = ThumbnailCache(self)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(220)
        self._hide_timer.timeout.connect(self.hide_overlay)

        self._animation = QPropertyAnimation(self, b"pos", self)
        self._animation.setDuration(220)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.title_label = QLabel(default_title)
        self.title_label.setObjectName("OverlayTitle")
        self.meta_label = QLabel(empty_text)
        self.meta_label.setObjectName("MetaLabel")

        self.saved_combo = NoScrollComboBox()
        self.saved_combo.addItem("选择已保存列表", "")
        self.save_button = QPushButton("保存")
        self.delete_button = QPushButton("删除")
        self.auto_play_checkbox = QCheckBox("自动连播")
        self.back_button = QPushButton("返回合集")
        self.back_button.clicked.connect(self._show_sections)
        self.back_button.hide()

        combo_row = QHBoxLayout()
        combo_row.setSpacing(6)
        combo_row.addWidget(self.saved_combo, 1)
        combo_row.addWidget(self.save_button)
        combo_row.addWidget(self.delete_button)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName(f"{object_name}List")
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
        layout.addWidget(self.back_button)
        layout.addLayout(combo_row)
        layout.addWidget(self.auto_play_checkbox)
        layout.addWidget(self.list_widget, 1)
        layout.addLayout(action_row)

        self.save_button.clicked.connect(self.save_requested)
        self.delete_button.clicked.connect(self._delete_saved)
        self.saved_combo.currentIndexChanged.connect(self._handle_saved_selection_changed)
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
        auto_play_next: bool = False,
    ) -> None:
        self._playlist = playlist
        self.auto_play_checkbox.blockSignals(True)
        self.auto_play_checkbox.setChecked(auto_play_next)
        self.auto_play_checkbox.blockSignals(False)

        signature = self._signature_for(playlist)
        if signature is None:
            self._section_mode = False
            self._active_section_id = ""
            self._display_indices = []
            self.back_button.hide()
            self.play_selected_button.setText("播放选中")
            self._playlist_signature = None
            self._current_index = current_index
            self._active_row = -1
            self._selected_rows = set()
            self.list_widget.clear()
            self.title_label.setText(self._default_title)
            self._update_empty_state_text()
            self._update_button_state()
            if not self.has_available_content():
                self.hide_overlay(animated=False)
            return

        self.title_label.setText(playlist.title)
        if playlist.sections:
            self.meta_label.setText(f"{playlist.uploader or 'Unknown'} - {len(playlist.sections)} 个专辑")
        else:
            self.meta_label.setText(f"{playlist.uploader or 'Unknown'} - {len(playlist.entries)} 条")

        if signature == self._playlist_signature:
            self.set_current_index(current_index)
            self._update_button_state()
            return

        self._playlist_signature = signature
        self._section_mode = False
        self._active_section_id = ""
        self._display_indices = []
        self.back_button.hide()
        self._selected_rows = set()
        # 列表整体重建后旧的行号已失效，活动行必须一并复位。
        self._active_row = -1
        self.list_widget.blockSignals(True)
        try:
            self.list_widget.clear()
            if playlist.sections:
                self._section_mode = False
                self.play_selected_button.setText("打开专辑")
                rows = [
                    PlaylistEntry(
                        playlist_id=playlist.playlist_id,
                        video_id=f"__section__:{section.section_id}",
                        title=section.title,
                        webpage_url="",
                        source_site=playlist.source_site,
                        uploader=f"专辑 · {len(section.entries)} 集",
                        thumbnail=section.thumbnail,
                        position=section.position,
                    )
                    for section in playlist.sections
                ]
                self._display_indices = list(range(len(rows)))
            else:
                self.play_selected_button.setText("播放选中")
                rows = list(playlist.entries)
                self._display_indices = list(range(len(rows)))
            for row_index, entry in enumerate(rows):
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, row_index)
                item.setData(Qt.ItemDataRole.UserRole + 1, bool(playlist.sections))
                item.setSizeHint(self._item_size_hint())
                self.list_widget.addItem(item)
                widget = PlaylistItemWidget(entry, row_index, self._network, self._thumbnail_cache, self.list_widget)
                self.list_widget.setItemWidget(item, widget)
        finally:
            self.list_widget.blockSignals(False)
        self.set_current_index(current_index)
        self._sync_selection_visuals()
        self._update_button_state()
        self._schedule_visible_thumbnail_load()

    @staticmethod
    def _signature_for(playlist: PlaylistInfo | None) -> tuple | None:
        if playlist is None or not playlist.entries:
            return None
        return (
            tuple(
                (entry.video_id, entry.title, entry.thumbnail, entry.duration, entry.uploader, entry.local_path)
                for entry in playlist.entries
            ),
            tuple(
                (section.section_id, section.title, section.thumbnail, len(section.entries))
                for section in playlist.sections
            ),
        )

    def set_saved_playlists(self, playlists: list[SavedPlaylist], current_key: str = "") -> None:
        self._saved_playlists = list(playlists)
        self._suppress_saved_auto_load += 1
        self.saved_combo.blockSignals(True)
        try:
            self.saved_combo.clear()
            self.saved_combo.addItem("选择已保存列表", "")
            selected_index = 0
            for index, playlist in enumerate(self._saved_playlists, start=1):
                self.saved_combo.addItem(playlist.name, playlist.playlist_key)
                if playlist.playlist_key == current_key:
                    selected_index = index
            self.saved_combo.setCurrentIndex(selected_index)
        finally:
            self.saved_combo.blockSignals(False)
            self._suppress_saved_auto_load -= 1
        # 记住主窗口刚加载完的那一项，避免用户手工再选一次时重复加载。
        self._loaded_saved_key = str(current_key or "")
        if not self.has_playlist():
            self._update_empty_state_text()
            self._sync_selection_visuals()
        self._update_button_state()

    def set_current_index(self, index: int) -> None:
        self._current_index = index
        display_index = index
        if self._playlist and self._playlist.sections and not self._section_mode:
            if 0 <= index < len(self._playlist.entries):
                section_id = self._playlist.entries[index].section_id
                section_index = next(
                    (row for row, section in enumerate(self._playlist.sections) if section.section_id == section_id),
                    -1,
                )
                if section_index >= 0:
                    self._open_section(section_index)
                    self.set_current_index(index)
                    return
            display_index = -1
        elif self._section_mode:
            try:
                display_index = self._display_indices.index(index)
            except ValueError:
                display_index = -1
        # 只动"上一个活动行"和"新活动行"两行，避免整表 unpolish/polish。
        if self._active_row != display_index:
            previous = self._row_widget(self._active_row)
            if previous is not None:
                previous.set_active(False)
            self._active_row = display_index
        current = self._row_widget(display_index)
        if current is not None:
            current.set_active(True)
        if 0 <= display_index < self.list_widget.count():
            item = self.list_widget.item(display_index)
            item.setSelected(True)
            self.list_widget.scrollToItem(item)
        self._sync_selection_visuals()

    def _row_widget(self, row: int) -> PlaylistItemWidget | None:
        if not 0 <= row < self.list_widget.count():
            return None
        widget = self.list_widget.itemWidget(self.list_widget.item(row))
        return widget if isinstance(widget, PlaylistItemWidget) else None

    def handle_pointer(self, pos: QPoint) -> None:
        if self.geometry().contains(pos):
            if self._loading:
                self._request_loading_hint()
                return
            if not self.has_available_content():
                return
            self.show_overlay()
            return
        if self._is_in_hot_zone(pos):
            if self._loading:
                self._request_loading_hint()
                return
            self._loading_hint_shown = False
            if not self.has_available_content():
                return
            self.show_overlay()
            return
        self._loading_hint_shown = False
        self.schedule_hide()

    def set_loading_state(self, loading: bool) -> None:
        """标记当前作者播放列表是否仍在解析，避免空面板被误判为无功能。"""
        loading = bool(loading)
        if self._loading == loading:
            return
        self._loading = loading
        if not loading:
            self._loading_hint_shown = False
        if loading:
            self.hide_overlay(animated=False)

    def is_loading(self) -> bool:
        return self._loading

    def _request_loading_hint(self) -> None:
        if self._loading_hint_shown:
            return
        self._loading_hint_shown = True
        self.playlist_loading_hint_requested.emit()

    def handle_idle_timeout(self) -> None:
        self.hide_overlay()

    def schedule_hide(self) -> None:
        self._hide_timer.start()

    def show_overlay(self, animated: bool = True) -> None:
        if not self.has_available_content():
            return
        self._hide_timer.stop()
        # 先收起兄弟面板，再滑入自己：两侧同时展开会互相遮挡视频画面。
        sibling = self._sibling_overlay
        if sibling is not None and sibling.is_open():
            sibling.hide_overlay()
        self._open = True
        self.show()
        self.raise_()
        self._move_panel(animated)
        self._schedule_visible_thumbnail_load()

    def hide_overlay(self, animated: bool = True) -> None:
        self._hide_timer.stop()
        self._open = False
        self._move_panel(animated)

    def is_open(self) -> bool:
        return self._open

    def set_sibling_overlay(self, overlay: PlaylistOverlay | None) -> None:
        self._sibling_overlay = overlay

    def relayout(self, host_rect) -> None:
        panel_height = max(320, host_rect.height() - 24)
        self.setFixedHeight(panel_height)
        # 窄窗口下把面板压到半屏以内，否则 430px 会盖住大半个画面。
        panel_width = min(PANEL_WIDTH, max(MIN_PANEL_WIDTH, host_rect.width() // 2 - 20))
        if panel_width != self.width():
            self.setFixedWidth(panel_width)
            self._resize_item_hints()
        self._move_panel(animated=False)

    def has_playlist(self) -> bool:
        return self._playlist is not None and bool(self._playlist.entries)

    def has_available_content(self) -> bool:
        return self.has_playlist() or bool(self._saved_playlists) or self._context_available

    def set_context_available(self, available: bool) -> None:
        """允许面板在没有内容时也能滑入（左侧合集面板要能显示"不属于任何合集"的空态）。"""
        self._context_available = bool(available)
        if not self._context_available and not self.has_available_content():
            self.hide_overlay(animated=False)

    def current_saved_key(self) -> str:
        return str(self.saved_combo.currentData() or "")

    def _is_in_hot_zone(self, pos: QPoint) -> bool:
        parent = self.parentWidget()
        if parent is None:
            return False
        if self._side == "left":
            return pos.x() <= HOT_ZONE_WIDTH
        return pos.x() >= parent.width() - HOT_ZONE_WIDTH

    def _move_panel(self, animated: bool) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        if self._side == "left":
            visible_x = PANEL_MARGIN
            hidden_x = -self.width() - 4
        else:
            visible_x = parent.width() - self.width() - PANEL_MARGIN
            hidden_x = parent.width() + 4
        y = max(PANEL_MARGIN, (parent.height() - self.height()) // 2)
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
        item = items[0]
        if bool(item.data(Qt.ItemDataRole.UserRole + 1)):
            self._open_section(int(item.data(Qt.ItemDataRole.UserRole)))
            return
        self.entry_activated.emit(self._display_indices[int(item.data(Qt.ItemDataRole.UserRole))])
        self.hide_overlay()

    def _double_clicked(self, item: QListWidgetItem) -> None:
        if bool(item.data(Qt.ItemDataRole.UserRole + 1)):
            self._open_section(int(item.data(Qt.ItemDataRole.UserRole)))
            return
        self.entry_activated.emit(self._display_indices[int(item.data(Qt.ItemDataRole.UserRole))])
        self.hide_overlay()

    def _open_section(self, index: int) -> None:
        playlist = self._playlist
        if playlist is None or not (0 <= index < len(playlist.sections)):
            return
        section = playlist.sections[index]
        self._section_mode = True
        self._active_section_id = section.section_id
        self.back_button.show()
        self.play_selected_button.setText("播放选中")
        self.title_label.setText(section.title)
        self.meta_label.setText(f"{len(section.entries)} 集")
        self._render_entries(section.entries)

    def _show_sections(self) -> None:
        playlist = self._playlist
        if playlist is None or not playlist.sections:
            return
        self._section_mode = False
        self._active_section_id = ""
        self.back_button.hide()
        self.play_selected_button.setText("打开专辑")
        self.title_label.setText(playlist.title)
        self.meta_label.setText(
            f"{playlist.uploader or 'Unknown'} - {len(playlist.sections)} 个专辑"
        )
        self._render_entries(
            [
                PlaylistEntry(
                    playlist_id=playlist.playlist_id,
                    video_id=f"__section__:{section.section_id}",
                    title=section.title,
                    webpage_url="",
                    source_site=playlist.source_site,
                    uploader=f"专辑 · {len(section.entries)} 集",
                    thumbnail=section.thumbnail,
                    position=section.position,
                )
                for section in playlist.sections
            ],
            section_rows=True,
        )

    def _render_entries(self, entries: list[PlaylistEntry], *, section_rows: bool = False) -> None:
        playlist = self._playlist
        if playlist is None:
            return
        self._display_indices = []
        for entry in entries:
            try:
                self._display_indices.append(next(i for i, item in enumerate(playlist.entries) if item is entry or item.video_id == entry.video_id))
            except StopIteration:
                self._display_indices.append(-1)
        self._selected_rows = set()
        self._active_row = -1
        self.list_widget.blockSignals(True)
        try:
            self.list_widget.clear()
            for row_index, entry in enumerate(entries):
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, row_index)
                item.setData(Qt.ItemDataRole.UserRole + 1, section_rows)
                item.setSizeHint(self._item_size_hint())
                self.list_widget.addItem(item)
                self.list_widget.setItemWidget(item, PlaylistItemWidget(entry, row_index, self._network, self._thumbnail_cache, self.list_widget))
        finally:
            self.list_widget.blockSignals(False)
        self._update_button_state()
        self._schedule_visible_thumbnail_load()

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
            row = int(item.data(Qt.ItemDataRole.UserRole))
            index = self._display_indices[row] if 0 <= row < len(self._display_indices) else -1
            if 0 <= index < len(playlist.entries):
                result.append(playlist.entries[index])
        return result

    def _handle_saved_selection_changed(self, _index: int) -> None:
        """切换已保存列表即加载，不再需要"加载"按钮。"""
        self._update_button_state()
        if self._suppress_saved_auto_load:
            return
        key = self.current_saved_key().strip()
        # 占位项（key 为空）不触发；重复选中同一项也不重复加载。
        if not key or key == self._loaded_saved_key:
            return
        self._loaded_saved_key = key
        self.load_saved_requested.emit(key)

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
        selecting_section = bool(
            has_selection
            and self.list_widget.selectedItems()[0].data(Qt.ItemDataRole.UserRole + 1)
        )
        self.play_selected_button.setEnabled(has_selection)
        self.download_selected_button.setEnabled(has_selection and not selecting_section)
        self.download_all_button.setEnabled(has_playlist)
        self.cancel_button.setEnabled(has_available or self._open)
        self.save_button.setEnabled(has_playlist)
        self.delete_button.setEnabled(bool(self.current_saved_key()))

    def _item_size_hint(self) -> QSize:
        return QSize(max(MIN_PANEL_WIDTH, self.width()) - 28, ITEM_HEIGHT)

    def _resize_item_hints(self) -> None:
        """面板宽度变了要同步条目 sizeHint，否则窄窗口下条目横向溢出被裁掉。"""
        hint = self._item_size_hint()
        for row in range(self.list_widget.count()):
            self.list_widget.item(row).setSizeHint(hint)

    def _update_empty_state_text(self) -> None:
        count = len(self._saved_playlists)
        if count > 0:
            self.meta_label.setText(f"已保存 {count} 个列表，从下拉框选择即可加载")
        else:
            self.meta_label.setText(self._empty_text)

    def _sync_selection_visuals(self) -> None:
        # selectedIndexes 只返回选中项，配合上一轮的集合求对称差，
        # 就只需要刷新真正发生变化的行（最终视觉状态与逐行遍历一致）。
        current = {index.row() for index in self.list_widget.selectedIndexes()}
        for row in current ^ self._selected_rows:
            widget = self._row_widget(row)
            if widget is not None:
                widget.set_selected(row in current)
        self._selected_rows = current
