from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from services.config_service import detect_browser_cookie_sources
from services.cookie_probe_service import discover_cookie_databases, probe_site_cookie_browsers


logger = logging.getLogger("tube_player.cookie")


class CookieProbeSignals(QObject):
    # 成功时携带 {site: browser_spec}；某站点没找到登录浏览器时不出现在字典里。
    success = Signal(object)
    error = Signal(str)
    finished = Signal()


class CookieProbeWorker(QRunnable):
    """启动时异步探测各站点的登录 Cookie 浏览器，不阻塞首屏。"""

    def __init__(self, sites: tuple[str, ...] = ("bilibili", "youtube")) -> None:
        super().__init__()
        self.sites = sites
        self.signals = CookieProbeSignals()

    @Slot()
    def run(self) -> None:
        try:
            sources = detect_browser_cookie_sources()
            databases = discover_cookie_databases(
                sources,
                home=Path.home(),
                environ=dict(os.environ),
                platform_name=sys.platform,
            )
            logger.info("cookie probe started browsers=%s", len(databases))
            result = probe_site_cookie_browsers(self.sites, databases)
            logger.info("cookie probe done result=%s", result)
            self.signals.success.emit(result)
        except Exception as exc:  # noqa: BLE001
            logger.exception("cookie probe worker failed")
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()
