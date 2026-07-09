from __future__ import annotations

import logging
import traceback

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from resolver.youtube_resolver import YoutubeResolver


logger = logging.getLogger("tube_player.worker")


class PlaylistWorkerSignals(QObject):
    success = Signal(object)
    error = Signal(str)
    finished = Signal()


class PlaylistWorker(QRunnable):
    def __init__(self, resolver: YoutubeResolver, url: str) -> None:
        super().__init__()
        self.resolver = resolver
        self.url = url
        self.signals = PlaylistWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            logger.info("playlist worker started url=%s", self.url)
            playlist = self.resolver.resolve_playlist(self.url)
            logger.info("playlist worker success url=%s title=%s count=%s", self.url, playlist.title, len(playlist.entries))
            self.signals.success.emit(playlist)
        except Exception as exc:
            detail = str(exc).strip() or traceback.format_exc()
            logger.exception("playlist worker failed url=%s", self.url)
            self.signals.error.emit(detail)
        finally:
            logger.info("playlist worker finished url=%s", self.url)
            self.signals.finished.emit()
