from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HomeVideo:
    video_id: str
    title: str
    webpage_url: str
    uploader: str = ""
    duration: int = 0
    thumbnail: str = ""


@dataclass
class VideoQuality:
    label: str
    height: int
    width: int
    fps: int
    vcodec: str
    acodec: str
    ext: str
    format_id: str
    video_url: str
    audio_url: str | None = None
    audio_format_id: str | None = None
    audio_filesize: int | None = None
    audio_tbr: float | None = None
    filesize: int | None = None
    tbr: float | None = None


@dataclass
class SubtitleInfo:
    language: str
    ext: str
    url: str
    is_auto: bool = False

    @property
    def label(self) -> str:
        suffix = "自动" if self.is_auto else "字幕"
        return f"{self.language} ({suffix}, {self.ext})"


@dataclass
class VideoInfo:
    video_id: str
    title: str
    description: str = ""
    uploader: str = ""
    channel_id: str = ""
    duration: int = 0
    upload_date: str = ""
    webpage_url: str = ""
    thumbnail: str = ""
    qualities: dict[str, VideoQuality] = field(default_factory=dict)
    subtitles: dict[str, SubtitleInfo] = field(default_factory=dict)
    automatic_captions: dict[str, Any] = field(default_factory=dict)
    http_headers: dict[str, str] = field(default_factory=dict)
    raw_info: dict[str, Any] | None = None
