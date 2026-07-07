from __future__ import annotations

import logging
import traceback

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from resolver.youtube_resolver import YoutubeResolver


logger = logging.getLogger("tube_player.worker")


class WorkerSignals(QObject):
    success = Signal(object)
    error = Signal(str)
    finished = Signal()


class ResolverWorker(QRunnable):
    def __init__(self, url: str, resolver: YoutubeResolver) -> None:
        super().__init__()
        self.url = url
        self.resolver = resolver
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            logger.info("resolver worker started url=%s", self.url)
            video_info = self.resolver.resolve(self.url)
            logger.info("resolver worker success url=%s title=%s", self.url, video_info.title)
            self.signals.success.emit(video_info)
        except Exception as exc:
            detail = str(exc).strip() or traceback.format_exc()
            logger.exception("resolver worker failed url=%s", self.url)
            self.signals.error.emit(detail)
        finally:
            logger.info("resolver worker finished url=%s", self.url)
            self.signals.finished.emit()
