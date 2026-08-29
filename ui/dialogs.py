from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app_paths import APP_NAME, asset_path, read_app_version
from services.update_service import REPO_URL
from services.webdav_client import DEFAULT_REMOTE_DIR, RemoteBackup, WebdavAccount


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
            "功能:\n- YouTube / Bilibili / 抖音 / TikTok 首页与搜索\n- URL 解析播放\n- 多清晰度播放\n- 下载 / 收藏 / 历史"
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


class WebdavAccountDialog(QDialog):
    test_requested = Signal(object)

    def __init__(self, account: WebdavAccount | None = None, parent=None) -> None:
        super().__init__(parent)
        self._account_id = account.account_id if account is not None else ""
        self.setWindowTitle("编辑 WebDAV" if account is not None else "新增 WebDAV")
        self.setMinimumWidth(520)
        self.name_edit = QLineEdit(account.name if account else "")
        self.url_edit = QLineEdit(account.base_url if account else "")
        self.username_edit = QLineEdit(account.username if account else "")
        self.password_edit = QLineEdit(account.password if account else "")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.remote_dir_edit = QLineEdit(account.remote_dir if account else DEFAULT_REMOTE_DIR)
        self.test_label = QLabel()
        self.test_label.setObjectName("MetaLabel")
        self.test_label.setWordWrap(True)
        self.test_button = QPushButton("测试连接")
        self.test_button.clicked.connect(self._test)

        form = QFormLayout()
        form.addRow("名称", self.name_edit)
        form.addRow("服务器地址", self.url_edit)
        form.addRow("用户名", self.username_edit)
        form.addRow("密码", self.password_edit)
        form.addRow("远程目录", self.remote_dir_edit)
        warning = QLabel("使用 http:// 时账号和备份内容会以明文传输。")
        warning.setObjectName("MetaLabel")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        actions = QHBoxLayout()
        actions.addWidget(self.test_button)
        actions.addStretch(1)
        actions.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(warning)
        layout.addWidget(self.test_label)
        layout.addLayout(actions)

    def account(self) -> WebdavAccount:
        return WebdavAccount(
            account_id=self._account_id,
            name=self.name_edit.text().strip(),
            base_url=self.url_edit.text().strip(),
            username=self.username_edit.text().strip(),
            password=self.password_edit.text(),
            remote_dir=self.remote_dir_edit.text().strip() or DEFAULT_REMOTE_DIR,
        )

    def set_test_result(self, ok: bool, message: str) -> None:
        self.test_button.setEnabled(True)
        self.test_label.setText(message)
        self.test_label.setStyleSheet("color: #65c466;" if ok else "color: #ef6c6c;")

    def _test(self) -> None:
        if not self._valid():
            return
        self.test_button.setEnabled(False)
        self.test_label.setText("正在测试连接...")
        self.test_requested.emit(self.account())

    def _accept_if_valid(self) -> None:
        if self._valid():
            self.accept()

    def _valid(self) -> bool:
        account = self.account()
        if not account.name or not account.username or not account.password:
            QMessageBox.warning(self, "WebDAV 配置", "名称、用户名和密码不能为空。")
            return False
        if not account.base_url.startswith(("http://", "https://")):
            QMessageBox.warning(self, "WebDAV 配置", "服务器地址必须以 http:// 或 https:// 开头。")
            return False
        return True


class BackupPickerDialog(QDialog):
    def __init__(self, backups: list[RemoteBackup], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择要恢复的备份")
        self.resize(720, 420)
        self._backups = list(backups)
        self.table = QTableWidget(len(self._backups), 3)
        self.table.setHorizontalHeaderLabels(["备份文件", "时间", "大小"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, backup in enumerate(self._backups):
            self.table.setItem(row, 0, QTableWidgetItem(backup.name))
            self.table.setItem(row, 1, QTableWidgetItem(backup.modified_at or _stamp_from_name(backup.name)))
            self.table.setItem(row, 2, QTableWidgetItem(_format_size(backup.size)))
        if self._backups:
            self.table.selectRow(0)
        self.table.doubleClicked.connect(self.accept)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        restore_button = buttons.addButton("恢复", QDialogButtonBox.ButtonRole.AcceptRole)
        restore_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addWidget(buttons)

    def selected_name(self) -> str:
        row = self.table.currentRow()
        return self._backups[row].name if 0 <= row < len(self._backups) else ""


def _format_size(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return "0 B"


def _stamp_from_name(name: str) -> str:
    stem = Path(name).stem
    parts = stem.rsplit("-", 2)
    return " ".join(parts[-2:]) if len(parts) >= 3 else ""
