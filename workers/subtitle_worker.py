from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from resolver.models import SubtitleInfo
from services.subtitle_service import materialize_subtitle


logger = logging.getLogger("tube_player.subtitle")


class SubtitleLoadSignals(QObject):
    # (请求序号, 字幕 key, 本地文件路径)
    success = Signal(int, str, str)
    # (请求序号, 字幕 key, 错误信息)
    error = Signal(int, str, str)
    finished = Signal(int)


class SubtitleLoadWorker(QRunnable):
    """下载/落盘字幕。字幕可能来自网络，绝不能在 UI 线程做。"""

    def __init__(
        self,
        request_id: int,
        key: str,
        subtitle: SubtitleInfo,
        video_id: str,
        *,
        proxy: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.key = key
        self.subtitle = subtitle
        self.video_id = video_id
        self.proxy = proxy
        self.headers = dict(headers or {})
        self.signals = SubtitleLoadSignals()

    @Slot()
    def run(self) -> None:
        try:
            path = materialize_subtitle(
                self.subtitle,
                self.video_id,
                proxy=self.proxy,
                headers=self.headers,
            )
            self.signals.success.emit(self.request_id, self.key, str(path))
        except Exception as exc:  # noqa: BLE001 - 网络与磁盘异常都要转成可读提示
            logger.exception("subtitle load failed key=%s language=%s", self.key, self.subtitle.language)
            self.signals.error.emit(self.request_id, self.key, str(exc))
        finally:
            self.signals.finished.emit(self.request_id)
