from __future__ import annotations

import logging
from logging import FileHandler
from pathlib import Path
from typing import Iterable

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


def sanitize_command(command: Iterable[str]) -> list[str]:
    sanitized: list[str] = []
    hide_next_for = {"--cookies", "--proxy"}
    for part in command:
        if sanitized and sanitized[-1] in hide_next_for:
            label = "<cookie-file>" if sanitized[-1] == "--cookies" else "<proxy>"
            sanitized.append(label)
        else:
            sanitized.append(str(part))
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
