from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app_paths import APP_NAME, asset_path, read_app_version
from services.update_service import REPO_URL


class AboutDialog(QDialog):
    version_center_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("关于")
        self.setFixedSize(450, 350)
        self._apply_icon()

        icon_label = QLabel()
        icon_label.setFixedSize(84, 84)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._load_pixmap(icon_label)

        name_label = QLabel(APP_NAME)
        name_label.setObjectName("AboutTitle")
        version_label = QLabel(f"版本: v{read_app_version()}")
        stack_label = QLabel("基于: PySide6 + libmpv + yt-dlp")
        features_label = QLabel(
            "功能:\n- YouTube / Bilibili 首页与搜索\n- URL 解析播放\n- 多清晰度播放\n- 下载 / 收藏 / 历史"
        )
        copyright_label = QLabel("Copyright: 2026")
        for label in (version_label, stack_label, features_label, copyright_label):
            label.setObjectName("MetaLabel")

        open_repo_button = QPushButton("GitHub")
        open_repo_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(REPO_URL)))
        version_center_button = QPushButton("版本中心")
        version_center_button.clicked.connect(self._open_version_center)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)

        info_column = QVBoxLayout()
        info_column.setContentsMargins(0, 0, 0, 0)
        info_column.setSpacing(8)
        info_column.addWidget(name_label)
        info_column.addWidget(version_label)
        info_column.addWidget(stack_label)
        info_column.addWidget(features_label)
        info_column.addStretch(1)
        info_column.addWidget(copyright_label)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(16)
        header.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)
        header.addLayout(info_column, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(open_repo_button)
        actions.addWidget(version_center_button)
        actions.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)
        layout.addLayout(header)
        layout.addStretch(1)
        layout.addLayout(actions)

    def _apply_icon(self) -> None:
        for path in (
            asset_path("icons", "app-icon.ico"),
            asset_path("icons", "app-icon-256.png"),
            asset_path("icons", "app-icon.png"),
        ):
            if path.exists():
                self.setWindowIcon(QIcon(str(path)))
                return

    def _load_pixmap(self, label: QLabel) -> None:
        for path in (
            asset_path("icons", "app-icon-about.png"),
            asset_path("icons", "app-icon-256.png"),
            asset_path("icons", "app-icon.png"),
        ):
            file_path = Path(path)
            if not file_path.exists():
                continue
            pixmap = QPixmap(str(file_path))
            if pixmap.isNull():
                continue
            label.setPixmap(
                pixmap.scaled(
                    label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            return
        label.setText("ABOUT")

    def _open_version_center(self) -> None:
        self.version_center_requested.emit()
        self.accept()
