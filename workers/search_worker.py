from __future__ import annotations

import logging
import traceback

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from resolver.youtube_resolver import YoutubeResolver


logger = logging.getLogger("tube_player.worker")


class SearchWorkerSignals(QObject):
    success = Signal(object, bool)
    error = Signal(str)
    finished = Signal()


class SearchWorker(QRunnable):
    def __init__(
        self,
        resolver: YoutubeResolver,
        keyword: str,
        page: int = 1,
        page_size: int = 45,
    ) -> None:
        super().__init__()
        self.resolver = resolver
        self.keyword = keyword
        self.page = page
        self.page_size = page_size
        self.signals = SearchWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            logger.info(
                "search worker started keyword=%s page=%s page_size=%s",
                self.keyword,
                self.page,
                self.page_size,
            )
            videos, has_next = self.resolver.search_videos(self.keyword, self.page, self.page_size)
            logger.info(
                "search worker success keyword=%s page=%s count=%s has_next=%s",
                self.keyword,
                self.page,
                len(videos),
                has_next,
            )
            self.signals.success.emit(videos, has_next)
        except Exception as exc:
            detail = str(exc).strip() or traceback.format_exc()
            logger.exception("search worker failed keyword=%s page=%s", self.keyword, self.page)
            self.signals.error.emit(detail)
        finally:
            logger.info("search worker finished keyword=%s page=%s", self.keyword, self.page)
            self.signals.finished.emit()
