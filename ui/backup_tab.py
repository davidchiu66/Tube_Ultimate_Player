from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services.backup_targets import BackupTargetStore
from services.webdav_client import RemoteBackup, WebdavAccount
from ui.dialogs import BackupPickerDialog, WebdavAccountDialog


class BackupTab(QWidget):
    test_requested = Signal(object)
    backup_requested = Signal(object, bool)
    restore_list_requested = Signal(object)
    restore_requested = Signal(object, str)

    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.store = BackupTargetStore(
            config.user_path.parent / "backup_targets.json",
            config.user_path.parent / ".backup_key",
        )
        self._accounts: list[WebdavAccount] = []
        self._pending_account_dialog: WebdavAccountDialog | None = None

        self.table = QTableWidget(0, 4)
        self.table.setObjectName("LibraryTable")
        self.table.setHorizontalHeaderLabels(["名称", "服务器地址", "用户名", "远程目录"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)

        self.add_button = QPushButton("新增")
        self.edit_button = QPushButton("编辑")
        self.delete_button = QPushButton("删除")
        self.test_button = QPushButton("测试连接")
        self.add_button.clicked.connect(self._add_account)
        self.edit_button.clicked.connect(self._edit_account)
        self.delete_button.clicked.connect(self._delete_account)
        self.test_button.clicked.connect(self._test_selected)
        account_actions = QHBoxLayout()
        for button in (self.add_button, self.edit_button, self.delete_button, self.test_button):
            account_actions.addWidget(button)
        account_actions.addStretch(1)

        servers = QGroupBox("WebDAV 服务器")
        servers_layout = QVBoxLayout(servers)
        servers_layout.addWidget(self.table)
        servers_layout.addLayout(account_actions)

        content_label = QLabel("配置、播放历史、收藏、播放列表、下载任务")
        content_label.setObjectName("MetaLabel")
        self.include_cookies = QCheckBox("包含 Cookie（含站点登录凭据，上传前请确认网盘可信）")
        self.include_cookies.setStyleSheet("QCheckBox { color: #ef6c6c; }")

        self.backup_button = QPushButton("立即备份")
        self.restore_button = QPushButton("从备份恢复")
        self.backup_button.clicked.connect(self._backup)
        self.restore_button.clicked.connect(self._restore_list)
        operations = QHBoxLayout()
        operations.addWidget(self.backup_button)
        operations.addWidget(self.restore_button)
        operations.addStretch(1)

        self.status_label = QLabel()
        self.status_label.setObjectName("MetaLabel")
        self.progress_label = QLabel()
        self.progress_label.setObjectName("MetaLabel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(servers, 1)
        layout.addWidget(QLabel("备份内容"))
        layout.addWidget(content_label)
        layout.addWidget(self.include_cookies)
        layout.addLayout(operations)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_label)
        self.reload()

    def reload(self) -> None:
        self._accounts = self.store.accounts()
        active_id = self.store.active_id()
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._accounts))
        selected_row = -1
        for row, account in enumerate(self._accounts):
            values = (account.name, account.base_url, account.username, account.remote_dir)
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
            if account.account_id == active_id:
                selected_row = row
        self.table.blockSignals(False)
        if selected_row < 0 and self._accounts:
            selected_row = 0
        if selected_row >= 0:
            self.table.selectRow(selected_row)
            self.store.set_active(self._accounts[selected_row].account_id)
        self.include_cookies.setChecked(bool(self.config.get("backup.include_cookies", False)))
        self.refresh_status()
        account = self.selected_account()
        self.progress_label.setText(
            self.store.credential_warning(account.account_id) if account is not None else ""
        )
        self._update_buttons()

    def refresh_status(self) -> None:
        name = str(self.config.get("backup.last_backup_name", "") or "")
        raw_time = str(self.config.get("backup.last_backup_at", "") or "")
        if name:
            display_time = raw_time
            try:
                display_time = datetime.fromisoformat(raw_time).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass
            self.status_label.setText(f"上次备份 {display_time} · {name}")
        else:
            self.status_label.setText("尚未创建备份")

    def selected_account(self) -> WebdavAccount | None:
        row = self.table.currentRow()
        return self._accounts[row] if 0 <= row < len(self._accounts) else None

    def set_busy(self, busy: bool, text: str = "") -> None:
        for button in (self.add_button, self.edit_button, self.delete_button, self.test_button, self.backup_button, self.restore_button):
            button.setEnabled(not busy)
        self.table.setEnabled(not busy)
        if text:
            self.progress_label.setText(text)
        if not busy:
            self._update_buttons()

    def set_progress(self, text: str) -> None:
        self.progress_label.setText(text)

    def report_result(self, ok: bool, message: str) -> None:
        self.progress_label.setText(message)
        if self._pending_account_dialog is not None:
            self._pending_account_dialog.set_test_result(ok, message)

    def show_backups(self, backups: list[RemoteBackup]) -> None:
        if not backups:
            QMessageBox.information(self, "恢复备份", "该 WebDAV 上还没有备份包。")
            return
        dialog = BackupPickerDialog(backups, self)
        if not dialog.exec():
            return
        remote_name = dialog.selected_name()
        if not remote_name:
            return
        answer = QMessageBox.question(
            self,
            "确认恢复",
            "恢复将覆盖当前配置与数据，覆盖前会在本地留一份快照。\n\n确定继续吗？",
        )
        if answer == QMessageBox.StandardButton.Yes:
            account = self.selected_account()
            if account is not None:
                self.restore_requested.emit(account, remote_name)

    def _selection_changed(self) -> None:
        account = self.selected_account()
        if account is not None:
            self.store.set_active(account.account_id)
            self.progress_label.setText(self.store.credential_warning(account.account_id))
        else:
            self.progress_label.clear()
        self._update_buttons()

    def _update_buttons(self) -> None:
        selected = self.selected_account() is not None
        for button in (self.edit_button, self.delete_button, self.test_button, self.backup_button, self.restore_button):
            button.setEnabled(selected)

    def _add_account(self) -> None:
        self._open_account_dialog(None)

    def _edit_account(self) -> None:
        account = self.selected_account()
        if account is not None:
            self._open_account_dialog(account)

    def _open_account_dialog(self, account: WebdavAccount | None) -> None:
        dialog = WebdavAccountDialog(account, self)
        dialog.test_requested.connect(self._test_dialog_account)
        self._pending_account_dialog = dialog
        try:
            if dialog.exec():
                saved = self.store.save_account(dialog.account())
                self.store.set_active(saved.account_id)
                self.reload()
        finally:
            self._pending_account_dialog = None

    def _delete_account(self) -> None:
        account = self.selected_account()
        if account is None:
            return
        answer = QMessageBox.question(self, "删除 WebDAV", f"确定删除“{account.name}”吗？")
        if answer == QMessageBox.StandardButton.Yes:
            self.store.delete(account.account_id)
            self.reload()

    def _test_dialog_account(self, account: WebdavAccount) -> None:
        self.test_requested.emit(account)

    def _test_selected(self) -> None:
        account = self.selected_account()
        if account is not None:
            self.test_requested.emit(account)

    def _backup(self) -> None:
        account = self.selected_account()
        if account is not None:
            self.backup_requested.emit(account, self.include_cookies.isChecked())

    def _restore_list(self) -> None:
        account = self.selected_account()
        if account is not None:
            self.restore_list_requested.emit(account)
