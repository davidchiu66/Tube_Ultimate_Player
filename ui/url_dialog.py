from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QLabel,
    QVBoxLayout,
)


class UrlPlayDialog(QDialog):
    def __init__(self, parent=None, initial_url: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("播放 URL")
        self.setModal(True)
        self.resize(520, 140)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        label = QLabel("请输入 YouTube 视频 URL")
        self.url_edit = QLineEdit(initial_url)
        self.url_edit.setPlaceholderText("https://www.youtube.com/watch?v=...")

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
        layout.addWidget(buttons)

        self.url_edit.returnPressed.connect(self.accept)
        self.url_edit.selectAll()
        self.url_edit.setFocus()

    def url(self) -> str:
        return self.url_edit.text().strip()
