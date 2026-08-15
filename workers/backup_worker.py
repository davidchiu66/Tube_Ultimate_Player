from __future__ import annotations

import logging
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from app_paths import RUNTIME_ROOT
from services.backup_service import (
    BackupVersionTooNew,
    build_backup_archive,
    prune_local_backups,
    prune_remote_backups,
    restore_backup_archive,
)
from services.config_service import ConfigService
from services.webdav_client import WebdavAccount, WebdavClient


logger = logging.getLogger("tube_player.backup")


class BackupWorkerSignals(QObject):
    success = Signal(object)
    error = Signal(str)
    finished = Signal()
    progress = Signal(str)


class _BackupWorker(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = BackupWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.success.emit(self.execute())
        except BackupVersionTooNew as exc:
            self.signals.success.emit({"needs_confirmation": True, "backup_version": exc.backup_version, "current_version": exc.current_version})
        except Exception as exc:  # noqa: BLE001
            logger.exception("backup worker failed")
            self.signals.error.emit(str(exc).strip() or traceback.format_exc())
        finally:
            self.signals.finished.emit()

    def execute(self):
        raise NotImplementedError


class WebdavTestWorker(_BackupWorker):
    def __init__(self, account: WebdavAccount, config: ConfigService) -> None:
        super().__init__()
        self.account = account
        self.config = config

    def execute(self) -> str:
        self.signals.progress.emit("正在测试 WebDAV 连接...")
        return WebdavClient(self.account, self.config).test_connection()


class BackupUploadWorker(_BackupWorker):
    def __init__(self, account: WebdavAccount, config: ConfigService, include_cookies: bool) -> None:
        super().__init__()
        self.account = account
        self.config = config
        self.include_cookies = include_cookies

    def execute(self) -> dict:
        self.signals.progress.emit("正在创建备份包...")
        archive = build_backup_archive(include_cookies=self.include_cookies, config=self.config)
        client = WebdavClient(self.account, self.config)
        client.ensure_dir()
        client.upload(archive, archive.name, lambda value: self.signals.progress.emit(f"正在上传 {value}%"))
        warnings: list[str] = []
        try:
            prune_remote_backups(client)
        except Exception as exc:  # noqa: BLE001
            logger.warning("remote backup pruning failed: %s", exc)
            warnings.append(f"远端旧备份清理失败：{exc}")
        prune_local_backups()
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        return {"name": archive.name, "created_at": now, "warnings": warnings}


class BackupListWorker(_BackupWorker):
    def __init__(self, account: WebdavAccount, config: ConfigService) -> None:
        super().__init__()
        self.account = account
        self.config = config

    def execute(self):
        self.signals.progress.emit("正在读取远端备份清单...")
        return WebdavClient(self.account, self.config).list_backups()


class BackupRestoreWorker(_BackupWorker):
    def __init__(self, account: WebdavAccount, config: ConfigService, remote_name: str, *, allow_newer: bool = False) -> None:
        super().__init__()
        self.account = account
        self.config = config
        self.remote_name = remote_name
        self.allow_newer = allow_newer

    def execute(self) -> dict:
        incoming = RUNTIME_ROOT / "backups" / f"incoming-{Path(self.remote_name).name}"
        client = WebdavClient(self.account, self.config)
        self.signals.progress.emit("正在下载备份...")
        client.download(self.remote_name, incoming, lambda value: self.signals.progress.emit(f"正在下载 {value}%"))
        try:
            self.signals.progress.emit("正在校验并恢复备份...")
            snapshot = restore_backup_archive(incoming, allow_newer=self.allow_newer)
            return {"restored": True, "snapshot": str(snapshot)}
        finally:
            incoming.unlink(missing_ok=True)
