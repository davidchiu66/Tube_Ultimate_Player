from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app_paths import APP_NAME, asset_path


class AboutPage(QWidget):
    check_update_requested = Signal()
    upgrade_requested = Signal()
    open_repo_requested = Signal()
    open_update_folder_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._upgrade_ready = False

        title = QLabel("关于")
        title.setObjectName("PageTitle")

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(96, 96)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._load_icon()

        self.name_label = QLabel(APP_NAME)
        self.name_label.setObjectName("TitleLabel")
        self.version_label = QLabel("当前版本：-")
        self.latest_label = QLabel("最新版本：-")
        self.mode_label = QLabel("运行形态：-")
        self.status_label = QLabel("可在这里检测新版本并查看发布说明。")
        self.status_label.setObjectName("MetaLabel")

        self.description_label = QLabel(
            "基于 PySide6、yt-dlp 与 libmpv 的桌面 YouTube 播放器，支持首页浏览、搜索、收藏、历史与下载。"
        )
        self.description_label.setWordWrap(True)

        self.repo_button = QPushButton("GitHub")
        self.check_button = QPushButton("检测版本")
        self.upgrade_button = QPushButton("在线升级")
        self.open_folder_button = QPushButton("打开升级目录")
        self.upgrade_button.setEnabled(False)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addWidget(self.repo_button)
        actions.addWidget(self.check_button)
        actions.addWidget(self.upgrade_button)
        actions.addWidget(self.open_folder_button)
        actions.addStretch(1)

        self.progress_label = QLabel("")
        self.progress_label.setObjectName("MetaLabel")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.hide()

        self.release_notes = QTextEdit()
        self.release_notes.setReadOnly(True)
        self.release_notes.setPlaceholderText("检测到新版本后，这里会显示 Release Note。")

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(16)
        top.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(6)
        text_col.addWidget(title)
        text_col.addWidget(self.name_label)
        text_col.addWidget(self.version_label)
        text_col.addWidget(self.latest_label)
        text_col.addWidget(self.mode_label)
        text_col.addWidget(self.description_label)
        text_col.addWidget(self.status_label)
        text_col.addLayout(actions)
        text_col.addWidget(self.progress_label)
        text_col.addWidget(self.progress_bar)
        top.addLayout(text_col, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addLayout(top)
        layout.addWidget(self.release_notes, 1)

        self.repo_button.clicked.connect(self.open_repo_requested.emit)
        self.check_button.clicked.connect(self.check_update_requested.emit)
        self.upgrade_button.clicked.connect(self.upgrade_requested.emit)
        self.open_folder_button.clicked.connect(self.open_update_folder_requested.emit)

    def set_current_version(self, version: str) -> None:
        self.version_label.setText(f"当前版本：{version}")

    def set_install_mode(self, label: str) -> None:
        self.mode_label.setText(f"运行形态：{label}")

    def set_latest_version(self, version: str, published_at: str = "") -> None:
        text = f"最新版本：{version or '-'}"
        if published_at:
            text += f"  发布日期：{published_at[:10]}"
        self.latest_label.setText(text)

    def set_release_notes(self, notes: str) -> None:
        self.release_notes.setPlainText(notes.strip() or "该版本没有提供 Release Note。")

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_checking(self, checking: bool) -> None:
        self.check_button.setEnabled(not checking)
        if checking:
            self.set_status("正在检查 GitHub Releases，请稍候...")

    def set_upgrade_available(self, available: bool) -> None:
        self._upgrade_ready = available
        self.upgrade_button.setEnabled(available)

    def set_upgrade_progress(self, active: bool, text: str = "", percent: float = 0.0) -> None:
        self.progress_label.setText(text)
        self.progress_bar.setVisible(active)
        if active:
            if percent <= 0:
                self.progress_bar.setRange(0, 0)
            else:
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(int(max(0, min(100, percent))))
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(int(max(0, min(100, percent))))

    def _load_icon(self) -> None:
        for path in (
            asset_path("icons", "app-icon-about.png"),
            asset_path("icons", "app-icon-256.png"),
            asset_path("icons", "app-icon.png"),
        ):
            if not Path(path).exists():
                continue
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                self.icon_label.setPixmap(
                    pixmap.scaled(
                        self.icon_label.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                return
        self.icon_label.setText("ICON")
