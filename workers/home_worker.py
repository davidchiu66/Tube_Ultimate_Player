from __future__ import annotations

import logging
import traceback

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from resolver.youtube_resolver import YoutubeResolver


logger = logging.getLogger("tube_player.worker")


class HomeWorkerSignals(QObject):
    success = Signal(object, bool)
    error = Signal(str)
    finished = Signal()


class HomeWorker(QRunnable):
    def __init__(
        self,
        resolver: YoutubeResolver,
        page: int = 1,
        page_size: int = 56,
    ) -> None:
        super().__init__()
        self.resolver = resolver
        self.page = page
        self.page_size = page_size
        self.signals = HomeWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            logger.info("home worker started page=%s page_size=%s", self.page, self.page_size)
            videos, has_next = self.resolver.fetch_home_videos(self.page, self.page_size)
            logger.info(
                "home worker success page=%s count=%s has_next=%s",
                self.page,
                len(videos),
                has_next,
            )
            self.signals.success.emit(videos, has_next)
        except Exception as exc:
            detail = str(exc).strip() or traceback.format_exc()
            logger.exception("home worker failed")
            self.signals.error.emit(detail)
        finally:
            logger.info("home worker finished")
            self.signals.finished.emit()
