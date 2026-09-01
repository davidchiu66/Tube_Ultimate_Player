from __future__ import annotations

import logging
from logging import FileHandler
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlparse

from app_paths import LOG_DIR


LOG_FORMAT = "%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    app_log = LOG_DIR / "app.log"
    ytdlp_log = LOG_DIR / "yt-dlp.log"
    for path in (app_log, ytdlp_log):
        path.write_text("", encoding="utf-8")

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    _remove_file_handlers(root)
    app_handler = _file_handler(app_log)
    app_handler.setLevel(logging.DEBUG)
    root.addHandler(app_handler)

    ytdlp_logger = logging.getLogger("tube_player.ytdlp")
    ytdlp_logger.setLevel(logging.DEBUG)
    ytdlp_logger.propagate = True
    _remove_file_handlers(ytdlp_logger)
    ytdlp_handler = _file_handler(ytdlp_log)
    ytdlp_handler.setLevel(logging.DEBUG)
    ytdlp_logger.addHandler(ytdlp_handler)


def install_qt_message_handler() -> None:
    """把 Qt 自己的分类日志（qt.network.http2 等）收进 logs/app.log。

    Qt 的消息不走 Python logging，默认直接打到 stderr，于是控制台会出现一堆
    看不出上下文、日志里又查不到的报错。转进来之后控制台干净，信息也不丢。
    """
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    qt_logger = logging.getLogger("tube_player.qt")
    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def handler(mode, context, message: str) -> None:
        category = str(getattr(context, "category", "") or "qt")
        qt_logger.log(levels.get(mode, logging.INFO), "[%s] %s", category, message)

    qInstallMessageHandler(handler)


def sanitize_command(command: Iterable[str]) -> list[str]:
    sanitized: list[str] = []
    hide_next_for = {"--cookies", "--proxy"}
    for part in command:
        if sanitized and sanitized[-1] in hide_next_for:
            label = "<cookie-file>" if sanitized[-1] == "--cookies" else "<proxy>"
            sanitized.append(label)
        else:
            value = str(part)
            parsed = urlparse(value)
            host = str(parsed.hostname or "").lower()
            query_names = {name.lower() for name, _value in parse_qsl(parsed.query, keep_blank_values=True)}
            if parsed.scheme in {"http", "https"} and host.endswith("xiaohongshu.com") and (
                "xsec_token" in query_names or "xsec_source" in query_names
            ):
                value = parsed._replace(query="<security-context>", fragment="").geturl()
                sanitized.append(value)
                continue
            if parsed.scheme in {"http", "https"} and host.endswith("xhscdn.com"):
                value = f"{parsed.scheme}://{parsed.netloc}/<media-url>"
                sanitized.append(value)
                continue
            if parsed.scheme in {"http", "https"} and (
                "/aweme/v1/play/" in parsed.path or str(parsed.hostname or "").endswith("douyinvod.com")
            ):
                value = f"{parsed.scheme}://{parsed.netloc}/<media-url>"
            sanitized.append(value)
    return sanitized


def _file_handler(path: Path) -> FileHandler:
    handler = FileHandler(path, mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    return handler


def _remove_file_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        if isinstance(handler, FileHandler):
            logger.removeHandler(handler)
            handler.close()
