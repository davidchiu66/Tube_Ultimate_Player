from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from services.config_service import ConfigService


class StartupCookieGuideDialog(QDialog):
    open_settings_requested = Signal()

    def __init__(self, config: ConfigService, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("首次使用指南")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(620)
        self.setMaximumWidth(760)

        title = QLabel("先配置各视频网站的 Cookie")
        title.setObjectName("PageTitle")

        body = QLabel(
            "首次使用建议先为各视频网站配置 Cookie。\n\n"
            "推荐方法：安装 Firefox，并分别登录 Bilibili、YouTube、抖音、TikTok 和小红书，"
            "然后进入“设置”，使用“自动检测”获取各站点 Cookie。\n\n"
            "也可以使用浏览器扩展（例如 Get cookies.txt LOCALLY）在本机导出 cookies.txt，"
            "再进入“设置”，为每个视频网站分别导入并保存。\n\n"
            "Cookie 属于登录凭据，请只在本机导出和保存，不要上传到网页或第三方服务。"
        )
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.hide_checkbox = QCheckBox("下次不再显示")
        self.settings_button = QPushButton("前往设置")
        self.later_button = QPushButton("稍后")
        self.settings_button.setDefault(True)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addWidget(self.hide_checkbox)
        actions.addStretch(1)
        actions.addWidget(self.later_button)
        actions.addWidget(self.settings_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(body)
        layout.addLayout(actions)

        self.settings_button.clicked.connect(self._open_settings)
        self.later_button.clicked.connect(self.reject)

    def _open_settings(self) -> None:
        self.open_settings_requested.emit()
        self.accept()

    def done(self, result: int) -> None:
        if self.hide_checkbox.isChecked():
            self.config.set("ui.hide_startup_cookie_guide", True)
            self.config.save()
        super().done(result)
