from __future__ import annotations

import logging
import traceback

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from resolver.site_resolver import SiteResolver


logger = logging.getLogger("tube_player.worker")


class HomeWorkerSignals(QObject):
    success = Signal(object, bool)
    error = Signal(str)
    finished = Signal()


class HomeWorker(QRunnable):
    def __init__(
        self,
        resolver: SiteResolver,
        page: int = 1,
        page_size: int = 56,
        force_refresh: bool = False,
        source: str = "",
    ) -> None:
        super().__init__()
        self.resolver = resolver
        self.page = page
        self.page_size = page_size
        self.force_refresh = force_refresh
        # 站点在提交任务时就固定下来：用户随时可能在工具栏切换，
        # 让 worker 自己去读当前选择会拿到切换后的值，结果就对不上发起时的意图。
        self.source = source
        self.signals = HomeWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            logger.info(
                "home worker started page=%s page_size=%s source=%s",
                self.page,
                self.page_size,
                self.source or "default",
            )
            videos, has_next = self.resolver.fetch_home_videos(
                self.page,
                self.page_size,
                force_refresh=self.force_refresh,
                source=self.source,
            )
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
