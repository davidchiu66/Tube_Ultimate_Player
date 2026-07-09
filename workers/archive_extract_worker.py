from __future__ import annotations

import logging
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


logger = logging.getLogger("tube_player.worker")


class ArchiveExtractWorkerSignals(QObject):
    success = Signal(str)
    error = Signal(str)
    finished = Signal()


class ArchiveExtractWorker(QRunnable):
    def __init__(self, archive_path: Path, extract_dir: Path) -> None:
        super().__init__()
        self.archive_path = Path(archive_path)
        self.extract_dir = Path(extract_dir)
        self.signals = ArchiveExtractWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            logger.info("archive extract started archive=%s target=%s", self.archive_path, self.extract_dir)
            self.extract_dir.mkdir(parents=True, exist_ok=True)
            try:
                import py7zr
            except ImportError as exc:
                raise RuntimeError("缺少 py7zr 依赖，无法解压 7z 压缩包。请先执行 pip install -r requirements.txt") from exc

            with py7zr.SevenZipFile(self.archive_path, mode="r") as archive:
                archive.extractall(path=self.extract_dir)

            logger.info("archive extract completed archive=%s target=%s", self.archive_path, self.extract_dir)
            self.signals.success.emit(str(self.extract_dir))
        except Exception as exc:
            logger.exception("archive extract failed archive=%s target=%s", self.archive_path, self.extract_dir)
            detail = str(exc).strip() or traceback.format_exc()
            self.signals.error.emit(detail)
        finally:
            self.signals.finished.emit()
