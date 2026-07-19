from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QTimer
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
                "color=c=black:s=320x180:d=2",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=stereo",
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-y",
                str(sample),
            ],
            check=True,
        )

        config = ConfigService(user_path=root / "user_config.json")
        widget = QWidget()
        widget.resize(640, 360)
        widget.show()
        player = MpvPlayer(widget, config)
        succeeded = {"value": False}

        def on_position(position: float) -> None:
            if position > 0.1:
                succeeded["value"] = True
                app.quit()

        player.position_changed.connect(on_position)
        player.load(str(sample))
        QTimer.singleShot(8000, app.quit)
        app.exec()
        player.shutdown()
        widget.close()
        return 0 if succeeded["value"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

