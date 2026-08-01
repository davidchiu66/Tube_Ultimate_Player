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
    QVBoxLayout,
)

from resolver.models import SubtitleInfo


class SubtitlePickerDialog(QDialog):
    """完整字幕列表 + 搜索。

    YouTube 的自动字幕可以有近五千种（机翻到各种语言），全塞进下拉框没法用，
    所以下拉框只放常用的一小截，其余走这个带搜索的对话框。
    """

    def __init__(
        self,
        subtitles: dict[str, SubtitleInfo],
        parent=None,
        current_key: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择字幕")
        self.setModal(True)
        self.resize(560, 520)
        self._selected_key = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        manual = sum(1 for item in subtitles.values() if not item.is_auto)
        summary = QLabel(f"共 {len(subtitles)} 条字幕轨（手动 {manual} 条，自动 {len(subtitles) - manual} 条）")
        summary.setObjectName("MetaLabel")

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("按语言名或语言代码搜索，如 中文 / zh / English")
        self.search_edit.setClearButtonEnabled(True)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.itemDoubleClicked.connect(self._accept_item)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_button:
            ok_button.setText("使用该字幕")
        if cancel_button:
            cancel_button.setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addWidget(summary)
        layout.addWidget(self.search_edit)
        layout.addWidget(self.list_widget, 1)
        layout.addWidget(buttons)

        self._populate(subtitles, current_key)
        self.search_edit.textChanged.connect(self._apply_filter)
        self.search_edit.setFocus()

    def selected_key(self) -> str:
        item = self.list_widget.currentItem()
        if item is None or item.isHidden():
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _populate(self, subtitles: dict[str, SubtitleInfo], current_key: str) -> None:
        for key, subtitle in subtitles.items():
            item = QListWidgetItem(subtitle.label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            # 搜索同时匹配可读名与语言代码，中文用户搜「中文」、英文用户搜 zh 都能命中。
            item.setData(
                Qt.ItemDataRole.UserRole + 1,
                f"{subtitle.display_language} {subtitle.language} {subtitle.ext}".casefold(),
            )
            item.setToolTip(f"{subtitle.display_language} · {subtitle.language} · {subtitle.ext}")
            self.list_widget.addItem(item)
            if key == current_key:
                self.list_widget.setCurrentItem(item)

    def _apply_filter(self, text: str) -> None:
        query = text.strip().casefold()
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            haystack = str(item.data(Qt.ItemDataRole.UserRole + 1) or "")
            # Qt6 去掉了 QListWidget.setItemHidden，隐藏要在 item 上设。
            item.setHidden(bool(query and query not in haystack))

    def _accept_item(self, _item: QListWidgetItem) -> None:
        self.accept()
