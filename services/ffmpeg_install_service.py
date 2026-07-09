from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from app_paths import UPDATE_DIR, runtime_path, thirdpart_path
from services.config_service import ConfigService


logger = logging.getLogger("tube_player.ffmpeg")

FFMPEG_DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.0.1-essentials_build.7z"
FFMPEG_ARCHIVE_NAME = "ffmpeg-8.0.1-essentials_build.7z"


@dataclass(slots=True)
class FfmpegInstallInfo:
    url: str
    archive_path: Path
    extract_dir: Path


class FfmpegInstallService:
    def __init__(self, config: ConfigService) -> None:
        self.config = config

    def is_available(self) -> bool:
        return bool(self.effective_ffmpeg_dir())

    def effective_ffmpeg_dir(self) -> str:
        configured = self.config.download_ffmpeg_location()
        if configured:
            found = _ffmpeg_dir_from_path(Path(configured))
            if found:
                return str(found)

        thirdpart_ffmpeg = _ffmpeg_dir_from_path(thirdpart_path("ffmpeg.exe"))
        if thirdpart_ffmpeg:
            return str(thirdpart_ffmpeg)

        system_ffmpeg = shutil.which("ffmpeg")
        if system_ffmpeg:
            return str(Path(system_ffmpeg).parent)

        installed = _ffmpeg_dir_from_path(self.extract_dir())
        if installed:
            return str(installed)

        return ""

    def install_info(self) -> FfmpegInstallInfo:
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        self.extract_dir().mkdir(parents=True, exist_ok=True)
        return FfmpegInstallInfo(
            url=FFMPEG_DOWNLOAD_URL,
            archive_path=UPDATE_DIR / FFMPEG_ARCHIVE_NAME,
            extract_dir=self.extract_dir(),
        )

    def extract_dir(self) -> Path:
        return runtime_path("ffmpeg")

    def locate_extracted_ffmpeg_dir(self) -> str:
        found = _ffmpeg_dir_from_path(self.extract_dir())
        return str(found) if found else ""


def _ffmpeg_dir_from_path(path: Path) -> Path | None:
    if path.is_file() and path.name.lower() in ("ffmpeg.exe", "ffmpeg"):
        return path.parent
    if path.is_dir():
        direct = path / "ffmpeg.exe"
        if direct.exists():
            return path
        direct = path / "ffmpeg"
        if direct.exists():
            return path
        try:
            for candidate in path.rglob("ffmpeg.exe"):
                if candidate.is_file():
                    return candidate.parent
            for candidate in path.rglob("ffmpeg"):
                if candidate.is_file():
                    return candidate.parent
        except OSError:
            return None
    return None
