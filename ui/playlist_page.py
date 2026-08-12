from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
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

from resolver.models import PlaylistEntry, PlaylistInfo, SavedPlaylist
from resolver.source_utils import source_site_label
from ui.player_page import format_seconds
from ui.text_elision import format_upload_date
from ui.widgets import NoScrollComboBox


class PlaylistPage(QWidget):
    back_requested = Signal()
    play_entry_requested = Signal(object, int)
    download_entries_requested = Signal(object)
    save_requested = Signal()
    load_saved_requested = Signal(str)
    delete_saved_requested = Signal(str)
    auto_play_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._playlist: PlaylistInfo | None = None
        self._current_index = -1
        self._saved_playlists: list[SavedPlaylist] = []
        # 程序化重填下拉框时禁止自动加载。blockSignals 只挡得住信号，挡不住重填过程中
        # 对 _update_button_state 的直接调用链，所以另外用计数器做第二道保险。
        self._suppress_saved_auto_load = 0
        self._loaded_saved_key = ""

        self.title_label = QLabel("播放列表")
        self.title_label.setObjectName("PageTitle")
        self.meta_label = QLabel("当前没有打开的播放列表，可从上方选择已保存列表。")
        self.meta_label.setObjectName("MetaLabel")
        self.description_label = QLabel("双击列表项即可开始播放；支持多选后批量下载，播放中条目会以 ▶ 标记。")
        self.description_label.setObjectName("MetaLabel")
        self.description_label.setWordWrap(True)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索标题、来源、作者或链接")
        self.search_edit.setClearButtonEnabled(True)

        self.saved_combo = NoScrollComboBox()
        self.saved_combo.addItem("选择已保存列表", "")
        self.delete_saved_button = QPushButton("删除")

        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)
        self.loading_bar.hide()

        self.back_button = QPushButton("返回播放器")
        self.play_selected_button = QPushButton("播放选中")
        self.play_all_button = QPushButton("从头播放")
        self.download_selected_button = QPushButton("下载选中")
        self.download_all_button = QPushButton("下载全部")
        self.save_button = QPushButton("保存列表")
        self.select_all_button = QPushButton("全选")
        self.auto_play_checkbox = QCheckBox("自动连播")

        self.table = QTableWidget(0, 8)
        self.table.setObjectName("LibraryTable")
        self.table.setHorizontalHeaderLabels(["标题", "来源", "作者", "时长", "更新时间", "序号", "状态", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 140)
        self.table.setColumnWidth(3, 90)
        self.table.setColumnWidth(4, 110)
        self.table.setColumnWidth(5, 70)
        self.table.setColumnWidth(6, 90)
        self.table.setColumnWidth(7, 150)
        self.table.verticalHeader().setDefaultSectionSize(40)

        header_row = QHBoxLayout()
        header_row.addWidget(self.title_label)
        header_row.addStretch()
        header_row.addWidget(self.back_button)

        saved_row = QHBoxLayout()
        saved_row.setSpacing(8)
        saved_row.addWidget(QLabel("已保存列表"))
        saved_row.addWidget(self.saved_combo, 1)
        saved_row.addWidget(self.delete_saved_button)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_row.addWidget(self.play_selected_button)
        action_row.addWidget(self.play_all_button)
        action_row.addWidget(self.download_selected_button)
        action_row.addWidget(self.download_all_button)
        action_row.addWidget(self.save_button)
        action_row.addWidget(self.select_all_button)
        action_row.addStretch(1)
        action_row.addWidget(self.auto_play_checkbox)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addLayout(header_row)
        layout.addWidget(self.meta_label)
        layout.addWidget(self.description_label)
        layout.addLayout(saved_row)
        layout.addWidget(self.search_edit)
        layout.addWidget(self.loading_bar)
        layout.addLayout(action_row)
        layout.addWidget(self.table, 1)

        self.back_button.clicked.connect(self.back_requested)
        self.delete_saved_button.clicked.connect(self._delete_saved)
        self.play_selected_button.clicked.connect(self._play_selected)
        self.play_all_button.clicked.connect(lambda: self._emit_play_index(0))
        self.download_selected_button.clicked.connect(self._download_selected)
        self.download_all_button.clicked.connect(self._download_all)
        self.save_button.clicked.connect(self.save_requested)
        self.select_all_button.clicked.connect(self.table.selectAll)
        self.auto_play_checkbox.toggled.connect(self.auto_play_changed)
        self.search_edit.textChanged.connect(self._apply_filter)
        self.saved_combo.currentIndexChanged.connect(self._handle_saved_selection_changed)
        self.table.itemSelectionChanged.connect(self._update_button_state)
        self.table.cellDoubleClicked.connect(lambda row, _column: self._emit_play_index(row))

        self._update_button_state()

    def set_loading(self, loading: bool, message: str = "") -> None:
        self.loading_bar.setVisible(loading)
        if loading:
            self.title_label.setText("播放列表")
            self.meta_label.setText(message or "正在加载播放列表，请稍候...")
            self.table.setRowCount(0)
        else:
            self._update_empty_state_text()
        self._update_button_state()

    def set_playlist(self, playlist: PlaylistInfo, current_index: int = -1, auto_play_next: bool = False) -> None:
        self._playlist = playlist
        self._current_index = current_index
        self.title_label.setText(playlist.title or "播放列表")
        self.meta_label.setText(
            f"{source_site_label(playlist.source_site, playlist.webpage_url)} | "
            f"{playlist.uploader or '未知作者'} | 共 {len(playlist.entries)} 条"
        )
        self.auto_play_checkbox.blockSignals(True)
        self.auto_play_checkbox.setChecked(auto_play_next)
        self.auto_play_checkbox.blockSignals(False)

        self.table.setRowCount(0)
        for index, entry in enumerate(playlist.entries):
            self._add_entry_row(index, entry)

        self._refresh_current_highlight()
        self._apply_filter()
        self._update_button_state()

    def clear_playlist(self) -> None:
        self._playlist = None
        self._current_index = -1
        self.title_label.setText("播放列表")
        self.table.setRowCount(0)
        self._update_empty_state_text()
        self._update_button_state()

    def set_saved_playlists(self, playlists: list[SavedPlaylist], current_key: str = "") -> None:
        self._saved_playlists = list(playlists)
        self._suppress_saved_auto_load += 1
        self.saved_combo.blockSignals(True)
        try:
            self.saved_combo.clear()
            self.saved_combo.addItem("选择已保存列表", "")
            selected_index = 0
            for index, playlist in enumerate(self._saved_playlists, start=1):
                self.saved_combo.addItem(
                    f"{playlist.name}（{len(playlist.entries)} 条）" if playlist.entries else playlist.name,
                    playlist.playlist_key,
                )
                if playlist.playlist_key == current_key:
                    selected_index = index
            self.saved_combo.setCurrentIndex(selected_index)
        finally:
            self.saved_combo.blockSignals(False)
            self._suppress_saved_auto_load -= 1
        # 记住主窗口刚加载完的那一项，避免用户手工再选一次时重复加载。
        self._loaded_saved_key = str(current_key or "")
        self._update_empty_state_text()
        self._update_button_state()

    def set_current_index(self, index: int) -> None:
        self._current_index = index
        self._refresh_current_highlight()

    def selected_entries(self) -> list[PlaylistEntry]:
        playlist = self._playlist
        if playlist is None:
            return []
        result: list[PlaylistEntry] = []
        for row in self._selected_rows():
            if 0 <= row < len(playlist.entries):
                result.append(playlist.entries[row])
        return result

    def _add_entry_row(self, row: int, entry: PlaylistEntry) -> None:
        self.table.insertRow(row)
        self.table.setRowHeight(row, 40)
        values = [
            entry.title or "未命名视频",
            source_site_label(entry.source_site, entry.webpage_url),
            entry.uploader or "未知作者",
            format_seconds(entry.duration or 0),
            format_upload_date(getattr(entry, "upload_date", "")) or "—",
            str(entry.position or row + 1),
            entry.availability or "可播放",
        ]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 0:
                item.setData(Qt.ItemDataRole.UserRole, row)
                item.setData(Qt.ItemDataRole.UserRole + 1, entry.webpage_url)
            if column in (1, 3, 4, 5, 6):
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, column, item)

        actions = QWidget()
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(4, 0, 4, 0)
        action_layout.setSpacing(4)
        play_button = QPushButton("播放")
        download_button = QPushButton("下载")
        for button in (play_button, download_button):
            button.setFixedHeight(28)
            button.setMinimumWidth(56)
            button.setObjectName("LibraryActionButton")
        play_button.clicked.connect(lambda _=False, index=row: self._emit_play_index(index))
        download_button.clicked.connect(lambda _=False, index=row: self._download_row(index))
        action_layout.addWidget(play_button)
        action_layout.addWidget(download_button)
        self.table.setCellWidget(row, 7, actions)

    def _play_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, "提示", "请先选择一个视频。")
            return
        self._emit_play_index(rows[0])

    def _emit_play_index(self, index: int) -> None:
        playlist = self._playlist
        if playlist is None or not (0 <= index < len(playlist.entries)):
            return
        self.play_entry_requested.emit(playlist, index)

    def _download_selected(self) -> None:
        entries = self.selected_entries()
        if entries:
            self.download_entries_requested.emit(entries)

    def _download_row(self, row: int) -> None:
        playlist = self._playlist
        if playlist is not None and 0 <= row < len(playlist.entries):
            self.download_entries_requested.emit([playlist.entries[row]])

    def _download_all(self) -> None:
        if self._playlist and self._playlist.entries:
            self.download_entries_requested.emit(list(self._playlist.entries))

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

    def current_saved_key(self) -> str:
        return str(self.saved_combo.currentData() or "")

    def _selected_rows(self) -> list[int]:
        return sorted({index.row() for index in self.table.selectionModel().selectedRows()})

    def _refresh_current_highlight(self) -> None:
        for row in range(self.table.rowCount()):
            title_item = self.table.item(row, 0)
            if title_item is None:
                continue
            playlist = self._playlist
            base_text = playlist.entries[row].title if playlist and row < len(playlist.entries) else title_item.text()
            title_item.setText(("▶ " if row == self._current_index else "") + (base_text or "未命名视频"))
            if row == self._current_index:
                self.table.selectRow(row)
                self.table.scrollToItem(title_item)

    def _apply_filter(self, _text: str = "") -> None:
        query = self.search_edit.text().strip().casefold()
        playlist = self._playlist
        for row in range(self.table.rowCount()):
            entry = playlist.entries[row] if playlist and row < len(playlist.entries) else None
            haystack = " ".join(
                (
                    entry.title if entry else "",
                    source_site_label(entry.source_site, entry.webpage_url) if entry else "",
                    entry.uploader if entry else "",
                    entry.webpage_url if entry else "",
                )
            ).casefold()
            self.table.setRowHidden(row, bool(query and query not in haystack))

    def _update_button_state(self) -> None:
        has_playlist = self._playlist is not None and bool(self._playlist.entries)
        has_selection = bool(self._selected_rows())
        saved_key = self.current_saved_key()
        self.play_selected_button.setEnabled(has_selection)
        self.play_all_button.setEnabled(has_playlist)
        self.download_selected_button.setEnabled(has_selection)
        self.download_all_button.setEnabled(has_playlist)
        self.save_button.setEnabled(has_playlist)
        self.select_all_button.setEnabled(has_playlist)
        self.delete_saved_button.setEnabled(bool(saved_key))

    def _update_empty_state_text(self) -> None:
        if self._playlist is not None and self._playlist.entries:
            return
        saved_count = len(self._saved_playlists)
        if saved_count:
            self.meta_label.setText(f"已保存 {saved_count} 个播放列表，从上方下拉框选择即可加载。")
        else:
            self.meta_label.setText("当前没有打开或保存的播放列表。播放或解析列表后，可在这里查看和管理。")
