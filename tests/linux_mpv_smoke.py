from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QWidget

from player.mpv_player import MpvPlayer
from services.config_service import ConfigService


def main() -> int:
    app = QApplication(sys.argv[:1])
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        sample = root / "smoke.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=320x180:d=5",
                "-c:v",
                "mpeg4",
                "-q:v",
                "5",
                "-pix_fmt",
                "yuv420p",
                "-an",
                "-y",
                str(sample),
            ],
            check=True,
        )

        config = ConfigService(user_path=root / "user_config.json")
        widget = QWidget()
        widget.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        widget.resize(640, 360)
        widget.show()
        native_window_id = int(widget.winId())
        app.processEvents()
        print(f"X11 smoke widget mapped wid={native_window_id}", flush=True)
        player = MpvPlayer(widget, config)
        succeeded = {"value": False}

        def on_position(position: float) -> None:
            if position > 0.1:
                succeeded["value"] = True
                app.quit()

        def on_timeout() -> None:
            print(
                "libmpv smoke timeout",
                {
                    "position": player.position(),
                    "duration": player.duration(),
                    "paused": player.get_bool("pause"),
                    "eof": player.get_bool("eof-reached"),
                    "idle": player.get_bool("core-idle"),
                    "sample_size": sample.stat().st_size,
                },
                flush=True,
            )
            app.quit()

        player.position_changed.connect(on_position)
        QTimer.singleShot(0, lambda: player.load(str(sample)))
        QTimer.singleShot(12000, on_timeout)
        app.exec()
        player.shutdown()
        widget.close()
        return 0 if succeeded["value"] else 1


if __name__ == "__main__":
    raise SystemExit(0 if "--check-imports" in sys.argv else main())
