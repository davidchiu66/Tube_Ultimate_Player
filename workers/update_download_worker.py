from __future__ import annotations

import logging
import os
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from services.update_service import UpdateService


logger = logging.getLogger("tube_player.worker")


class UpdateDownloadWorkerSignals(QObject):
    started = Signal(str)
    progress = Signal(int, int, float, str)
    success = Signal(str)
    error = Signal(str)
    finished = Signal()


class UpdateDownloadWorker(QRunnable):
    def __init__(self, service: UpdateService, url: str, target_path: Path, label: str) -> None:
        super().__init__()
        self.service = service
        self.url = url
        self.target_path = Path(target_path)
        self.label = label
        self.signals = UpdateDownloadWorkerSignals()

    @Slot()
    def run(self) -> None:
        temp_path = self.target_path.with_suffix(self.target_path.suffix + ".part")
        self.signals.started.emit(self.label)
        try:
            logger.info("download worker started label=%s url=%s target=%s", self.label, self.url, self.target_path)
            self.target_path.parent.mkdir(parents=True, exist_ok=True)
            if temp_path.exists():
                temp_path.unlink()

            downloaded = 0
            started = time.perf_counter()
            with self.service.open_url(self.url) as response, temp_path.open("wb") as file:
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
