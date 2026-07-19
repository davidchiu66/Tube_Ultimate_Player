from __future__ import annotations

import logging
import os
import sys

from platform_support import configure_qt_platform_environment, is_root_user, linux_session_type


configure_qt_platform_environment()

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from app_paths import APP_NAME, asset_path, ensure_runtime_dirs, resource_path
from services.logging_service import setup_logging
from ui.main_window import MainWindow


logger = logging.getLogger("tube_player.app")


def main() -> int:
    ensure_runtime_dirs()
    setup_logging()
    logger.info(
        "application starting platform=%s session=%s qpa=%s root=%s",
        sys.platform,
        linux_session_type(),
        os.environ.get("QT_QPA_PLATFORM", "auto"),
        is_root_user(),
    )
    if is_root_user():
        logger.warning(
            "application is running as root; desktop session, cookies, audio and created file ownership may be limited"
        )
    app = QApplication(sys.argv)
    logger.info("Qt platform backend=%s", app.platformName())
    app.setApplicationName(APP_NAME)
    _load_app_icon(app)
    _load_stylesheet(app)
    try:
        window = MainWindow()
    except Exception as exc:
        logger.exception("application startup failed")
        QMessageBox.critical(None, "启动失败", str(exc))
        return 1
    window.show()
    exit_code = app.exec()
    logger.info("application exited: %s", exit_code)
    return exit_code


def _load_stylesheet(app: QApplication) -> None:
    for name in ("dark_theme.qss", "dark.qss"):
        path = resource_path("qss", name)
        try:
            with path.open("r", encoding="utf-8") as file:
                app.setStyleSheet(file.read())
                return
        except OSError:
            continue


def _load_app_icon(app: QApplication) -> None:
    for path in (
        asset_path("icons", "app-icon.ico"),
        asset_path("icons", "app-icon-256.png"),
        asset_path("icons", "app-icon.png"),
    ):
        if path.exists():
            app.setWindowIcon(QIcon(str(path)))
            return


if __name__ == "__main__":
    raise SystemExit(main())
