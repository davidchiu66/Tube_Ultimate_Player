from __future__ import annotations

import logging
import os
import time
import traceback
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from services.update_service import (
    TRUSTED_DOWNLOAD_HOSTS,
    UpdateService,
    ensure_trusted_download_url,
    verify_downloaded_file,
)


logger = logging.getLogger("tube_player.worker")


class UpdateDownloadWorkerSignals(QObject):
    started = Signal(str)
    progress = Signal(int, int, float, str)
    success = Signal(str)
    error = Signal(str)
    finished = Signal()


class UpdateDownloadWorker(QRunnable):
    def __init__(
        self,
        service: UpdateService,
        url: str,
        target_path: Path,
        label: str,
        *,
        expected_size: int = 0,
        expected_sha256: str = "",
        expected_sha256_resolver: Callable[[], str] | None = None,
        trusted_hosts: tuple[str, ...] = TRUSTED_DOWNLOAD_HOSTS,
        verify_signature: bool = False,
    ) -> None:
        super().__init__()
        self.service = service
        self.url = url
        self.target_path = Path(target_path)
        self.label = label
        self.expected_size = max(0, int(expected_size or 0))
        self.expected_sha256 = str(expected_sha256 or "")
        self.expected_sha256_resolver = expected_sha256_resolver
        self.trusted_hosts = tuple(trusted_hosts or ())
        self.verify_signature = bool(verify_signature)
        self.signals = UpdateDownloadWorkerSignals()

    @Slot()
    def run(self) -> None:
        temp_path = self.target_path.with_suffix(self.target_path.suffix + ".part")
        self.signals.started.emit(self.label)
        try:
            logger.info("download worker started label=%s url=%s target=%s", self.label, self.url, self.target_path)
            if self.trusted_hosts:
                ensure_trusted_download_url(self.url, self.trusted_hosts)
            self.target_path.parent.mkdir(parents=True, exist_ok=True)
            if temp_path.exists():
                temp_path.unlink()

            downloaded = 0
            started = time.perf_counter()
            with self.service.open_url(self.url) as response, temp_path.open("wb") as file:
                if self.trusted_hosts:
                    # 重定向后的最终地址同样必须落在受信任域名内。
                    ensure_trusted_download_url(response.geturl(), self.trusted_hosts)
                total = int(response.headers.get("Content-Length", "0") or 0)
                while True:
                    chunk = response.read(1024 * 128)
                    if not chunk:
                        break
                    file.write(chunk)
                    downloaded += len(chunk)
                    elapsed = max(0.001, time.perf_counter() - started)
                    speed_text = _format_speed(downloaded / elapsed)
                    percent = downloaded * 100 / total if total > 0 else 0.0
                    self.signals.progress.emit(downloaded, total, percent, speed_text)

            verify_downloaded_file(
                temp_path,
                expected_size=self.expected_size,
                expected_sha256=self._expected_hash(),
            )
            if self.verify_signature and not self._expected_hash():
                self.service.verify_authenticode(temp_path)

            os.replace(temp_path, self.target_path)
            logger.info(
                "download worker completed label=%s target=%s size=%s",
                self.label,
                self.target_path,
                self.target_path.stat().st_size if self.target_path.exists() else 0,
            )
            self.signals.success.emit(str(self.target_path))
        except Exception as exc:
            logger.exception("download worker failed label=%s target=%s", self.label, self.target_path)
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            detail = str(exc).strip() or traceback.format_exc()
            self.signals.error.emit(detail)
        finally:
            self.signals.finished.emit()

    def _expected_hash(self) -> str:
        """惰性解析期望哈希（可能涉及网络请求，只在工作线程内执行一次）。"""
        if not self.expected_sha256 and self.expected_sha256_resolver is not None:
            resolver = self.expected_sha256_resolver
            self.expected_sha256_resolver = None
            try:
                self.expected_sha256 = str(resolver() or "")
            except Exception:
                logger.exception("解析升级包期望哈希失败 label=%s", self.label)
                self.expected_sha256 = ""
        return self.expected_sha256


def _format_speed(bytes_per_second: float) -> str:
    value = float(max(0.0, bytes_per_second))
    units = ("B/s", "KB/s", "MB/s", "GB/s")
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    return f"{value:.1f} {units[unit_index]}"
