from __future__ import annotations

import logging
import traceback

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from resolver.models import VideoInfo
from resolver.site_resolver import SiteResolver


logger = logging.getLogger("tube_player.worker")


class CreatorVideosWorkerSignals(QObject):
    success = Signal(int, str, object)
    error = Signal(int, str, str)
    finished = Signal(int, str)


class CreatorVideosWorker(QRunnable):
    def __init__(
        self,
        resolver: SiteResolver,
        video: VideoInfo,
        generation: int,
        limit: int = 50,
    ) -> None:
        super().__init__()
        self.resolver = resolver
        self.video = video
        self.generation = generation
        self.limit = limit
        self.signals = CreatorVideosWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            logger.info(
                "creator videos worker started site=%s creator=%s video=%s",
                self.video.source_site,
                self.video.creator_id or self.video.channel_id,
                self.video.video_id,
            )
            playlist = self.resolver.resolve_creator_playlist(self.video, self.limit)
            self.signals.success.emit(self.generation, self.video.video_id, playlist)
        except Exception as exc:
            detail = str(exc).strip() or traceback.format_exc()
            logger.exception("creator videos worker failed video=%s", self.video.video_id)
            self.signals.error.emit(self.generation, self.video.video_id, detail)
        finally:
            self.signals.finished.emit(self.generation, self.video.video_id)
