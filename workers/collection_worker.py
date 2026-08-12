from __future__ import annotations

import logging
import traceback

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from resolver.models import VideoInfo
from resolver.site_resolver import SiteResolver


logger = logging.getLogger("tube_player.worker")


class CollectionWorkerSignals(QObject):
    # 信号自带 generation + video_id：切集比探测快得多，主窗口靠这两个值丢弃过期结果。
    success = Signal(int, str, object)
    error = Signal(int, str, str)
    finished = Signal(int, str)


class CollectionWorker(QRunnable):
    """探测当前视频所属的合集。

    结果为 None 属于正常情况（普通单集稿件不属于任何合集），不算失败。
    """

    def __init__(self, resolver: SiteResolver, video: VideoInfo, generation: int) -> None:
        super().__init__()
        self.resolver = resolver
        self.video = video
        self.generation = generation
        self.signals = CollectionWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            logger.info(
                "collection worker started site=%s video=%s",
                self.video.source_site,
                self.video.video_id,
            )
            playlist = self.resolver.resolve_collection_playlist(self.video)
            self.signals.success.emit(self.generation, self.video.video_id, playlist)
        except Exception as exc:
            detail = str(exc).strip() or traceback.format_exc()
            logger.exception("collection worker failed video=%s", self.video.video_id)
            self.signals.error.emit(self.generation, self.video.video_id, detail)
        finally:
            self.signals.finished.emit(self.generation, self.video.video_id)
