from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class Toast(QFrame):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setVisible(False)
        self.label = QLabel()
        self.label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.addWidget(self.label)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, message: str, timeout_ms: int = 3000) -> None:
        self.label.setText(message)
        self.adjustSize()
        parent = self.parentWidget()
        if parent:
            width = min(max(self.sizeHint().width(), 260), 420)
            # 宽度被夹到上限后必须按新宽度重算高度：字幕限流那种两三行的处置建议
            # 如果沿用 sizeHint 的"一行"高度，后面几行会被直接裁掉。
            height = self.heightForWidth(width)
            self.resize(width, height if height > 0 else self.sizeHint().height())
            x = max(12, parent.width() - self.width() - 24)
            y = max(12, parent.height() - self.height() - 24)
            self.move(x, y)
        self.show()
        self.raise_()
        self._timer.start(timeout_ms)

