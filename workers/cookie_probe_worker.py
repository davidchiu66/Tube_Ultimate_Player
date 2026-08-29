from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from services.config_service import detect_browser_cookie_sources
from services.cookie_probe_service import discover_cookie_databases, probe_site_cookie_browsers_detailed


logger = logging.getLogger("tube_player.cookie")


class CookieProbeSignals(QObject):
    # 成功时携带 ProbeReport：matches={site: browser_spec}，unreadable=读不到的浏览器
    success = Signal(object)
    error = Signal(str)
    finished = Signal()


class CookieProbeWorker(QRunnable):
    """启动时异步探测各站点的登录 Cookie 浏览器，不阻塞首屏。"""

    def __init__(self, sites: tuple[str, ...] = ("bilibili", "youtube", "douyin", "tiktok")) -> None:
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
            report = probe_site_cookie_browsers_detailed(self.sites, databases)
            logger.info(
                "cookie probe done matches=%s unreadable=%s",
                report.matches,
                report.unreadable,
            )
            self.signals.success.emit(report)
        except Exception as exc:  # noqa: BLE001
            logger.exception("cookie probe worker failed")
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()
