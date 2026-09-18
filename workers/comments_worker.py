from __future__ import annotations

import logging
import traceback

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from resolver.models import VideoInfo
from resolver.site_resolver import SiteResolver


logger = logging.getLogger("tube_player.worker")


class CommentsWorkerSignals(QObject):
    success = Signal(int, str, object, bool)
    error = Signal(int, str, str)
    finished = Signal(int, str)


class CommentsWorker(QRunnable):
    def __init__(self, resolver: SiteResolver, video: VideoInfo, generation: int, cursor: str = "") -> None:
        super().__init__()
        self.resolver = resolver
        self.video = video
        self.generation = generation
        self.cursor = cursor
        self.signals = CommentsWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            comments, next_cursor, has_more = self.resolver.fetch_comments(
                self.video, cursor=self.cursor, limit=20
            )
            self.signals.success.emit(
                self.generation,
                self.video.video_id,
                (comments, next_cursor),
                bool(has_more),
            )
        except Exception as exc:
            detail = str(exc).strip() or traceback.format_exc()
            logger.exception("comments worker failed video=%s", self.video.video_id)
            self.signals.error.emit(self.generation, self.video.video_id, detail)
        finally:
            self.signals.finished.emit(self.generation, self.video.video_id)
