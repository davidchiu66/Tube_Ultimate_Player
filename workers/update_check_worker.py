from __future__ import annotations

import logging
import traceback

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from services.update_service import UpdateCheckResult, UpdateService


logger = logging.getLogger("tube_player.worker")


class UpdateCheckWorkerSignals(QObject):
    success = Signal(object)
    error = Signal(str)
    finished = Signal()


class UpdateCheckWorker(QRunnable):
    def __init__(self, service: UpdateService) -> None:
        super().__init__()
        self.service = service
        self.signals = UpdateCheckWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            logger.info("update check worker started")
            result: UpdateCheckResult = self.service.check_for_updates()
            logger.info(
                "update check worker success current=%s latest=%s has_update=%s mode=%s",
                result.current_version,
                result.latest_version,
                result.has_update,
                result.install_mode,
            )
            self.signals.success.emit(result)
        except Exception as exc:
            detail = str(exc).strip() or traceback.format_exc()
            logger.exception("update check worker failed")
            self.signals.error.emit(detail)
        finally:
            self.signals.finished.emit()
