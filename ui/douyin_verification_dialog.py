from __future__ import annotations

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class DouyinVerificationDialog(QDialog):
    continue_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, service, url: str, reason: str, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._url = str(url or "https://www.douyin.com/jingxuan")
        self._cancel_emitted = False
        self._detached = False

        self.setWindowTitle("抖音安全验证")
        self.resize(1100, 760)
        self.setMinimumSize(800, 560)

        self.status_label = QLabel(str(reason or "抖音需要完成安全验证"))
        self.status_label.setObjectName("MetaLabel")
        self.status_label.setWordWrap(True)

        self.web_view = QWebEngineView(self)

        self.refresh_button = QPushButton("刷新")
        self.external_button = QPushButton("在浏览器中打开")
        self.continue_button = QPushButton("验证完成，继续")
        self.cancel_button = QPushButton("取消")

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.external_button)
        actions.addStretch(1)
        actions.addWidget(self.continue_button)
        actions.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(self.status_label)
        layout.addWidget(self.web_view, 1)
        layout.addLayout(actions)

        self.refresh_button.clicked.connect(self._service.reload_verification_page)
        self.external_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self._url)))
        self.continue_button.clicked.connect(self._continue)
        self.cancel_button.clicked.connect(self.reject)
        self.rejected.connect(self._emit_cancel)

        self._service.attach_verification_view(self.web_view, self._url)

    def update_request(self, url: str, reason: str) -> None:
        self._url = str(url or self._url)
        self.status_label.setText(str(reason or "抖音需要完成安全验证"))

    def _continue(self) -> None:
        self.continue_requested.emit()
        self.accept()

    def _emit_cancel(self) -> None:
        if self._cancel_emitted:
            return
        self._cancel_emitted = True
        self.cancel_requested.emit()

    def done(self, result: int) -> None:
        try:
            if not self._detached:
                self._detached = True
                self._service.detach_verification_view(self.web_view)
        finally:
            super().done(result)
